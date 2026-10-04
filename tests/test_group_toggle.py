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
        self.veranda = bose.SPEAKERS['veranda']
        self.cuisine = bose.SPEAKERS['cuisine']
        self.button = GroupButton(self.veranda, self.cuisine)
        self.ids = {self.veranda: 'VERANDA', self.cuisine: 'CUISINE'}
        self.button.device_ids = self.ids
        self.presets = {host: ET.fromstring('<presets>' + ''.join(
            f'<preset id="{n}"><ContentItem source="LOCAL_INTERNET_RADIO" location="radio{n}"/></preset>'
            for n in range(1, 6)) + '</presets>') for host in self.button.hosts}
        self.writes = []

    def request(self, host, endpoint, body=None):
        if endpoint == 'info':
            return b'', ET.Element('info', deviceID=self.ids[host])
        if endpoint == 'getZone':
            return b'', ET.Element('zone')
        if endpoint == 'now_playing':
            result = playing()
            result.set('deviceID', self.ids[host])
            return b'', result
        if body is not None:
            self.writes.append((host, endpoint, ET.fromstring(body)))
            self.presets[host].append(ET.fromstring(body))
        return b'', self.presets[host]

    def test_initialize_writes_only_six_on_both_speakers(self):
        originals = {host: [bose.signature(p) for p in root] for host, root in self.presets.items()}
        with patch.object(bose, 'request', side_effect=self.request):
            self.button.initialize()
            self.button.initialize()
        self.assertEqual([(host, endpoint, body.get('id')) for host, endpoint, body in self.writes],
                         [(self.veranda, 'storePreset', '6'), (self.cuisine, 'storePreset', '6')])
        for host in self.button.hosts:
            self.assertEqual([bose.signature(p) for p in list(self.presets[host])[:5]], originals[host])

    def test_existing_six_on_cuisine_blocks_all_writes(self):
        self.presets[self.cuisine].append(
            ET.fromstring('<preset id="6"><ContentItem source="DEEZER"/></preset>'))
        with patch.object(bose, 'request', side_effect=self.request), \
             self.assertRaisesRegex(RuntimeError, 'Cuisine contient déjà autre chose'):
            self.button.initialize()
        self.assertEqual(self.writes, [])

    def test_only_selection_six_triggers_from_either_speaker(self):
        with patch.object(self.button, 'press') as press:
            self.button.on_message(self.cuisine, '<updates><nowSelectionUpdated><preset id="5"/></nowSelectionUpdated></updates>')
            self.button.on_message(self.veranda, '<userActivityUpdate/>')
            self.button.on_message(self.cuisine, '<updates><nowSelectionUpdated><preset id="6"/></nowSelectionUpdated></updates>')
            self.button.on_message(self.veranda, '<updates><nowSelectionUpdated><preset id="6"/></nowSelectionUpdated></updates>')
        self.assertEqual([call.args[0] for call in press.call_args_list], [self.cuisine, self.veranda])

    def test_marker_and_grouped_slave_audio_do_not_replace_own_radio(self):
        original = playing()
        self.button.remember(self.cuisine, original)
        self.button.remember(self.cuisine, playing('INVALID_SOURCE', MARKER_URL))
        grouped = playing()
        grouped.set('deviceID', self.ids[self.veranda])
        self.button.remember(self.cuisine, grouped)
        self.assertEqual(bose.signature(self.button.last_playing[self.cuisine]), bose.signature(original))

    def test_slave_press_does_not_rejoin_after_native_separation(self):
        with patch.object(self.button, 'read_group_master', side_effect=[None, None, None]), \
             patch.object(self.button, 'restore_playback') as restore, \
             patch.object(bose.time, 'sleep') as sleep, \
             patch.object(bose, 'zone', return_value='leave') as zone:
            self.button._run(self.cuisine, None, self.veranda)
        restore.assert_not_called()
        sleep.assert_any_call(6)
        zone.assert_called_once_with(self.veranda, 'leave', (self.cuisine,))

    def test_slave_reintegration_is_removed_again(self):
        with patch.object(self.button, 'read_group_master',
                          side_effect=[None, self.veranda, None, None]), \
             patch.object(bose.time, 'sleep'), \
             patch.object(bose, 'zone', return_value='leave') as zone:
            self.button._run(self.cuisine, None, self.veranda)
        self.assertEqual(zone.call_count, 2)

    def test_cuisine_press_uses_its_own_radio_to_form_group(self):
        order = []
        previous = playing()
        with patch.object(self.button, 'read_group_master', side_effect=[None, self.cuisine]), \
             patch.object(self.button, 'restore_playback', side_effect=lambda *args: order.append(('restore', args[0]))), \
             patch.object(bose, 'zone', side_effect=lambda *args: order.append(('zone', args)) or 'join'):
            self.button._run(self.cuisine, previous, None)
        self.assertEqual(order, [('restore', self.cuisine),
                                 ('zone', (self.cuisine, 'join', (self.veranda,)))])

    def test_standby_cuisine_joins_playing_veranda(self):
        self.button.last_playing[self.veranda] = playing()
        with patch.object(self.button, 'read_group_master', side_effect=[None, self.veranda]), \
             patch.object(self.button, 'restore_playback') as restore, \
             patch.object(bose, 'zone', return_value='join') as zone:
            self.button._run(self.cuisine, None, None)
        restore.assert_not_called()
        zone.assert_called_once_with(self.veranda, 'join', (self.cuisine,))

    def test_master_playback_is_restored_before_separation(self):
        order = []
        with patch.object(self.button, 'read_group_master', side_effect=[self.veranda, None, None]), \
             patch.object(self.button, 'restore_playback', side_effect=lambda *args: order.append('restore')), \
             patch.object(bose.time, 'sleep'), \
             patch.object(bose, 'zone', side_effect=lambda *args: order.append('leave') or 'leave'):
            self.button._run(self.veranda, playing(), self.veranda)
        self.assertEqual(order, ['restore', 'leave'])

    def test_restore_failure_does_not_change_zone(self):
        with patch.object(self.button, 'read_group_master', side_effect=[None, None]), \
             patch.object(self.button, 'restore_playback', side_effect=RuntimeError('offline')), \
             patch.object(bose, 'zone') as zone:
            self.button._run(self.cuisine, playing(), None)
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
