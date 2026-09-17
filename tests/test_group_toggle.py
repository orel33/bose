from pathlib import Path
import sys
import unittest
from unittest.mock import call, patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'bridge'))
import bose
from group_toggle import GroupButton, marker, MARKER_URL


def playing(source='DEEZER', location='1227352431', state='PLAY_STATE'):
    root = ET.Element('nowPlaying', source=source)
    ET.SubElement(root, 'ContentItem', source=source, location=location, sourceAccount='test')
    ET.SubElement(root, 'playStatus').text = state
    return root


class GroupButtonTests(unittest.TestCase):
    def setUp(self):
        self.button = GroupButton(bose.HOSTS[0], bose.HOSTS)
        self.presets = ET.fromstring('<presets>' + ''.join(
            f'<preset id="{n}"><ContentItem source="DEEZER" location="{n}"/></preset>'
            for n in (1, 2, 3)) + '</presets>')
        self.writes = []

    def request(self, host, endpoint, body=None):
        if body is not None:
            self.writes.append((host, endpoint, ET.fromstring(body)))
            self.presets.append(ET.fromstring(body))
        result = playing() if endpoint == 'now_playing' else self.presets
        return b'', result

    def test_startup_only_configures_six_on_master_and_is_idempotent(self):
        old = [bose.signature(p) for p in self.presets]
        with patch.object(bose, 'request', side_effect=self.request):
            self.button.initialize()
            self.button.initialize()
        self.assertEqual(len(self.writes), 1)
        host, endpoint, body = self.writes[0]
        self.assertEqual((host, endpoint, body.get('id')), (bose.HOSTS[0], 'storePreset', '6'))
        self.assertEqual([bose.signature(p) for p in list(self.presets)[:3]], old)

    def test_invalid_source_and_marker_do_not_replace_last_music(self):
        original = playing()
        self.button.remember(original)
        self.button.observe(ET.tostring(playing('INVALID_SOURCE', MARKER_URL)))
        self.button.observe(ET.tostring(playing('LOCAL_INTERNET_RADIO', MARKER_URL)))
        self.button.observe('<broken')
        self.assertEqual(bose.signature(self.button.last_playing), bose.signature(original))

    def test_standby_clears_old_music(self):
        self.button.remember(playing())
        self.button.remember(ET.Element('nowPlaying', source='STANDBY'))
        self.assertIsNone(self.button.last_playing)

    def test_deezer_restored_before_group_toggle(self):
        order = []
        with patch.object(self.button, 'restore_playback', side_effect=lambda p: order.append('restore')), \
             patch.object(bose, 'zone', side_effect=lambda *args: order.append(args) or 'join'):
            self.button._run(playing())
        self.assertEqual(order, ['restore', (bose.HOSTS[0], 'toggle', bose.HOSTS)])
        self.assertFalse(self.button.busy)

    def test_restore_failure_does_not_change_group(self):
        with patch.object(self.button, 'restore_playback', side_effect=RuntimeError('offline')), \
             patch.object(bose, 'zone') as zone:
            self.button._run(playing())
        zone.assert_not_called()
        self.assertFalse(self.button.busy)

    def test_quick_second_press_is_serialized(self):
        self.button.remember(playing())
        self.button.busy = True
        self.button.press()
        with patch.object(self.button, 'restore_playback') as restore, \
             patch.object(bose, 'zone', side_effect=['join', 'leave']) as zone:
            self.button._run(playing())
        self.assertEqual(restore.call_count, 2)
        self.assertEqual(zone.call_count, 2)
        self.assertFalse(self.button.busy)

    def test_radio_restored_via_upnp(self):
        previous = playing('UPNP', 'http://radio.test/live.mp3')
        with patch.object(bose, 'request', side_effect=[(b'', playing('INVALID_SOURCE')), (b'', previous)]), \
             patch.object(bose, 'soap') as soap:
            self.button.restore_playback(previous)
        self.assertEqual(soap.call_args_list, [
            call(bose.HOSTS[0], 'Stop', {'InstanceID': 0}),
            call(bose.HOSTS[0], 'SetAVTransportURI', {
                'InstanceID': 0, 'CurrentURI': 'http://radio.test/live.mp3',
                'CurrentURIMetaData': ''}),
            call(bose.HOSTS[0], 'Play', {'InstanceID': 0, 'Speed': '1'}),
        ])

    def test_paused_radio_is_repaused_after_restart(self):
        previous = playing('UPNP', 'http://radio.test/live.mp3', 'PAUSE_STATE')
        resumed = playing('UPNP', 'http://radio.test/live.mp3')
        with patch.object(bose, 'request', side_effect=[
                (b'', playing('INVALID_SOURCE')), (b'', resumed),
                (b'', ET.Element('status'))]) as request, \
             patch.object(bose, 'soap'):
            self.button.restore_playback(previous)
        request.assert_called_with(bose.HOSTS[0], 'key',
            b'<key state="release" sender="Gabbo">PAUSE</key>')

    def test_deezer_restored_via_native_select(self):
        previous = playing()
        with patch.object(bose, 'request', side_effect=[(b'', playing('INVALID_SOURCE')), (b'', ET.Element('status')), (b'', previous)]) as request:
            self.button.restore_playback(previous)
        selection = request.call_args_list[1].args
        self.assertEqual(selection[:2], (bose.HOSTS[0], 'select'))
        self.assertEqual(ET.fromstring(selection[2]).get('location'), '1227352431')


