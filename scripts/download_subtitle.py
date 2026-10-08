#!/usr/bin/env python3
"""Portable SubHD CLI. See --help and ../SKILL.md."""
import argparse
import codecs
import html
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote, unquote, urljoin, urlsplit

from subtitle_files import (
    SubtitleError, VIDEO_EXTENSIONS, decode_subtitle, detect_subtitle_format,
    install_subtitle, parse_episode, select_subtitle, unpack_payload,
)

BASE = 'https://www.subhd.cc'
UA = 'Mozilla/5.0 SubHDSubtitleDownload/2.0'


class RateLimited(SubtitleError):
    pass


def curl(args, insecure=False, timeout=60):
    """Never downgrade TLS automatically; POST token requests are not replayed."""
    cmd = ['curl', '--silent', '--show-error', '--fail', '--location',
           '--max-redirs', '3', '--retry-max-time', '30', '--proto', '=https', '--proto-redir', '=https',
           '--connect-timeout', '15', '--max-time', str(timeout),
           '--max-filesize', str(64 * 1024 * 1024)]
    if '-X' not in args:
        cmd += ['--retry', '2', '--retry-delay', '3']
    if insecure:
        cmd.append('--insecure')
    result = subprocess.run(cmd + args, capture_output=True)
    return result.returncode, result.stdout, result.stderr


def validate_sid(value):
    parsed = urlsplit(value)
    if parsed.scheme:
        if parsed.scheme != 'https' or parsed.hostname not in ('subhd.cc', 'www.subhd.cc'):
            raise SubtitleError('请提供 SubHD 的 https://www.subhd.cc/a/<sid> 地址')
        match = re.fullmatch(r'/a/([A-Za-z0-9]+)/*', parsed.path)
        value = match.group(1) if match else ''
    if not re.fullmatch(r'[A-Za-z0-9]+', value):
        raise SubtitleError('sid 必须是字母数字字符串或 SubHD /a/ 页面 URL')
    return value


def trusted_url(value, activation=False):
    if not isinstance(value, str) or not value:
        raise SubtitleError('上游没有返回有效下载地址')
    url = urljoin(BASE + '/', value)
    parsed = urlsplit(url)
    # Do not accept arbitrary hosts/private URLs from an upstream response.
    allowed = {'subhd.cc', 'www.subhd.cc'}
    allowed_host = parsed.hostname in allowed or (not activation and bool(parsed.hostname) and parsed.hostname.endswith('.subhd.me'))
    if parsed.scheme != 'https' or not allowed_host or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise SubtitleError('下载地址不属于已支持的 SubHD HTTPS 域名；需核验站点变化')
    if activation and not parsed.path.startswith('/down/'):
        raise SubtitleError('上游激活地址不是 /down/；需核验站点变化')
    return url


def request(url, cookie_jar=None, sid=None, post=False, insecure=False, output=None):
    args = [url, '-H', f'User-Agent: {UA}', '-H', f'Referer: {BASE}/a/{sid or ""}']
    if cookie_jar:
        args += ['-b', str(cookie_jar), '-c', str(cookie_jar)]
    if post:
        args += ['-X', 'POST', '-H', 'Content-Type: application/json',
                 '-H', 'X-Requested-With: XMLHttpRequest', '-d', json.dumps({'sid': sid})]
    if output:
        args += ['-o', str(output)]
    rc, body, stderr = curl(args, insecure=insecure)
    if rc:
        error = stderr.decode(errors='replace')[:240]
        if rc == 22 and '429' in error:
            raise RateLimited('HTTP 429：下载频率过高')
        if rc == 22 and '403' in error:
            raise SubtitleError('HTTP 403：站点拒绝访问或需要浏览器验证；停止自动重试')
        raise SubtitleError(f'curl exit {rc}: {error}')
    return body


def api_response(body, stage):
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SubtitleError(f'{stage} 返回非 JSON；可能是登录/验证页或 API 已变化') from exc
    if not isinstance(data, dict):
        raise SubtitleError(f'{stage} 返回的 JSON 结构不受支持')
    msg = str(data.get('msg', ''))
    if data.get('success') is not True:
        if re.search(r'频率|rate.?limit|too many', msg, re.I):
            raise RateLimited(msg)
        raise SubtitleError(f'{stage} 失败：{msg or "success 不为 true"}')
    return data


