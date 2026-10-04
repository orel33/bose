"""Bouton 6 de Veranda : bascule du groupe natif Veranda + Cuisine."""
import copy
import os
from pathlib import Path
import sys
import threading
import time
import xml.etree.ElementTree as ET

import websocket

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import bose

MASTER = os.environ.get('GROUP_MASTER_HOST', '192.168.0.152')
MEMBER = os.environ.get('GROUP_MEMBER_HOST', '192.168.0.151')
MARKER_URL = 'http://bose-bridge.invalid/multiroom-toggle'


def marker():
    preset = ET.Element('preset', id='6')
    content = ET.SubElement(preset, 'ContentItem', source='LOCAL_INTERNET_RADIO',
                            type='stationurl', location=MARKER_URL,
                            sourceAccount='', isPresetable='true')
    ET.SubElement(content, 'itemName').text = 'Groupe Veranda + Cuisine'
    return preset


class GroupButton:
    def __init__(self, master=MASTER, member=MEMBER):
        if master == member:
            raise ValueError('Le maître et le membre doivent être différents')
        self.master = master
        self.member = member
        self.last_playing = None
        self.lock = threading.Lock()
        self.busy = False
        self.last_press = 0.0

    def initialize(self):
        before = bose.slots(bose.request(self.master, 'presets')[1])
        expected = marker().find('ContentItem')
        current = before.get('6')
        if current is not None and bose.signature(current) != bose.signature(expected):
            raise RuntimeError('Le preset 6 contient déjà autre chose ; aucune écriture')
        if current is None:
            bose.request(self.master, 'storePreset', ET.tostring(marker()))
            after = bose.slots(bose.request(self.master, 'presets')[1])
            if any(bose.signature(before.get(str(n))) != bose.signature(after.get(str(n)))
                   for n in range(1, 6)):
                raise RuntimeError('Un preset radio 1–5 a changé pendant la configuration de 6')
            if bose.signature(after.get('6')) != bose.signature(expected):
                raise RuntimeError('Le repère du bouton 6 n’a pas été confirmé')
            print(f'[group] repère 6 configuré sur {self.master}', flush=True)
        self.remember(bose.request(self.master, 'now_playing')[1])
        print(f'[group] bouton 6 prêt : {self.master} + {self.member}', flush=True)

    def remember(self, playing):
        source = playing.get('source')
        content = playing.find('ContentItem')
        if source == 'STANDBY':
            with self.lock:
                self.last_playing = None
            return
        if (not source or source == 'INVALID_SOURCE' or content is None
                or content.get('location') == MARKER_URL
                or playing.findtext('playStatus') not in ('PLAY_STATE', 'PAUSE_STATE')):
            return
        with self.lock:
            self.last_playing = copy.deepcopy(playing)

    def on_message(self, message):
        try:
            root = ET.fromstring(message)
        except ET.ParseError:
            return
        for playing in root.iter('nowPlaying'):
            self.remember(playing)
        selection = root.find('.//nowSelectionUpdated/preset')
        if selection is not None and selection.get('id') == '6':
            self.press()

    def press(self):
        with self.lock:
            now = time.monotonic()
            if self.busy or now - self.last_press < 0.8:
                print('[group] appui répété ignoré', flush=True)
                return
            self.busy = True
            self.last_press = now
            previous = copy.deepcopy(self.last_playing)
        print('[group] bouton 6 reçu', flush=True)
        threading.Thread(target=self._run, args=(previous,), daemon=True).start()

    def restore_playback(self, previous):
        # Le repère local passe brièvement en INVALID_SOURCE.
        for _ in range(15):
            current = bose.request(self.master, 'now_playing')[1]
            if current.get('source') == 'INVALID_SOURCE':
                break
            time.sleep(0.1)
        if previous is None:
            print('[group] aucune lecture à reprendre', flush=True)
            return
        content = previous.find('ContentItem')
        if previous.get('source') == 'UPNP':
            url = content.get('location')
            if not url or not url.startswith(('http://', 'https://')):
                raise RuntimeError('URL de la radio précédente indisponible')
            bose.soap(self.master, 'SetAVTransportURI', {
                'InstanceID': 0, 'CurrentURI': url, 'CurrentURIMetaData': ''})
        else:
            bose.request(self.master, 'select', ET.tostring(content))
        for _ in range(20):
            current = bose.request(self.master, 'now_playing')[1]
            active = current.find('ContentItem')
            if (current.get('source') == previous.get('source') and active is not None
                    and active.get('location') == content.get('location')
                    and current.findtext('playStatus') in ('PLAY_STATE', 'PAUSE_STATE')):
                if previous.findtext('playStatus') == 'PAUSE_STATE':
                    bose.request(self.master, 'key',
                                 b'<key state="release" sender="Gabbo">PAUSE</key>')
                return
            time.sleep(0.2)
        raise RuntimeError('Reprise de la lecture non confirmée')

    def _run(self, previous):
        try:
            self.restore_playback(previous)
            action = bose.zone(self.master, 'toggle', (self.member,))
            print(f'[group] {"activé" if action == "join" else "désactivé"}', flush=True)
        except Exception as exc:
            print(f'[group] erreur : {exc}', flush=True)
        finally:
            with self.lock:
                self.busy = False


def main():
    group = GroupButton()
    group.initialize()
    while True:
        try:
            ws = websocket.create_connection(f'ws://{group.master}:8080',
                                             subprotocols=['gabbo'], timeout=10,
                                             http_proxy_host=None)
            ws.settimeout(None)
            print('[group] WebSocket connecté', flush=True)
            group.remember(bose.request(group.master, 'now_playing')[1])
            try:
                while True:
                    group.on_message(ws.recv())
            finally:
                ws.close()
        except KeyboardInterrupt:
            return
        except Exception as exc:
            print(f'[group] connexion perdue : {exc} ; nouvel essai dans 3 s', flush=True)
            time.sleep(3)


if __name__ == '__main__':
    main()
