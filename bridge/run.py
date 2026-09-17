"""Adaptateur local du bridge amont 1.8.5, fourni par l'image Docker figée."""
import os
from pathlib import Path
import sys

sys.path.insert(0, '/')  # Le package amont est installé dans /bose_bridge.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from bose_bridge import bridge as upstream
from bose_bridge.config import load_options
from bose_bridge.helpers import _parse_ws_preset_id
from group_toggle import GroupButton
import bose


def main():
    config = load_options()
    hosts = list(dict.fromkeys([s['host'] for s in config.get('speakers', []) if s.get('host')]))
    if config.get('bose_host') and config['bose_host'] not in hosts:
        hosts.append(config['bose_host'])
    master = os.environ['GROUP_MASTER_HOST']
    if master not in hosts or len(hosts) < 2:
        raise ValueError('Le maître et les membres du groupe doivent être configurés dans SPEAKERS_JSON')
    group = GroupButton(master, hosts)
    # Échec explicite si le maître est indisponible ; Docker pourra réessayer.
    group.initialize()

    class LocalSpeakerBridge(upstream.SpeakerBridge):
        def _on_message(self, ws, message):
            if self.host == master:
                group.observe(message)
                if _parse_ws_preset_id(message) == 6:
                    print(f'[group] bouton 6 reçu sur {master}', flush=True)
                    group.press()
                    return
            super()._on_message(ws, message)

        def _on_open(self, ws):
            super()._on_open(ws)
            if self.host == master:
                # Actualiser le contenu à reprendre après une reconnexion.
                try:
                    group.remember(bose.request(master, 'now_playing')[1])
                except Exception as exc:
                    print(f'[group] état de lecture indisponible : {exc}', flush=True)

    upstream.SpeakerBridge = LocalSpeakerBridge
    upstream.main()


if __name__ == '__main__':
    main()