def download_payload(sid, directory, cookie_jar, insecure=False):
    sid = validate_sid(sid)
    prepare = api_response(request(BASE + '/api/sub/prepare-download', cookie_jar, sid, True, insecure), 'prepare')
    activation = trusted_url(prepare.get('url'), activation=True)
    request(activation, cookie_jar, sid, insecure=insecure)
    result = api_response(request(BASE + '/api/sub/down', cookie_jar, sid, True, insecure), 'down')
    if result.get('pass') is not True:
        raise SubtitleError('下载许可 pass 不为 true；可能需人工验证或配额不足')
    file_url = trusted_url(result.get('url'))
    filename = Path(unquote(urlsplit(file_url).path)).name
    # Prevent path components from encoded provider filenames.
    filename = re.sub(r'[\\/:\x00-\x1f]', '_', filename) or 'subtitle.bin'
    payload = Path(directory) / filename
    request(file_url, cookie_jar, sid, insecure=insecure, output=payload)
    if not payload.is_file() or payload.stat().st_size > 64 * 1024 * 1024:
        raise SubtitleError('下载文件缺失或超过 64 MiB 限制')
    return payload


def fetch_with_backoff(sid, directory, cookie, args):
    for attempt in range(args.rate_retries + 1):
        try:
            return download_payload(sid, directory, cookie, args.insecure)
        except RateLimited:
            if attempt == args.rate_retries:
                raise
            wait = args.cooldown * (attempt + 1)
            print(f'[限流] 冷却 {wait:g} 秒后重新开始下载链', file=sys.stderr, flush=True)
            time.sleep(wait)


def download_one(sid, mkv_path, cookie_jar, language='chs-eng', preferred_format='auto', overwrite=False):
    """Compatibility helper; no unrelated episode fallback and no default overwrite."""
    try:
        with tempfile.TemporaryDirectory(prefix='subhd-') as d:
            payload = download_payload(sid, d, cookie_jar)
            files = unpack_payload(payload, Path(d) / 'files')
            source = select_subtitle(files, mkv_path, language, preferred_format)
            status, dst = install_subtitle(source, mkv_path, overwrite=overwrite)
            return True, f'{status}: {dst}'
    except (SubtitleError, OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)


def video_files(directory, recursive=False):
    root = Path(directory)
    if not root.is_dir():
        raise SubtitleError(f'目录不存在：{directory}（不会自动猜测相似路径）')
    paths = root.rglob('*') if recursive else root.iterdir()
    return sorted(p for p in paths if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)


def find_mkv_for_episode(directory, ep_idx, season=None):
    # Name retained for callers of the original reference script.
    found = [p for p in video_files(directory) if parse_episode(p.stem) and
             parse_episode(p.stem)[1] == ep_idx and (season is None or parse_episode(p.stem)[0] == season)]
    return str(found[0]) if len(found) == 1 else None


class SearchParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.current = None
        self.items = {}

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            attrs = dict(attrs)
            match = re.fullmatch(r'/a/([A-Za-z0-9]+)/*', urlsplit(attrs.get('href', '')).path)
            if match:
                self.current = (match.group(1), [attrs.get('title', '')])

    def handle_data(self, data):
        if self.current:
            self.current[1].append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.current:
            sid, texts = self.current
            title = ' '.join(' '.join(texts).split())
            if title and len(title) > len(self.items.get(sid, '')):
                self.items[sid] = html.unescape(title)
            self.current = None


def search(query):
    body = request(BASE + '/search/' + quote(query, safe=''))
    text = body.decode('utf-8', errors='replace')
    if re.search(r'(?i)just a moment|cf-chl-|captcha', text):
        raise SubtitleError('搜索遇到浏览器验证；请用可用浏览器搜索并复制 /a/ 地址')
    parser = SearchParser()
    parser.feed(text)
    if not parser.items:
        raise SubtitleError('未解析到候选条目；可能无结果、需要验证或页面结构已变，请用浏览器核查')
    return [{'sid': sid, 'title': title, 'url': BASE + '/a/' + sid} for sid, title in parser.items.items()]


