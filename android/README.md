# Bose Maison pour Android

Application locale pour les trois SoundTouch 10 du projet : Veranda, Cuisine et
Chambre. Les adresses IP sont préconfigurées dans `BoseClient.java`.

## Installer

Copier `build/bose-maison.apk` sur le téléphone puis l'ouvrir. Autoriser
l'installation depuis l'application utilisée pour ouvrir le fichier si Android
le demande. Le téléphone doit être connecté au Wi-Fi qui atteint les Bose
(`192.168.0.0/24`). Aucun compte ni Raspberry Pi n'est requis pour les commandes
de l'application.

L'application propose les cinq radios de `config/radios.env`, lecture/pause,
arrêt, veille, volume, sourdine, groupe Veranda + Cuisine et redémarrage de
l'enceinte sélectionnée. Le redémarrage demande une confirmation et utilise la
console Bose sur le port TCP 17000.

## Reconstruire l'APK

Depuis ce dossier, lancer `bash build.sh`. Le script utilise le SDK Android
installé dans `/usr/lib/android-sdk`, ou `ANDROID_HOME` s'il est défini. Il
génère un APK signé dans `build/bose-maison.apk`. La clé de signature propre à
cette application est créée dans `.signing/` ; conserver ce dossier pour pouvoir
installer les versions suivantes comme mises à jour.

L'application utilise directement l'[API locale SoundTouch publiée par Bose](https://assets.bosecreative.com/m/496577402d128874/original/SoundTouch-Web-API.pdf).
