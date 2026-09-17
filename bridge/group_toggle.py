"""Bouton 6 du maître : bascule du groupe Bose, sans commande shell."""
import copy
import threading
import time
import xml.etree.ElementTree as ET

import bose

MARKER_URL = 'http://bose-bridge.invalid/multiroom-toggle'


def marker():
    preset = ET.Element('preset', id='6')
    content = ET.SubElement(preset, 'ContentItem', source='LOCAL_INTERNET_RADIO',
                            type='stationurl', location=MARKER_URL,
                            sourceAccount='', isPresetable='true')
    ET.SubElement(content, 'itemName').text = 'Groupe Bose (bascule)'
    return preset


class GroupButton:
    def __init__(self, host, hosts):
        self.host = host
        self.hosts = tuple(hosts)
        self.last_playing = None
        self.lock = threading.Lock()
        self.busy = False
        self.pending = 0

    def initialize(self):
        self.remember(bose.request(self.host, 'now_playing')[1])
        before = bose.slots(bose.request(self.host, 'presets')[1])
        expected = marker().find('ContentItem')
        current = before.get('6')
        if bose.signature(current) != bose.signature(expected):
            bose.request(self.host, 'storePreset', ET.tostring(marker()))
            after = bose.slots(bose.request(self.host, 'presets')[1])
            if any(bose.signature(before.get(str(n))) != bose.signature(after.get(str(n)))
                   for n in range(1, 6)):
                raise RuntimeError('Un preset 1–5 a changé pendant la configuration de 6')
            if bose.signature(after.get('6')) != bose.signature(expected):
                raise RuntimeError('Repère du bouton 6 non confirmé')
            print(f'[group] {self.host}: repère 6 configuré', flush=True)
        print(f'[group] {self.host}: bouton 6 prêt ; groupe inchangé au démarrage', flush=True)

    def remember(self, playing):
        source = playing.get('source')
        content = playing.find('ContentItem')
        if source == 'STANDBY':
            with self.lock:
                self.last_playing = None
            return
        if (not source or source == 'INVALID_SOURCE' or content is None
                or content.get('location') == MARKER_URL
                or playing.findtext('playStatus') not in ('PLAY_STATE', 'PAUSE_STATE', 'STOP_STATE')):
            return
        with self.lock:
            self.last_playing = copy.deepcopy(playing)

    def observe(self, message):
        try:
            root = ET.fromstring(message)
        except ET.ParseError:
            return
        for node in root.iter('nowPlaying'):
            self.remember(node)

    def press(self):
        with self.lock:
            if self.busy:
                self.pending += 1
                print('[group] appui mis en attente', flush=True)
                return
            self.busy = True
            previous = copy.deepcopy(self.last_playing)
        threading.Thread(target=self._run, args=(previous,), daemon=True).start()

    def restore_playback(self, previous):
        # La sélection native du repère met la source en INVALID_SOURCE.
        # Attendre cette transition avant de rétablir le contenu précédent.
        for _ in range(15):
            current = bose.request(self.host, 'now_playing')[1]
            if current.get('source') == 'INVALID_SOURCE':
                break
            time.sleep(0.1)
        if previous is None:
            print('[group] aucune lecture précédente ; choisir ensuite un bouton 1/2/3', flush=True)
            return
        content = previous.find('ContentItem')
        if previous.get('source') == 'UPNP':
            url = content.get('location')
            if not url or not url.startswith(('http://', 'https://')):
                raise RuntimeError('URL de la lecture précédente indisponible')
            # Vider la lecture précédente avant de reprendre le flux courant.
            # SetAVTransportURI seul ne garantit pas le renouvellement audio.
            bose.soap(self.host, 'Stop', {'InstanceID': 0})
            bose.soap(self.host, 'SetAVTransportURI', {
                'InstanceID': 0, 'CurrentURI': url, 'CurrentURIMetaData': ''})
            bose.soap(self.host, 'Play', {'InstanceID': 0, 'Speed': '1'})
        else:
            bose.request(self.host, 'select', ET.tostring(content))
        for _ in range(20):
            current = bose.request(self.host, 'now_playing')[1]
            active = current.find('ContentItem')
            if (current.get('source') == previous.get('source') and active is not None
                    and active.get('location') == content.get('location')
                    and current.findtext('playStatus') in ('PLAY_STATE', 'PAUSE_STATE')):
                if previous.findtext('playStatus') == 'PAUSE_STATE':
                    bose.request(self.host, 'key', b'<key state="release" sender="Gabbo">PAUSE</key>')
                return
            time.sleep(0.2)
        raise RuntimeError('Reprise de la lecture non confirmée')

    def _run(self, previous):
        while True:
            try:
                self.restore_playback(previous)
                action = bose.zone(self.host, 'toggle', self.hosts)
                print(f'[group] {"activé" if action == "join" else "désactivé"}', flush=True)
            except Exception as exc:
                print(f'[group] erreur : {exc}', flush=True)
            with self.lock:
                if self.pending:
                    self.pending -= 1
                    previous = copy.deepcopy(self.last_playing)
                else:
                    self.busy = False
                    return