def doctor():
    result = {'python': sys.version.split()[0], 'platform': sys.platform,
              'curl': shutil.which('curl'), '7zip': shutil.which('7zz') or shutil.which('7z'),
              'zip': 'Python 标准库，无需安装',
              'network_checked': False, 'ready': bool(shutil.which('curl')) and sys.version_info >= (3, 9)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if sys.version_info < (3, 9):
        print('请安装 Python 3.9 或更新版本。', file=sys.stderr)
    if not result['curl']:
        print('请安装 curl 并加入 PATH。Windows 10/11 通常已有 curl.exe；Linux 用系统包管理器安装。', file=sys.stderr)
    if not result['7zip']:
        print('可处理 ZIP/直接字幕。7z/RAR 需另装 7-Zip：macOS brew install sevenzip；Linux 安装 7zip/p7zip-full；Windows 安装 7-Zip 并将 7z.exe 所在目录加入 PATH。', file=sys.stderr)
    return 0 if result['ready'] else 2


def argument_parser():
    ap = argparse.ArgumentParser(description='SubHD 字幕搜索/下载：Python 3.9+、curl；ZIP 无额外依赖')
    ap.add_argument('--doctor', action='store_true', help='检查本地依赖，不联网或修改配置')
    ap.add_argument('--search', metavar='QUERY', help='搜索候选条目（JSON 输出，不自动选择或下载）')
    ap.add_argument('--sid', help='单条 sid 或 https://www.subhd.cc/a/<sid>；可配 --video 或 --dir')
    ap.add_argument('--video', '--mkv', dest='video', help='电影或剧集视频路径；兼容旧 --mkv')
    ap.add_argument('--name', help='没有本地视频时的输出基本名（不含字幕后缀），需 --output-dir')
    ap.add_argument('--dir', help='视频目录：单 sid 整季包，或 --sids 按集号分配')
    ap.add_argument('--sids', help='旧批量模式：逗号分隔 sid 列表，从 --start-episode 起依次对应集号')
    ap.add_argument('--season', type=int, help='目录模式只处理指定季；--sids 多季目录时必须指定')
    ap.add_argument('--start-episode', type=int, default=1, help='--sids 第一条对应集号，默认 1')
    ap.add_argument('--recursive', action='store_true', help='递归扫描视频目录（默认仅当前层）')
    ap.add_argument('--language', choices=['chs-eng', 'cht-eng', 'chs', 'cht', 'zh-eng', 'zh', 'eng', 'any'], default='chs-eng', help='默认简英双语；any 允许无语言标记，但仍拒绝候选歧义')
    ap.add_argument('--format', choices=['auto', 'srt', 'ass', 'ssa', 'vtt'], default='auto', help='默认按实际内容识别，优先 SRT，不做格式转换')
    ap.add_argument('--select-file', help='明确选择归档内相对路径，如 Eng/Show.S01E01.srt；仍校验季集和语言')
    ap.add_argument('--encoding', help='可选源编码，如 big5；默认 UTF BOM/UTF-8/GB18030')
    ap.add_argument('--output-dir', help='输出目录，默认视频旁；文件名保持视频 stem 原样')
    ap.add_argument('--overwrite', action='store_true', help='明确允许替换同名字幕（默认保留）')
    ap.add_argument('--interval', type=float, default=45, help='多个 sid 下载之间间隔秒数，默认 45')
    ap.add_argument('--cooldown', type=float, default=120, help='命中限流后的首次冷却秒数，默认 120')
    ap.add_argument('--rate-retries', type=int, choices=range(4), default=1, help='限流后重试次数 0..3，默认 1；403/验证码不重试')
    ap.add_argument('--report', help='保存 JSON 结果（必须是尚不存在的路径）')
    ap.add_argument('--insecure', action='store_true', help='显式跳过 TLS 证书验证；不会自动启用')
    return ap


def validate_args(args):
    if args.doctor or args.search:
        if args.doctor and args.search or args.sid or args.sids or args.video or args.name or args.dir:
            raise SubtitleError('--doctor/--search 必须单独使用')
        return []
    if bool(args.sid) == bool(args.sids) or sum(bool(v) for v in (args.video, args.name, args.dir)) != 1:
        raise SubtitleError('需要 --sid + --video/--dir/--name，或 --sids + --dir')
    if args.sids and not args.dir:
        raise SubtitleError('--sids 需要 --dir')
    if not math.isfinite(args.interval) or not math.isfinite(args.cooldown) or args.interval < 0 or args.cooldown < 0 or args.start_episode < 1 or args.season is not None and args.season < 0:
        raise SubtitleError('间隔/冷却/季号不能为负；起始集号必须为正')
    if args.encoding:
        try:
            codecs.lookup(args.encoding)
        except LookupError as exc:
            raise SubtitleError('未知编码：' + args.encoding) from exc
    if args.report:
        report = Path(args.report)
        if report.exists() or not report.parent.is_dir():
            raise SubtitleError('报告路径已存在或父目录不存在；请选择新路径')
    if args.name:
        if not args.output_dir or re.search(r'[\\/<>:"|?*\x00-\x1f]', args.name) or args.name in ('.', '..') or args.name.endswith((' ', '.')):
            raise SubtitleError('--name 需要 --output-dir，且基本名不能含路径分隔符或不通用的文件名字符')
        return [(validate_sid(args.sid), [Path(args.output_dir) / (args.name + '.mp4')])]
    if args.video:
        video = Path(args.video)
        if not video.is_file() or video.suffix.lower() not in VIDEO_EXTENSIONS:
            raise SubtitleError(f'视频不存在或格式不受支持：{video}')
        parse_episode(video.stem)
        return [(validate_sid(args.sid), [video])]
    videos = video_files(args.dir, args.recursive)
    if args.season is not None:
        videos = [v for v in videos if parse_episode(v.stem) and parse_episode(v.stem)[0] == args.season]
    if not videos:
        raise SubtitleError('未找到符合条件的视频')
    if args.sid:
        if any(parse_episode(v.stem) is None for v in videos):
            raise SubtitleError('--sid + --dir 整季包模式要求视频名含 S01E01/S1E1/1x01；电影用 --video')
        return [(validate_sid(args.sid), videos)]
    sids = args.sids.split(',')
    if not all(s.strip() for s in sids):
        raise SubtitleError('--sids 列表包含空项')
    seasons = {parse_episode(v.stem)[0] for v in videos if parse_episode(v.stem)}
    if len(seasons) != 1:
        raise SubtitleError('--sids 目录季号不唯一；请指定 --season 或分季处理')
    jobs = []
    for idx, sid in enumerate(sids, args.start_episode):
        matches = [v for v in videos if parse_episode(v.stem) == (next(iter(seasons)), idx)]
        if len(matches) != 1:
            raise SubtitleError(f'E{idx:02d} 视频缺失或不唯一；请使用 --sid + --video 显式指定')
        jobs.append((validate_sid(sid.strip()), matches))
    return jobs


def run_downloads(jobs, args):
    results = []
    with tempfile.TemporaryDirectory(prefix='subhd-session-') as session:
        cookie = Path(session) / 'cookies.txt'
        cookie.touch(mode=0o600)
        for index, (sid, videos) in enumerate(jobs, 1):
            print(f'[{index}/{len(jobs)}] sid={sid}，目标 {len(videos)} 个视频，下载中…', flush=True)
            with tempfile.TemporaryDirectory(prefix='subhd-payload-') as d:
                try:
                    payload = fetch_with_backoff(sid, d, cookie, args)
                    files = unpack_payload(payload, Path(d) / 'files', args.encoding)
                except (SubtitleError, OSError, subprocess.SubprocessError, ValueError) as exc:
                    for video in videos:
                        identity = {'name': args.name} if args.name else {'video': str(video)}
                        results.append({'sid': sid, **identity, 'status': 'failed', 'reason': str(exc)})
                    print(f'  failed: {exc}', file=sys.stderr, flush=True)
                else:
                    for video in videos:
                        identity = {'name': args.name} if args.name else {'video': str(video)}
                        label = args.name or video.name
                        try:
                            source = select_subtitle(files, video, args.language, args.format, allow_single=not args.dir or bool(args.sids), encoding=args.encoding, source_root=Path(d) / 'files', selected_file=args.select_file)
                            status, target = install_subtitle(source, video, args.output_dir, args.overwrite, args.encoding)
                            record = {'sid': sid, **identity, 'source': source.relative_to(Path(d) / 'files').as_posix(),
                                      'status': status, 'target': str(target)}
                            print(f'  {label}: {status} → {target}', flush=True)
                        except (SubtitleError, OSError, UnicodeError) as exc:
                            record = {'sid': sid, **identity, 'status': 'failed', 'reason': str(exc)}
                            print(f'  {label}: failed: {exc}', file=sys.stderr, flush=True)
                        results.append(record)
            if index < len(jobs):
                print(f'等待 {args.interval:g} 秒后处理下一条 sid', flush=True)
                time.sleep(args.interval)
    if args.report:
        with Path(args.report).open('x', encoding='utf-8') as fp:
            json.dump({'results': results}, fp, ensure_ascii=False, indent=2)
    counts = {key: sum(r['status'] == key for r in results) for key in ('written', 'unchanged', 'skipped_existing', 'failed')}
    print(json.dumps(counts, ensure_ascii=False), flush=True)
    return 1 if counts['failed'] else 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    ap = argument_parser()
    args = ap.parse_args(argv)
    try:
        jobs = validate_args(args)
        if args.doctor:
            return doctor()
        if sys.version_info < (3, 9):
            raise SubtitleError('需要 Python 3.9 或更新版本')
        if not shutil.which('curl'):
            raise SubtitleError('缺少 curl；请先运行 --doctor')
        if args.insecure:
            print('[警告] --insecure 已显式开启，本次 HTTPS 不验证证书', file=sys.stderr, flush=True)
        if args.search:
            print(json.dumps(search(args.search), ensure_ascii=False, indent=2))
            return 0
        return run_downloads(jobs, args)
    except (SubtitleError, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f'错误：{exc}', file=sys.stderr, flush=True)
        return 2
    except KeyboardInterrupt:
        print('已中断，临时下载和 cookie 已清理；已完成文件保留', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
