# Bose Maison pour Android

Application locale pour les trois SoundTouch 10 du projet : Veranda, Cuisine et
Chambre. Les adresses IP sont préconfigurées dans `BoseClient.java`.

## Installer

Télécharger [l'APK signé depuis GitHub](https://github.com/orel33/bose/raw/refs/heads/main/android/build/bose-maison-1.2.apk), ou copier `build/bose-maison-1.2.apk` sur le téléphone, puis l'ouvrir. Autoriser l'installation depuis l'application utilisée pour ouvrir le fichier si Android le demande.

Le téléphone doit être connecté au Wi-Fi qui atteint les Bose (`192.168.0.0/24`). L'application parle directement à leurs API locales : elle ne dépend ni du bridge Docker ni du Raspberry Pi. Elle continue donc à fonctionner si le bridge est arrêté. Le bridge sert séparément aux boutons physiques des enceintes.

L'APK demande Android 8.0 minimum, cible Android 16 (API 36) et a été vérifié
sur Android 17.

L'application propose les cinq radios de `config/radios.env`, lecture/pause,
arrêt, veille, volume, sourdine, groupe Veranda + Cuisine et redémarrage de
l'enceinte sélectionnée. Le redémarrage demande une confirmation et utilise la
console Bose sur le port TCP 17000.

## Reconstruire l'APK

Depuis ce dossier, lancer `bash build.sh`. Le script utilise le SDK Android
installé dans `/usr/lib/android-sdk`, ou `ANDROID_HOME` s'il est défini. Il
génère un APK signé nommé `build/bose-maison-<version>.apk` (actuellement
`bose-maison-1.2.apk`). Le numéro est lu automatiquement dans le champ
`versionName` d'`AndroidManifest.xml`. La clé de signature propre à
cette application est créée dans `.signing/` ; conserver ce dossier pour pouvoir
installer les versions suivantes comme mises à jour.

L'application utilise directement l'[API locale SoundTouch publiée par Bose](https://assets.bosecreative.com/m/496577402d128874/original/SoundTouch-Web-API.pdf).
