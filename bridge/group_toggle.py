"""Bouton 6 de Veranda ou Cuisine : bascule de leur groupe natif Bose."""
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

VERANDA = os.environ.get('GROUP_VERANDA_HOST', '192.168.0.152')
CUISINE = os.environ.get('GROUP_CUISINE_HOST', '192.168.0.151')
MARKER_URL = 'http://bose-bridge.invalid/multiroom-toggle'


def marker():
    preset = ET.Element('preset', id='6')
    content = ET.SubElement(preset, 'ContentItem', source='LOCAL_INTERNET_RADIO',
                            type='stationurl', location=MARKER_URL,
                            sourceAccount='', isPresetable='true')
    ET.SubElement(content, 'itemName').text = 'Groupe Veranda + Cuisine'
    return preset


class GroupButton:
    def __init__(self, veranda=VERANDA, cuisine=CUISINE):
        if veranda == cuisine:
            raise ValueError('Les deux enceintes doivent être différentes')
        self.hosts = (veranda, cuisine)
        self.device_ids = {}
        self.last_playing = {host: None for host in self.hosts}
        self.group_master = None
        self.lock = threading.Lock()
        self.busy = False
        self.last_press = 0.0

    def other(self, host):
        if host not in self.hosts:
            raise ValueError('Enceinte hors du groupe')
        return self.hosts[1] if host == self.hosts[0] else self.hosts[0]

    def initialize(self):
        # Valider tous les presets avant la première écriture.
        before = {host: bose.slots(bose.request(host, 'presets')[1]) for host in self.hosts}
        expected = marker().find('ContentItem')
        for host in self.hosts:
            current = before[host].get('6')
            if current is not None and bose.signature(current) != bose.signature(expected):
                raise RuntimeError(f'Le preset 6 de {bose.speaker_name(host)} contient déjà autre chose')
        for host in self.hosts:
            if before[host].get('6') is not None:
                continue
            bose.request(host, 'storePreset', ET.tostring(marker()))
            after = bose.slots(bose.request(host, 'presets')[1])
            if any(bose.signature(before[host].get(str(n))) != bose.signature(after.get(str(n)))
                   for n in range(1, 6)):
                raise RuntimeError(f'Un preset radio a changé sur {bose.speaker_name(host)}')
            if bose.signature(after.get('6')) != bose.signature(expected):
                raise RuntimeError(f'Le repère 6 de {bose.speaker_name(host)} n’a pas été confirmé')
            print(f'[group] repère 6 configuré sur {bose.speaker_name(host)}', flush=True)
        self.device_ids = {host: bose.request(host, 'info')[1].get('deviceID')
                           for host in self.hosts}
        if not all(self.device_ids.values()) or len(set(self.device_ids.values())) != 2:
            raise RuntimeError('Identités des enceintes absentes ou dupliquées')
        for host in self.hosts:
            self.remember(host, bose.request(host, 'now_playing')[1])
        self.group_master = self.read_group_master()
        print('[group] boutons 6 prêts : Veranda et Cuisine', flush=True)

    def read_group_master(self):
        active_ids = {bose.request(host, 'getZone')[1].get('master') for host in self.hosts}
        active_ids.discard(None)
        if not active_ids:
            return None
        if len(active_ids) != 1:
            raise RuntimeError('Les enceintes appartiennent à des groupes différents')
        master_id = active_ids.pop()
        for host, device_id in self.device_ids.items():
            if master_id == device_id:
                return host
        raise RuntimeError('Une enceinte appartient à un autre groupe')

    def remember(self, host, playing):
        # Sur une enceinte secondaire, les notifications peuvent porter l'ID du maître.
        if playing.get('deviceID') not in (None, self.device_ids.get(host)):
            return
        source = playing.get('source')
        content = playing.find('ContentItem')
        if source == 'STANDBY':
            with self.lock:
                self.last_playing[host] = None
            return
        if (not source or source == 'INVALID_SOURCE' or content is None
                or content.get('location') == MARKER_URL
                or playing.findtext('playStatus') not in ('PLAY_STATE', 'PAUSE_STATE')):
            return
        with self.lock:
            self.last_playing[host] = copy.deepcopy(playing)

    def on_message(self, host, message):
        try:
            root = ET.fromstring(message)
        except ET.ParseError:
            return
        for playing in root.iter('nowPlaying'):
            self.remember(host, playing)
        selection = root.find('.//nowSelectionUpdated/preset')
        if selection is not None and selection.get('id') == '6':
            self.press(host)

    def press(self, host):
        self.other(host)
        with self.lock:
            now = time.monotonic()
            if self.busy or now - self.last_press < 0.8:
                print('[group] appui répété ignoré', flush=True)
                return
            self.busy = True
            self.last_press = now
            previous = copy.deepcopy(self.last_playing[host])
            cached_master = self.group_master
        print(f'[group] bouton 6 reçu sur {bose.speaker_name(host)}', flush=True)
        threading.Thread(target=self._run, args=(host, previous, cached_master), daemon=True).start()

    def restore_playback(self, host, previous):
        # Le repère local passe brièvement en INVALID_SOURCE.
        for _ in range(15):
            current = bose.request(host, 'now_playing')[1]
            if current.get('source') == 'INVALID_SOURCE':
                break
            time.sleep(0.1)
        if previous is None:
            print(f'[group] aucune lecture à reprendre sur {bose.speaker_name(host)}', flush=True)
            return
        content = previous.find('ContentItem')
        if previous.get('source') == 'UPNP':
            url = content.get('location')
            if not url or not url.startswith(('http://', 'https://')):
                raise RuntimeError('URL de la radio précédente indisponible')
            bose.soap(host, 'SetAVTransportURI', {
                'InstanceID': 0, 'CurrentURI': url, 'CurrentURIMetaData': ''})
        else:
            bose.request(host, 'select', ET.tostring(content))
        for _ in range(20):
            current = bose.request(host, 'now_playing')[1]
            active = current.find('ContentItem')
            if (current.get('source') == previous.get('source') and active is not None
                    and active.get('location') == content.get('location')
                    and current.findtext('playStatus') in ('PLAY_STATE', 'PAUSE_STATE')):
                if previous.findtext('playStatus') == 'PAUSE_STATE':
                    bose.request(host, 'key',
                                 b'<key state="release" sender="Gabbo">PAUSE</key>')
                return
            time.sleep(0.2)
        raise RuntimeError('Reprise de la lecture non confirmée')

    def _run(self, host, previous, cached_master):
        try:
            current_master = self.read_group_master()
            # Le firmware peut déjà séparer le groupe au moment où le secondaire
            # sélectionne son repère. Le dernier état connu évite de le recréer.
            prior_master = current_master or cached_master
            other = self.other(host)
            if prior_master:
                if host == prior_master:
                    self.restore_playback(host, previous)
                else:
                    # Le secondaire sort brièvement du groupe, puis le firmware
                    # le réintègre environ cinq secondes plus tard. Attendre ce
                    # retour avant d'envoyer la séparation définitive.
                    time.sleep(6)
                for _ in range(3):
                    bose.zone(prior_master, 'leave', (self.other(prior_master),))
                    time.sleep(1)
                    actual = self.read_group_master()
                    if actual is None:
                        break
                    if actual != prior_master:
                        raise RuntimeError('Le maître du groupe a changé pendant la séparation')
                else:
                    raise RuntimeError('La séparation ne tient pas après trois essais')
                action = 'leave'
            else:
                with self.lock:
                    other_playing = copy.deepcopy(self.last_playing[other])
                master = host if previous is not None or other_playing is None else other
                if master == host:
                    self.restore_playback(host, previous)
                bose.zone(master, 'join', (self.other(master),))
                action = 'join'
            actual = self.read_group_master()
            if (action == 'join' and actual is None) or (action == 'leave' and actual is not None):
                raise RuntimeError('État final du groupe non confirmé')
            with self.lock:
                self.group_master = actual
            print(f'[group] {"activé" if action == "join" else "désactivé"}'
                  f' ; maître={bose.speaker_name(actual) if actual else "aucun"}', flush=True)
        except Exception as exc:
            print(f'[group] erreur : {exc}', flush=True)
            try:
                actual = self.read_group_master()
                with self.lock:
                    self.group_master = actual
            except Exception:
                pass
        finally:
            with self.lock:
                self.busy = False

    def refresh_group(self):
        try:
            actual = self.read_group_master()
            with self.lock:
                if not self.busy:
                    self.group_master = actual
        except Exception as exc:
            print(f'[group] état du groupe indisponible : {exc}', flush=True)

    def listen(self, host):
        while True:
            try:
                ws = websocket.create_connection(f'ws://{host}:8080',
                                                 subprotocols=['gabbo'], timeout=10,
                                                 http_proxy_host=None)
                ws.settimeout(None)
                print(f'[group] WebSocket connecté : {bose.speaker_name(host)}', flush=True)
                self.remember(host, bose.request(host, 'now_playing')[1])
                self.refresh_group()
                try:
                    while True:
                        self.on_message(host, ws.recv())
                finally:
                    ws.close()
            except Exception as exc:
                print(f'[group] {bose.speaker_name(host)} : connexion perdue : {exc}'
                      ' ; nouvel essai dans 3 s', flush=True)
                time.sleep(3)

    def poll_group(self):
        while True:
            time.sleep(3)
            self.refresh_group()


def main():
    group = GroupButton()
    group.initialize()
    threading.Thread(target=group.poll_group, daemon=True).start()
    threads = [threading.Thread(target=group.listen, args=(host,), daemon=True)
               for host in group.hosts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


if __name__ == '__main__':
    main()
