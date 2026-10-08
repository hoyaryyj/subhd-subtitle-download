import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import download_subtitle as app
import subtitle_files as local

SRT = '1\n00:00:01,000 --> 00:00:02,000\n你好 Hello\n'
ASS = '[Script Info]\nTitle: Test\n[V4+ Styles]\n[Events]\nDialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Hello\n'


class LocalTests(unittest.TestCase):
    def test_episode_tokens_and_boundaries(self):
        for name, expected in [('S01E01', (1, 1)), ('S1E1', (1, 1)), ('1x01', (1, 1)),
                               ('S01E10', (1, 10)), ('S01E010', (1, 10)), ('S02E01', (2, 1))]:
            self.assertEqual(local.parse_episode(name), expected)
        self.assertIsNone(local.parse_episode('Movie.2025.1080p'))
        with self.assertRaises(local.SubtitleError):
            local.parse_episode('S01E01.S01E02')

    def test_combined_episode_ranges_require_explicit_handling(self):
        for name in ('Show.S01E01E02.eng.srt', 'Show.S01E01-E02.eng.srt', 'Show.1x01-02.eng.srt'):
            with self.subTest(name=name), self.assertRaises(local.SubtitleError):
                local.parse_episode(name)

    def test_partial_write_failure_does_not_leave_final_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / 'source.eng.srt'
            source.write_text(SRT)
            video = root / 'Movie.mp4'
            target = video.with_suffix('.srt')
            original_open = Path.open
            class BrokenWrite:
                def __enter__(self):
                    self.fp = original_open(target, 'xb')
                    return self
                def write(self, raw):
                    self.fp.write(raw[:10])
                    raise OSError('disk write interrupted')
                def __exit__(self, *args):
                    self.fp.close()
            def failing_open(path, mode='r', *args, **kwargs):
                if path == target and mode == 'xb':
                    return BrokenWrite()
                return original_open(path, mode, *args, **kwargs)
            with patch.object(local.os, 'link', side_effect=OSError('hard links unavailable')), patch.object(Path, 'open', failing_open):
                with self.assertRaises(OSError):
                    local.install_subtitle(source, video)
            self.assertFalse(target.exists())
            self.assertFalse(list(root.glob('.subhd-*')))
            self.assertEqual(local.install_subtitle(source, video)[0], 'written')

    def test_decode_utf16_endianness_and_gb18030(self):
        for raw in (SRT.encode('utf-8-sig'), SRT.encode('utf-16'), b'\xfe\xff' + SRT.encode('utf-16-be'), SRT.encode('gb18030')):
            text = local.decode_subtitle(raw)
            self.assertEqual(text, SRT)
            self.assertEqual(local.detect_subtitle_format(text), 'srt')

    def test_format_validation_and_content_wins(self):
        self.assertEqual(local.detect_subtitle_format(ASS), 'ass')
        self.assertEqual(local.detect_subtitle_format(ASS.replace('[V4+ Styles]', '[V4 Styles]')), 'ssa')
        self.assertEqual(local.detect_subtitle_format('WEBVTT\n\n00:01.000 --> 00:02.000\nHello'), 'vtt')
        for text in ('', '\ufeff<html>blocked</html>', '{"msg":"failed"}', 'plain text', '[Script Info]\nTitle: incomplete'):
            self.assertIsNone(local.detect_subtitle_format(text))

    def test_language_labels(self):
        for name, lang in [('Show.zh&en.ass', 'zh-eng'), ('Show.zh.srt', 'zh'), ('Show.ChsEng.srt', 'chs-eng'), ('Show.chs&eng.srt', 'chs-eng'),
                           ('简体&英文.srt', 'chs-eng'), ('繁体&英文.srt', 'cht-eng'),
                           ('Show.chs.srt', 'chs'), ('Show.en.srt', 'eng'), ('Show.Cht.srt', 'cht'),
                           ('subtitle.srt', 'unknown')]:
            self.assertEqual(local.language_of(name), lang)

    def test_bundle_selects_episode_language_and_format(self):
        with tempfile.TemporaryDirectory() as d:
            files = []
            for name, text in [('S01E18.chs.eng.srt', SRT), ('S01E01.chs.eng.srt', SRT),
                               ('S01E01.eng.srt', SRT), ('S02E01.eng.srt', SRT), ('S01E01.eng.ass', ASS)]:
                p = Path(d, name)
                p.write_text(text, encoding='utf-8')
                files.append(p)
            video = 'Show.S01E01.mp4'
            self.assertEqual(local.select_subtitle(files, video, 'eng').name, 'S01E01.eng.srt')
            self.assertEqual(local.select_subtitle(files, video, 'eng', 'ass').name, 'S01E01.eng.ass')
            with self.assertRaises(local.SubtitleError):
                local.select_subtitle(files, 'Show.S01E03.mp4', 'eng', allow_single=False)

    def test_ambiguity_and_unknown_language_do_not_guess(self):
        with tempfile.TemporaryDirectory() as d:
            files = [Path(d, n) for n in ('a.eng.srt', 'b.eng.srt')]
            for p in files:
                p.write_text(SRT)
            with self.assertRaises(local.SubtitleError):
                local.select_subtitle(files, 'Movie.mp4', 'eng')
            unknown = Path(d, 'subtitle.srt')
            unknown.write_text(SRT)
            with self.assertRaises(local.SubtitleError):
                local.select_subtitle([unknown], 'Movie.mp4')
            self.assertEqual(local.select_subtitle([unknown], 'Movie.mp4', 'any'), unknown)
            with self.assertRaises(local.SubtitleError):
                local.select_subtitle([unknown], 'Show.S01E01.mp4', 'any', allow_single=False)

    def test_folder_language_labels_and_explicit_member_choice(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            files = [root / folder / 'Show.S01E10.srt' for folder in ('ChsEng', 'Eng')]
            for path in files:
                path.parent.mkdir()
                path.write_text(SRT)
            self.assertEqual(local.select_subtitle(files, 'Show.S01E10.mp4', 'eng', source_root=root), files[1])
            self.assertEqual(local.select_subtitle(files, 'Show.S01E10.mp4', 'any', source_root=root, selected_file='Eng/Show.S01E10.srt'), files[1])

    def test_zip_nested_files_and_content_format(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            payload = root / 'download.bin'
            with zipfile.ZipFile(payload, 'w') as z:
                z.writestr('nested/Show.S01E01.ChsEng.srt', ASS.encode('utf-16'))
            files = local.unpack_payload(payload, root / 'extract')
            chosen = local.select_subtitle(files, 'Show.S01E01.mp4')
            status, target = local.install_subtitle(chosen, root / 'Show.S01E01.mp4')
            self.assertEqual(status, 'written')
            self.assertEqual(target.suffix, '.ass')
            self.assertEqual(target.read_text(encoding='utf-8-sig'), ASS)

    def test_html_disguised_as_srt_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            payload = Path(d, 'subtitle.srt')
            payload.write_bytes(b'\xef\xbb\xbf<html>captcha</html>')
            with self.assertRaises(local.SubtitleError):
                local.unpack_payload(payload, Path(d, 'out'))

    def test_zip_traversal_is_rejected_for_both_path_separators(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name in ('../escape.srt', '..\\escape.srt', '/escape.srt', 'C:\\escape.srt'):
                payload = root / 'bad.zip'
                with zipfile.ZipFile(payload, 'w') as z:
                    z.writestr(name, SRT)
                with self.assertRaises(local.SubtitleError):
                    local.unpack_payload(payload, root / 'out')
            self.assertFalse((root / 'escape.srt').exists())

    def test_symlink_zip_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            payload = Path(d, 'bad.zip')
            info = zipfile.ZipInfo('link')
            info.external_attr = 0o120777 << 16
            with zipfile.ZipFile(payload, 'w') as z:
                z.writestr(info, '../../outside')
            with self.assertRaises(local.SubtitleError):
                local.unpack_payload(payload, Path(d, 'out'))

    def test_existing_unchanged_overwrite_and_exact_stem(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / 'source.eng.srt'
            source.write_text(SRT)
            video = root / '电影 (2025).S1E1.8.00.A.M..mp4'
            status, target = local.install_subtitle(source, video)
            self.assertEqual(target.name, '电影 (2025).S1E1.8.00.A.M..srt')
            self.assertEqual(status, 'written')
            self.assertEqual(local.install_subtitle(source, video)[0], 'unchanged')
            target.write_text('keep me')
            self.assertEqual(local.install_subtitle(source, video)[0], 'skipped_existing')
            self.assertEqual(target.read_text(), 'keep me')
            self.assertEqual(local.install_subtitle(source, video, overwrite=True)[0], 'written')
            self.assertFalse(list(root.glob('.subhd-*')))

    @unittest.skipUnless(shutil.which('7zz') or shutil.which('7z'), '7-Zip optional')
    def test_real_7zip_archive(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / 'S01E01.eng.srt'
            source.write_text(SRT)
            payload = root / 'bundle.7z'
            seven = shutil.which('7zz') or shutil.which('7z')
            subprocess.run([seven, 'a', str(payload), source.name], cwd=root, check=True, capture_output=True)
            files = local.unpack_payload(payload, root / 'out')
            self.assertEqual(local.select_subtitle(files, 'Show.S01E01.mp4', 'eng').read_text(), SRT)


class NetworkTests(unittest.TestCase):
    def test_sid_and_upstream_url_validation(self):
        self.assertEqual(app.validate_sid('https://www.subhd.cc/a/Ab12Cd?x=1'), 'Ab12Cd')
        self.assertEqual(app.validate_sid('123456'), '123456')
        for value in ('https://evil.test/a/Ab12Cd', '../Ab12Cd', ''):
            with self.assertRaises(local.SubtitleError):
                app.validate_sid(value)
        self.assertEqual(app.trusted_url('/down/Ab12Cd', True), app.BASE + '/down/Ab12Cd')
        self.assertEqual(app.trusted_url('https://dlus.subhd.me/test.srt'), 'https://dlus.subhd.me/test.srt')
        for value in ('https://subhd.me.evil.test/test', 'https://not-subhd.me/test', 'http://dl.subhd.me/test', 'https://evil.test/sub.srt', '//localhost/test'):
            with self.assertRaises(local.SubtitleError):
                app.trusted_url(value)

    def test_api_json_rate_limit_and_permission_are_distinct(self):
        with self.assertRaises(app.RateLimited):
            app.api_response(b'{"success":false,"msg":"rate limit"}', 'prepare')
        for raw in (b'<html>login</html>', b'[]', b'{"success":"true"}', b'{"success":false,"msg":"denied"}'):
            with self.assertRaises(local.SubtitleError):
                app.api_response(raw, 'prepare')

    def test_curl_does_not_automatically_disable_tls_or_replay_posts(self):
        response = subprocess.CompletedProcess([], 35, b'', b'TLS failed')
        with patch.object(app.subprocess, 'run', return_value=response) as run:
            self.assertEqual(app.curl(['https://www.subhd.cc/test', '-X', 'POST'])[0], 35)
        self.assertEqual(run.call_count, 1)
        command = run.call_args[0][0]
        self.assertIn('--fail', command)
        self.assertNotIn('--insecure', command)
        self.assertNotIn('--retry', command)

    def test_http_403_and_429_are_not_success(self):
        for message, error in ((b'HTTP 403', local.SubtitleError), (b'HTTP 429', app.RateLimited)):
            with patch.object(app, 'curl', return_value=(22, b'', message)):
                with self.assertRaises(error):
                    app.request(app.BASE + '/test')

    def test_download_chain_uses_one_cookie_and_checks_pass(self):
        with tempfile.TemporaryDirectory() as d:
            replies = [json.dumps({'success': True, 'url': '/down/Ab12Cd'}).encode(), b'ok',
                       json.dumps({'success': True, 'pass': False, 'url': 'https://dl.subhd.me/test.srt'}).encode()]
            with patch.object(app, 'request', side_effect=replies) as req:
                with self.assertRaises(local.SubtitleError):
                    app.download_payload('Ab12Cd', d, 'same-cookie')
            self.assertEqual(req.call_count, 3)
            for call in req.call_args_list:
                self.assertEqual(call.args[1], 'same-cookie')

    def test_bounded_rate_retries_restart_whole_chain(self):
        args = app.argument_parser().parse_args(['--sid', 'Ab12Cd', '--video', 'movie.mp4'])
        with patch.object(app, 'download_payload', side_effect=[app.RateLimited('limit'), Path('ok')]) as fetch, patch.object(app.time, 'sleep') as sleep:
            self.assertEqual(app.fetch_with_backoff('Ab12Cd', 'tmp', 'cookie', args), Path('ok'))
            self.assertEqual(fetch.call_count, 2)
            sleep.assert_called_once_with(120)
        with patch.object(app, 'download_payload', side_effect=app.RateLimited('limit')) as fetch, patch.object(app.time, 'sleep'):
            with self.assertRaises(app.RateLimited):
                app.fetch_with_backoff('Ab12Cd', 'tmp', 'cookie', args)
            self.assertEqual(fetch.call_count, 2)

    def test_search_deduplicates_titles_and_encodes_query(self):
        body = b'<a href="/a/Ab12Cd"><img></a><a href="/a/Ab12Cd">Show <b>S01E01</b></a>'
        with patch.object(app, 'request', return_value=body) as req:
            found = app.search('Show / Season 1')
        self.assertEqual(found, [{'sid': 'Ab12Cd', 'title': 'Show S01E01', 'url': app.BASE + '/a/Ab12Cd'}])
        self.assertIn('Show%20%2F%20Season%201', req.call_args.args[0])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)

    def args(self, *values):
        return app.argument_parser().parse_args(values)

    def test_doctor_does_not_mark_unsupported_python_ready(self):
        output = io.StringIO()
        with patch.object(app.sys, 'version_info', (3, 8, 10)), contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = app.doctor()
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(output.getvalue())['ready'])

    def test_doctor_json_works_with_non_utf8_terminal(self):
        env = dict(os.environ, PYTHONIOENCODING='cp1252')
        script = Path(app.__file__)
        result = subprocess.run([sys.executable, str(script), '--doctor'], capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['ready'])

    def test_legacy_mkv_and_modern_video_flags(self):
        video = self.root / 'Movie.mp4'
        video.touch()
        for flag in ('--mkv', '--video'):
            jobs = app.validate_args(self.args('--sid', 'Ab12Cd', flag, str(video)))
            self.assertEqual(jobs, [('Ab12Cd', [video])])

    def test_download_without_video_uses_explicit_output_name(self):
        report = self.root / 'report.json'
        def fake_download(sid, directory, cookie, insecure=False):
            payload = Path(directory) / 'Movie.eng.srt'
            payload.write_text(SRT)
            return payload
        with patch.object(app, 'download_payload', side_effect=fake_download), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                code = app.main(['--sid', 'Ab12Cd', '--name', '电影 (2025)', '--output-dir', str(self.root), '--language', 'eng', '--report', str(report)])
            except SystemExit as exc:
                code = exc.code
        self.assertEqual(code, 0)
        self.assertTrue((self.root / '电影 (2025).srt').exists())
        self.assertFalse((self.root / '电影 (2025).mp4').exists())

    def test_explicit_encoding_is_applied_once_to_direct_subtitle(self):
        text = SRT.replace('你好 Hello', 'café')
        def fake_download(sid, directory, cookie, insecure=False):
            payload = Path(directory) / 'Movie.eng.srt'
            payload.write_bytes(text.encode('cp1252'))
            return payload
        with patch.object(app, 'download_payload', side_effect=fake_download), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = app.main(['--sid', 'Ab12Cd', '--name', 'Movie', '--output-dir', str(self.root), '--language', 'eng', '--encoding', 'cp1252'])
        self.assertEqual(code, 0)
        self.assertEqual((self.root / 'Movie.srt').read_text(encoding='utf-8-sig'), text)

    def test_legacy_sids_respect_start_episode_and_season(self):
        for name in ('Show.S01E05.mp4', 'Show.1x06.mkv', 'Show.S02E05.mp4'):
            (self.root / name).touch()
        values = ['--dir', str(self.root), '--sids', 'Ab12Cd,De34Fg', '--start-episode', '5']
        with self.assertRaises(local.SubtitleError):
            app.validate_args(self.args(*values))
        jobs = app.validate_args(self.args(*values, '--season', '1'))
        self.assertEqual([local.parse_episode(job[1][0].stem) for job in jobs], [(1, 5), (1, 6)])

    def test_invalid_arguments_fail_before_network(self):
        for values in ([], ['--sids', 'A,B', '--video', 'movie.mp4'], ['--sid', 'A', '--sids', 'B', '--dir', str(self.root)],
                       ['--doctor', '--sid', 'A'], ['--sid', 'A', '--dir', str(self.root), '--interval', '-1']):
            with self.subTest(values=values), patch.object(app, 'request') as req, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(app.main(values), 2)
                req.assert_not_called()

    def test_multiple_episode_tokens_cannot_be_renamed_to_unrelated_episode(self):
        video = self.root / 'Show.S01E10.mp4'
        video.touch()
        def fake_download(sid, directory, cookie, insecure=False):
            payload = Path(directory) / 'Show.S01E01.S01E02.eng.srt'
            payload.write_text(SRT)
            return payload
        with patch.object(app, 'download_payload', side_effect=fake_download), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = app.main(['--sid', 'Ab12Cd', '--video', str(video), '--language', 'eng'])
        self.assertEqual(code, 1)
        self.assertFalse(video.with_suffix('.srt').exists())

    def test_bundle_cli_reports_exact_mapping_missing_and_existing(self):
        videos = [self.root / f'Show.S01E{e:02d}.mp4' for e in (1, 10, 18)]
        for video in videos:
            video.touch()
        videos[0].with_suffix('.srt').write_text('existing')
        report = self.root / 'report.json'
        captured_cookies = []
        def fake_download(sid, directory, cookie, insecure=False):
            captured_cookies.append(Path(cookie))
            self.assertTrue(Path(cookie).exists())
            payload = Path(directory) / 'bundle.zip'
            with zipfile.ZipFile(payload, 'w') as z:
                z.writestr('nested/Show.S01E01.eng.srt', SRT)
                z.writestr('nested/Show.S01E10.eng.srt', SRT.replace('Hello', 'Episode Ten'))
            return payload
        with patch.object(app, 'download_payload', side_effect=fake_download), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = app.main(['--sid', 'Ab12Cd', '--dir', str(self.root), '--language', 'eng', '--report', str(report)])
        self.assertEqual(code, 1)
        data = json.loads(report.read_text())['results']
        self.assertEqual([row['status'] for row in data], ['skipped_existing', 'written', 'failed'])
        self.assertEqual(videos[0].with_suffix('.srt').read_text(), 'existing')
        self.assertIn('Episode Ten', videos[1].with_suffix('.srt').read_text(encoding='utf-8-sig'))
        self.assertFalse(videos[2].with_suffix('.srt').exists())
        self.assertFalse(captured_cookies[0].exists())
        self.assertFalse(Path(data[1]['source']).is_absolute())


if __name__ == '__main__':
    unittest.main()