class ZoneToggleTests(unittest.TestCase):
    def setUp(self):
        self.hosts = ('192.168.0.152', '192.168.0.151', '192.168.0.111')
        self.ids = dict(zip(self.hosts, ('MASTER', 'KITCHEN', 'BEDROOM')))
        self.members = set()
        self.mutations = []

    def request(self, host, endpoint, body=None):
        if endpoint == 'info':
            return b'', ET.Element('info', deviceID=self.ids[host])
        if body is not None:
            root = ET.fromstring(body)
            self.mutations.append((host, endpoint, root))
            if endpoint == 'setZone':
                self.members = {m.get('ipaddress') for m in root}
            else:
                # Reproduire le firmware : seul le premier membre est retiré.
                self.members.discard(root.find('member').get('ipaddress'))
                if self.members == {self.hosts[0]}:
                    self.members.clear()
            return b'', ET.Element('status')
        root = ET.Element('zone')
        if host in self.members:
            root.set('master', 'MASTER')
            for h in self.hosts:
                if h not in self.members:
                    continue
                ET.SubElement(root, 'member', ipaddress=h).text = self.ids[h]
        return b'', root

    def test_toggle_uses_actual_state_each_time(self):
        with patch.object(bose, 'request', side_effect=self.request), \
             patch.object(bose.time, 'sleep'), patch.object(bose.socket, 'socket') as socket:
            socket.return_value.__enter__.return_value.getsockname.return_value = ('192.168.0.100', 1234)
            self.assertEqual(bose.zone(self.hosts[0], 'toggle', self.hosts), 'join')
            self.assertEqual(bose.zone(self.hosts[0], 'toggle', self.hosts), 'leave')
            self.assertEqual(bose.zone(self.hosts[0], 'toggle', self.hosts), 'join')
        self.assertEqual([m[1] for m in self.mutations], ['setZone', 'removeZoneSlave', 'removeZoneSlave', 'setZone'])
        self.assertEqual([m[2].find('member').get('ipaddress') for m in self.mutations[1:3]], list(self.hosts[1:]))

    def test_partial_group_removes_only_remaining_member(self):
        self.members = {self.hosts[0], self.hosts[2]}
        with patch.object(bose, 'request', side_effect=self.request), patch.object(bose.time, 'sleep'):
            self.assertEqual(bose.zone(self.hosts[0], 'toggle', self.hosts), 'leave')
        self.assertEqual(len(self.mutations), 1)
        self.assertEqual(self.mutations[0][2].find('member').get('ipaddress'), self.hosts[2])

    def test_unreachable_member_prevents_partial_group_write(self):
        with patch.object(bose, 'request', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                bose.zone(self.hosts[0], 'toggle', self.hosts)
        self.assertEqual(self.mutations, [])


if __name__ == '__main__':
    unittest.main()
