import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
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


if __name__ == '__main__':
    unittest.main()
