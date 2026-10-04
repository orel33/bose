from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'bridge'))
import bose
from group_toggle import GroupButton, MARKER_URL


def playing(source='UPNP', location='http://radio.test/live.mp3', state='PLAY_STATE'):
    root = ET.Element('nowPlaying', source=source)
    ET.SubElement(root, 'ContentItem', source=source, location=location)
    ET.SubElement(root, 'playStatus').text = state
    return root


class GroupButtonTests(unittest.TestCase):
    def setUp(self):
        self.button = GroupButton('192.168.0.152', '192.168.0.151')
        self.presets = ET.fromstring('<presets>' + ''.join(
            f'<preset id="{n}"><ContentItem source="LOCAL_INTERNET_RADIO" location="radio{n}"/></preset>'
            for n in range(1, 6)) + '</presets>')
        self.writes = []

    def request(self, host, endpoint, body=None):
        if body is not None:
            self.writes.append((host, endpoint, ET.fromstring(body)))
            self.presets.append(ET.fromstring(body))
        return b'', playing() if endpoint == 'now_playing' else self.presets

    def test_initialize_writes_only_empty_six_and_preserves_radios(self):
        originals = [bose.signature(p) for p in self.presets]
        with patch.object(bose, 'request', side_effect=self.request):
            self.button.initialize()
            self.button.initialize()
        self.assertEqual([(host, endpoint, body.get('id')) for host, endpoint, body in self.writes],
                         [('192.168.0.152', 'storePreset', '6')])
        self.assertEqual([bose.signature(p) for p in list(self.presets)[:5]], originals)

    def test_existing_six_is_not_overwritten(self):
        self.presets.append(ET.fromstring('<preset id="6"><ContentItem source="DEEZER"/></preset>'))
        with patch.object(bose, 'request', side_effect=self.request), self.assertRaisesRegex(RuntimeError, 'déjà autre chose'):
            self.button.initialize()
        self.assertEqual(self.writes, [])

    def test_only_selection_six_triggers_group(self):
        with patch.object(self.button, 'press') as press:
            self.button.on_message('<updates><nowSelectionUpdated><preset id="5"/></nowSelectionUpdated></updates>')
            self.button.on_message('<userActivityUpdate/>')
            self.button.on_message('<updates><nowSelectionUpdated><preset id="6"/></nowSelectionUpdated></updates>')
        press.assert_called_once()

    def test_marker_does_not_replace_last_radio(self):
        original = playing()
        self.button.remember(original)
        self.button.remember(playing('INVALID_SOURCE', MARKER_URL))
        self.button.remember(playing('LOCAL_INTERNET_RADIO', MARKER_URL))
        self.assertEqual(bose.signature(self.button.last_playing), bose.signature(original))

    def test_radio_is_restored_before_zone_changes(self):
        order = []
        previous = playing()
        with patch.object(self.button, 'restore_playback', side_effect=lambda p: order.append('restore')), \
             patch.object(bose, 'zone', side_effect=lambda *args: order.append(args) or 'join'):
            self.button._run(previous)
        self.assertEqual(order, ['restore', ('192.168.0.152', 'toggle', ('192.168.0.151',))])

    def test_restore_failure_does_not_change_zone(self):
        with patch.object(self.button, 'restore_playback', side_effect=RuntimeError('offline')), \
             patch.object(bose, 'zone') as zone:
            self.button._run(playing())
        zone.assert_not_called()


class ZoneTests(unittest.TestCase):
    def setUp(self):
        self.master = '192.168.0.152'
        self.member = '192.168.0.151'
        self.ids = {self.master: 'MASTER', self.member: 'KITCHEN'}
        self.joined = False
        self.writes = []

    def request(self, host, endpoint, body=None):
        if endpoint == 'info':
            return b'', ET.Element('info', deviceID=self.ids[host])
        if body is not None:
            self.writes.append((host, endpoint, ET.fromstring(body)))
            self.joined = endpoint == 'setZone'
            return b'', ET.Element('status')
        root = ET.Element('zone')
        if self.joined:
            root.set('master', 'MASTER')
            for name in (self.master, self.member):
                ET.SubElement(root, 'member', ipaddress=name).text = self.ids[name]
        return b'', root

    def test_toggle_joins_only_pair_then_leaves(self):
        with patch.object(bose, 'request', side_effect=self.request), \
             patch.object(bose.time, 'sleep'), patch.object(bose.socket, 'socket') as connection:
            connection.return_value.__enter__.return_value.getsockname.return_value = ('192.168.0.100', 1234)
            self.assertEqual(bose.zone(self.master, 'toggle', (self.member,)), 'join')
            self.assertEqual(bose.zone(self.master, 'toggle', (self.member,)), 'leave')
        self.assertEqual([endpoint for _, endpoint, _ in self.writes], ['setZone', 'removeZoneSlave'])
        self.assertEqual({node.get('ipaddress') for node in self.writes[0][2].findall('member')},
                         {self.master, self.member})

    def test_offline_member_prevents_any_write(self):
        with patch.object(bose, 'request', side_effect=OSError('offline')), self.assertRaises(OSError):
            bose.zone(self.master, 'toggle', (self.member,))
        self.assertEqual(self.writes, [])


if __name__ == '__main__':
    unittest.main()
