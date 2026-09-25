import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('bose', Path(__file__).resolve().parents[1] / 'tools/bose.py')
bose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bose)


class RestoreSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.host = '192.168.0.152'
        self.identity = ET.fromstring('<info deviceID="TEST"/>')
        self.original = ET.fromstring('<presets>' + ''.join(
            f'<preset id="{n}"><ContentItem source="DEEZER" location="old{n}"/></preset>'
            for n in range(1, 7)) + '</presets>')
        self.current = ET.fromstring(ET.tostring(self.original))
        self.current.find('preset/ContentItem').set('location', 'marker')
        ET.ElementTree(self.identity).write(self.directory / f'info-{self.host}.xml')
        ET.ElementTree(self.original).write(self.directory / f'presets-{self.host}.xml')
        self.writes = []

    def request(self, host, endpoint, body=None):
        self.assertEqual(host, self.host)
        if body is not None:
            self.assertEqual(endpoint, 'storePreset')
            preset = ET.fromstring(body)
            self.writes.append(preset.get('id'))
            existing = next(p for p in self.current if p.get('id') == preset.get('id'))
            self.current.remove(existing)
            self.current.append(preset)
            return b'<status/>', ET.Element('status')
        root = self.identity if endpoint == 'info' else self.current
        return ET.tostring(root), root

    def test_preview_never_writes(self):
        with patch.object(bose, 'request', side_effect=self.request):
            bose.restore(self.host, self.directory, False)
        self.assertEqual(self.writes, [])

    def test_restore_writes_only_one_and_two_after_backup(self):
        with patch.object(bose, 'request', side_effect=self.request), patch.object(bose, 'backup') as backup:
            bose.restore(self.host, self.directory, True)
        backup.assert_called_once_with([self.host])
        self.assertEqual(self.writes, ['1', '2'])

    def test_wrong_device_refused(self):
        self.identity.set('deviceID', 'DIFFERENT')
        with patch.object(bose, 'request', side_effect=self.request), self.assertRaises(ValueError):
            bose.restore(self.host, self.directory, True)
        self.assertEqual(self.writes, [])

    def test_failed_backup_prevents_writes(self):
        with patch.object(bose, 'request', side_effect=self.request), patch.object(bose, 'backup', side_effect=RuntimeError), self.assertRaises(RuntimeError):
            bose.restore(self.host, self.directory, True)
        self.assertEqual(self.writes, [])


class Reboot(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.connect = patch.object(bose.socket, 'create_connection', return_value=self.connection)
        self.create_connection = self.connect.start()
        self.addCleanup(self.connect.stop)
        self.output = patch('builtins.print')
        self.output.start()
        self.addCleanup(self.output.stop)

    def test_cuisine_cli_waits_for_fragmented_prompt_and_sends_once(self):
        self.connection.recv.side_effect = [b'Console\r\n-', b'>']
        with patch('sys.argv', ['bose.py', '--host', '192.168.0.151', 'reboot']):
            bose.main()
        self.create_connection.assert_called_once_with(('192.168.0.151', 17000), timeout=6)
        self.connection.sendall.assert_called_once_with(b'sys reboot\r\n')
        self.connection.__exit__.assert_called_once()

    def test_closed_console_sends_nothing(self):
        self.connection.recv.return_value = b''
        with self.assertRaisesRegex(RuntimeError, 'Console fermée'):
            bose.reboot('192.168.0.151')
        self.connection.sendall.assert_not_called()

    def test_timeout_sends_nothing(self):
        self.connection.recv.side_effect = bose.socket.timeout()
        with self.assertRaisesRegex(RuntimeError, 'Invite de diagnostic'):
            bose.reboot('192.168.0.151')
        self.connection.sendall.assert_not_called()

    def test_unexpected_banner_is_bounded(self):
        self.connection.recv.return_value = b'x' * 1024
        with self.assertRaisesRegex(RuntimeError, 'Invite de diagnostic'):
            bose.reboot('192.168.0.151')
        self.assertEqual(self.connection.recv.call_count, 4)
        self.connection.sendall.assert_not_called()

    def test_prompt_deadline_does_not_reset_for_each_chunk(self):
        self.connection.recv.return_value = b'-'
        with patch.object(bose.time, 'monotonic', side_effect=[0, 1, 7]):
            with self.assertRaisesRegex(RuntimeError, 'Invite de diagnostic'):
                bose.reboot('192.168.0.151')
        self.connection.recv.assert_called_once()
        self.connection.sendall.assert_not_called()

    def test_send_failure_is_not_retried(self):
        self.connection.recv.return_value = b'->'
        self.connection.sendall.side_effect = BrokenPipeError()
        with self.assertRaises(BrokenPipeError):
            bose.reboot('192.168.0.151')
        self.connection.sendall.assert_called_once()
        self.create_connection.assert_called_once()


if __name__ == '__main__':
    unittest.main()
