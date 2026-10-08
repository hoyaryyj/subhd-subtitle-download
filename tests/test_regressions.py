import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('download_subtitle', ROOT / 'scripts/download_subtitle.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
SRT = '1\n00:00:01,000 --> 00:00:02,000\nwrong episode\n'


class Regressions(unittest.TestCase):
    def fake_download(self, files, video, root):
        archive = root / 'bundle.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            for name, content in files.items():
                z.writestr(name, content)
        replies = iter([b'{"success":true,"url":"/down/Ab12Cd"}', b'ok', b'{"success":true,"pass":true,"url":"https://dl.subhd.me/bundle.zip"}'])
        def fake_curl(args, **kwargs):
            if '-o' in args:
                Path(args[args.index('-o') + 1]).write_bytes(archive.read_bytes())
                return 0, b'', b''
            return 0, next(replies), b''
        with patch.object(mod, 'curl', fake_curl):
            return mod.download_one('Ab12Cd', str(video), str(root / 'cookie'))

    def test_missing_episode_does_not_install_different_episode(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            video = root / 'Show.S01E01.mkv'
            video.touch()
            ok, info = self.fake_download({'Show.S01E18.chs.eng.srt': SRT}, video, root)
            self.assertFalse(ok, info)
            self.assertFalse(video.with_suffix('.srt').exists())

    def test_existing_subtitle_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            video = root / 'Show.S01E01.mkv'
            video.touch()
            target = video.with_suffix('.srt')
            target.write_text('existing subtitle')
            self.fake_download({'Show.S01E01.chs.eng.srt': SRT}, video, root)
            self.assertEqual(target.read_text(), 'existing subtitle')

    def test_html_is_not_a_subtitle(self):
        self.assertIsNone(mod.detect_subtitle_format('<html>captcha</html>'))

    def test_ambiguous_seasons_are_not_silently_selected(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ('Show.S01E01.mkv', 'Show.S02E01.mkv'):
                Path(d, name).touch()
            self.assertIsNone(mod.find_mkv_for_episode(d, 1))


if __name__ == '__main__':
    unittest.main()
