"""Local subtitle validation, selection and installation (standard library only)."""
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile

VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.avi', '.mov', '.m4v', '.wmv', '.ts', '.m2ts', '.webm'}
SUBTITLE_EXTENSIONS = {'.srt', '.ass', '.ssa', '.vtt'}
MAX_UNPACKED = 256 * 1024 * 1024
MAX_FILES = 2000


class SubtitleError(Exception):
    pass


def parse_episode(name):
    """Match complete numeric tokens, never E01 inside E010 or E10."""
    if re.search(r'(?i)(?:s\d{1,2}e\d{1,3}(?:e\d+|-\s*(?:s\d+)?e?\d+)|\d{1,2}x\d{1,3}-\d+)(?![a-z0-9])', str(name)):
        raise SubtitleError('合并集或集号范围需要人工确认，不会自动配作单集')
    matches = re.findall(r'(?i)(?:s(\d{1,2})e(\d{1,3})|(\d{1,2})x(\d{1,3}))(?!\d)', str(name))
    episodes = {(int(s or sx), int(e or ex)) for s, e, sx, ex in matches}
    if len(episodes) > 1:
        raise SubtitleError('文件含多个季集号，需要人工确认对应关系')
    return next(iter(episodes)) if episodes else None


def decode_subtitle(raw, encoding=None):
    if encoding:
        choices = [encoding]
    elif raw.startswith((b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')):
        choices = ['utf-32']
    elif raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        choices = ['utf-16']
    else:
        choices = ['utf-8-sig', 'gb18030']
    for codec in choices:
        try:
            return raw.decode(codec).lstrip('\ufeff')
        except (UnicodeDecodeError, LookupError):
            continue
    raise SubtitleError('无法解码字幕；请用 --encoding 指定源编码')


def detect_subtitle_format(text):
    # A file extension/BOM alone does not prove this is a subtitle.
    text = text.lstrip('\ufeff \t\r\n')
    if re.search(r'(?i)<(?:!doctype|html|head|body)\b', text[:1024]):
        return None
    if text.startswith('WEBVTT') and re.search(r'\d{2}:\d{2}\.\d{3}\s+-->\s+', text):
        return 'vtt'
    if re.search(r'(?im)^\[Script Info\]\s*$', text) and re.search(r'(?im)^Dialogue\s*:', text):
        return 'ssa' if re.search(r'(?im)^\[V4 Styles\]', text) else 'ass'
    if re.search(r'(?m)^\s*\d{1,3}:\d{2}:\d{2},\d{3}\s+-->\s+\d{1,3}:\d{2}:\d{2},\d{3}', text):
        return 'srt'
    return None


def language_of(name):
    name = str(name).lower()
    bilingual = any(x in name for x in ('chseng', 'chteng', '简英', '繁英'))
    chs = bool(re.search(r'简体|简中|简英|chseng|zh[._-](?:cn|hans)|(?:^|[./\s_&-])chs(?:$|[./\s_&-])', name))
    cht = bool(re.search(r'繁体|繁中|繁英|chteng|zh[._-](?:tw|hant)|(?:^|[./\s_&-])cht(?:$|[./\s_&-])', name))
    eng = bilingual or bool(re.search(r'英文|英语|english|(?:^|[./\s_&-])(?:en|eng)(?:$|[./\s_&-])', name))
    if chs and eng:
        return 'chs-eng'
    if cht and eng:
        return 'cht-eng'
    if chs:
        return 'chs'
    if cht:
        return 'cht'
    zh = bool(re.search(r'中文|chinese|(?:^|[./\s_&-])zh(?:$|[./\s_&-])', name))
    if zh:
        return 'zh-eng' if eng else 'zh'
    if eng:
        return 'eng'
    return 'unknown'


def select_subtitle(files, video, language='chs-eng', preferred_format='auto', allow_single=True, encoding=None, source_root=None, selected_file=None):
    files = sorted(Path(p) for p in files)
    def member_name(path):
        return path.relative_to(source_root).as_posix() if source_root else path.name
    inventory = ', '.join(member_name(p) for p in files[:20])
    if selected_file:
        chosen_name = selected_file.replace('\\', '/')
        files = [p for p in files if member_name(p) == chosen_name]
        if not files:
            raise SubtitleError('指定归档文件不存在；候选：' + inventory)
    episode = parse_episode(Path(video).stem)
    def source_episode(path):
        try:
            return parse_episode(member_name(path))
        except SubtitleError:
            return 'combined'
    files = [p for p in files if source_episode(p) != 'combined']
    if episode:
        matched = [p for p in files if source_episode(p) == episode]
        if not matched and allow_single and len(files) == 1 and source_episode(files[0]) is None:
            matched = files
        files = matched
    else:
        files = [p for p in files if source_episode(p) is None]
    if not files:
        raise SubtitleError(f'没有匹配 {Path(video).name} 的季集字幕；不会用其他集替代')
    if language != 'any':
        def candidate_language(path):
            detected = language_of(path.name)
            return detected if detected != 'unknown' else language_of(member_name(path))
        files = [p for p in files if candidate_language(p) == language]
        if not files:
            raise SubtitleError(f'未找到语言 {language}；未标注的文件需先核验再用 --language any；候选：{inventory}')
    valid = []
    for path in files:
        try:
            fmt = detect_subtitle_format(decode_subtitle(path.read_bytes(), encoding))
        except SubtitleError:
            continue
        if fmt and (preferred_format == 'auto' or fmt == preferred_format):
            valid.append((path, fmt))
    if preferred_format == 'auto' and valid:
        rank = {'srt': 0, 'ass': 1, 'ssa': 2, 'vtt': 3}
        best = min(rank[fmt] for _, fmt in valid)
        valid = [(p, fmt) for p, fmt in valid if rank[fmt] == best]
    if len(valid) != 1:
        raise SubtitleError('没有有效字幕或同条件候选不唯一：' + ', '.join(member_name(p) for p, _ in valid))
    return valid[0][0]


def checked_member(name, destination):
    # Backslashes are separators on Windows, even in a ZIP created on Unix.
    name = name.replace('\\', '/')
    parts = PurePosixPath(name)
    if parts.is_absolute() or '..' in parts.parts or re.match(r'^[A-Za-z]:', name):
        raise SubtitleError(f'归档包含不安全路径：{name}')
    dest = (Path(destination) / name).resolve()
    if not dest.is_relative_to(Path(destination).resolve()):
        raise SubtitleError(f'归档路径越界：{name}')
    return dest


def unpack_payload(payload, destination, encoding=None):
    payload, destination = Path(payload), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(payload):
        try:
            with zipfile.ZipFile(payload) as archive:
                members = archive.infolist()
                if len(members) > MAX_FILES or sum(m.file_size for m in members) > MAX_UNPACKED:
                    raise SubtitleError('归档超过展开限制（2000 文件 / 256 MiB）')
                for member in members:
                    target = checked_member(member.filename, destination)
                    mode = member.external_attr >> 16
                    if stat.S_ISLNK(mode) or member.flag_bits & 1:
                        raise SubtitleError('不支持符号链接或加密归档')
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(member) as src, target.open('wb') as dst:
                            shutil.copyfileobj(src, dst)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
            raise SubtitleError(f'ZIP 解压失败：{exc}') from exc
    elif payload.read_bytes()[:8].startswith((b'7z\xbc\xaf\x27\x1c', b'Rar!\x1a\x07')):
        seven = shutil.which('7zz') or shutil.which('7z')
        if not seven:
            raise SubtitleError('此归档需要 7-Zip：安装 7z/7zz 并加入 PATH；可运行 --doctor')
        listing = subprocess.run([seven, 'l', '-slt', '-ba', '-sccUTF-8', str(payload)], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        if listing.returncode:
            raise SubtitleError('7-Zip 无法列出归档')
        entries = []
        for block in re.split(r'\r?\n\s*\r?\n', listing.stdout.strip()):
            fields = dict(line.split(' = ', 1) for line in block.splitlines() if ' = ' in line)
            if 'Path' not in fields:
                continue
            checked_member(fields['Path'], destination)
            if fields.get('Encrypted') == '+' or fields.get('Symbolic Link') or fields.get('Hard Link') or 'l' in fields.get('Attributes', '').lower():
                raise SubtitleError('不支持链接或加密归档')
            entries.append(int(fields.get('Size', '0')))
        if not entries or len(entries) > MAX_FILES or sum(entries) > MAX_UNPACKED:
            raise SubtitleError('归档为空或超过展开限制')
        result = subprocess.run([seven, 'x', str(payload), f'-o{destination}', '-y', '-bd', '-p'], capture_output=True, timeout=120)
        if result.returncode:
            raise SubtitleError('7-Zip 解压失败')
    else:
        text = decode_subtitle(payload.read_bytes(), encoding)
        fmt = detect_subtitle_format(text)
        if not fmt:
            raise SubtitleError('下载内容不是有效字幕或支持的归档（可能是 HTML 验证页）')
        # Preserve the provider filename for episode/language selection.
        target = destination / (payload.stem + '.' + fmt)
        target.write_bytes(payload.read_bytes())
    return sorted(p for p in destination.rglob('*') if p.is_file() and p.suffix.lower() in SUBTITLE_EXTENSIONS)


def install_subtitle(source, video, output_dir=None, overwrite=False, encoding=None):
    video = Path(video)
    text = decode_subtitle(Path(source).read_bytes(), encoding)
    fmt = detect_subtitle_format(text)
    if not fmt:
        raise SubtitleError('文件内容不是有效字幕，拒绝写入')
    directory = Path(output_dir) if output_dir else video.parent
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (video.stem + '.' + fmt)
    raw = text.encode('utf-8-sig')
    if target.exists():
        if target.read_bytes() == raw:
            return 'unchanged', target
        if not overwrite:
            return 'skipped_existing', target
    # Publish a complete staged file atomically where hard links are supported.
    fd, name = tempfile.mkstemp(dir=directory, prefix='.subhd-')
    temp_path = Path(name)
    try:
        with os.fdopen(fd, 'wb') as temp:
            temp.write(raw)
        if overwrite:
            os.replace(temp_path, target)
        else:
            try:
                os.link(temp_path, target)
            except FileExistsError:
                return 'skipped_existing', target
            except OSError:
                # Some SMB filesystems lack hard links: create exclusively and
                # remove only our newly created target if its write is interrupted.
                try:
                    fp = target.open('xb')
                except FileExistsError:
                    return 'skipped_existing', target
                try:
                    with fp:
                        fp.write(raw)
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
        return 'written', target
    finally:
        temp_path.unlink(missing_ok=True)
