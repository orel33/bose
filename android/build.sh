#!/usr/bin/env bash
set -euo pipefail

app_dir=$(cd "$(dirname "$0")" && pwd)
sdk_dir=${ANDROID_HOME:-/usr/lib/android-sdk}
build_tools="$sdk_dir/build-tools/28.0.2"
android_jar="$sdk_dir/platforms/android-26/android.jar"
build_dir="$app_dir/build"
signing_dir="$app_dir/.signing"
app_version=$(python3 - "$app_dir/AndroidManifest.xml" <<'PY'
import re
import sys
import xml.etree.ElementTree as ET

version = ET.parse(sys.argv[1]).getroot().get(
    "{http://schemas.android.com/apk/res/android}versionName", ""
)
if not re.fullmatch(r"[0-9][A-Za-z0-9._-]*", version):
    sys.exit("versionName absent ou invalide dans AndroidManifest.xml")
print(version)
PY
)
apk_file="$build_dir/bose-maison-$app_version.apk"

for tool in aapt dx zipalign apksigner; do
  if [[ ! -x "$build_tools/$tool" ]]; then
    echo "Outil Android absent : $build_tools/$tool" >&2
    exit 1
  fi
done
if [[ ! -f "$android_jar" ]]; then
  echo "Plateforme Android absente : $android_jar" >&2
  exit 1
fi

mkdir -p "$build_dir/assets" "$build_dir/classes" "$build_dir/dex" "$signing_dir"
cp "$app_dir/../config/radios.env" "$build_dir/assets/radios.env"
"$build_tools/aapt" package -f \
  -M "$app_dir/AndroidManifest.xml" \
  -S "$app_dir/res" \
  -A "$build_dir/assets" \
  -I "$android_jar" \
  -F "$build_dir/unsigned.apk"

find "$app_dir/src" -name '*.java' -print0 | \
  xargs -0 javac --release 8 -cp "$android_jar" -d "$build_dir/classes"
jar cf "$build_dir/classes.jar" -C "$build_dir/classes" .
"$build_tools/dx" --dex --min-sdk-version=26 \
  --output="$build_dir/dex/classes.dex" "$build_dir/classes.jar"
(cd "$build_dir/dex" && "$build_tools/aapt" add "$build_dir/unsigned.apk" classes.dex)
"$build_tools/zipalign" -f 4 "$build_dir/unsigned.apk" "$build_dir/aligned.apk"

key_file="$signing_dir/bose-maison.jks"
password_file="$signing_dir/password"
if [[ ! -f "$key_file" ]]; then
  python3 -c 'import secrets; print(secrets.token_urlsafe(36), end="")' > "$password_file"
  chmod 600 "$password_file"
  keytool -genkeypair -noprompt -storetype PKCS12 \
    -keystore "$key_file" -alias bose-maison -keyalg RSA -keysize 3072 \
    -validity 10000 -dname 'CN=Bose Maison' \
    -storepass "$(cat "$password_file")" -keypass "$(cat "$password_file")"
  chmod 600 "$key_file"
fi
if [[ ! -f "$password_file" ]]; then
  echo "Mot de passe de signature absent : $password_file" >&2
  exit 1
fi

export BOSE_SIGNING_PASSWORD
BOSE_SIGNING_PASSWORD=$(cat "$password_file")
"$build_tools/apksigner" sign --ks "$key_file" \
  --ks-key-alias bose-maison \
  --ks-pass env:BOSE_SIGNING_PASSWORD \
  --key-pass env:BOSE_SIGNING_PASSWORD \
  --out "$apk_file" "$build_dir/aligned.apk"
unset BOSE_SIGNING_PASSWORD
"$build_tools/apksigner" verify --verbose "$apk_file"
echo "APK prêt : $apk_file"
