#!/usr/bin/env python3
"""
subhd.cc 字幕下载参考实现。

用法：
    # 单集：传 sid + 对应 mkv 路径
    python3 download_subtitle.py --sid 123456 --mkv "/path/to/Show.S01E01.mkv"

    # 整季：传剧集目录 + sid 列表（按集顺序，逗号分隔）
    python3 download_subtitle.py --dir "/path/to/Show Season 1" --sids 111,222,333

详细流程与坑见 ../SKILL.md。
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
BASE = "https://www.subhd.cc"


def curl(args, insecure=False, timeout=60):
    """统一 curl 调用，返回 (returncode, stdout, stderr)。exit 35 自动降级 -k 重试。"""
    cmd = ["curl", "-s", "-S", "--max-time", str(timeout), "--retry", "3", "--retry-delay", "4"]
    if insecure:
        cmd.append("-k")
    cmd += args
    p = subprocess.run(cmd, capture_output=True)
    # exit 35（TLS 握手失败）且未降级时，自动 -k 重试一次（覆盖全链路，见坑 11）
    if p.returncode == 35 and not insecure:
        print("  [warn] curl exit 35, 降级 -k 重试", file=sys.stderr)
        return curl(args, insecure=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def download_one(sid, mkv_path, cookie_jar):
    """
    走完整 4 步链下载一个 sid 的字幕，落到 mkv_path 同目录的 .srt。
    返回 (ok: bool, info: str)。info 为目标路径（成功）或失败原因（失败）。
    失败原因 'rate_limited' 表示命中限流，调用方可退避后重试。
    """
    headers = [
        "-H", f"User-Agent: {UA}",
        "-H", f"Referer: {BASE}/a/{sid}",
        "-H", "X-Requested-With: XMLHttpRequest",
    ]

    # 步骤 1: prepare-download
    rc, out, err = curl([
        "-X", "POST", f"{BASE}/api/sub/prepare-download",
        "-H", "Content-Type: application/json",
        "-d", json.dumps({"sid": sid}),
        "-b", cookie_jar, "-c", cookie_jar,
        *headers,
    ])
    if rc != 0:
        return False, f"prepare curl exit {rc}: {err.decode(errors='replace')[:200]}"
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return False, f"prepare non-JSON: {out[:200]!r}"
    if not data.get("success"):
        msg = str(data.get("msg", ""))
        if "频率过高" in msg:
            return False, "rate_limited"
        return False, f"prepare success=false: {data}"
    # /down/XXXXXX 激活 token
    down_path = data.get("url")
    if not down_path:
        return False, "prepare no url"

    # 步骤 2: 激活 token（必须紧跟步骤 1，几秒内失效）
    rc, out, err = curl([
        f"{BASE}{down_path}",
        "-b", cookie_jar, "-c", cookie_jar,
        *headers,
    ])
    if rc != 0:
        return False, f"activate curl exit {rc}: {err.decode(errors='replace')[:200]}"

    # 步骤 3: /api/sub/down
    rc, out, err = curl([
        "-X", "POST", f"{BASE}/api/sub/down",
        "-H", "Content-Type: application/json",
        "-d", json.dumps({"sid": sid}),
        "-b", cookie_jar, "-c", cookie_jar,
        *headers,
    ])
    if rc != 0:
        return False, f"down curl exit {rc}: {err.decode(errors='replace')[:200]}"
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return False, f"down non-JSON: {out[:200]!r}"
    if not data.get("success"):
        return False, f"down success=false: {data}"
    if not data.get("pass"):
        return False, "down pass=false"
    file_url = data.get("url")
    if not file_url:
        return False, "down no url"

    # 步骤 4: 拉文件（默认不 -k，exit 35 才降级）
    archive_fd, archive_path = tempfile.mkstemp(suffix=".arc")
    os.close(archive_fd)
    rc, out, err = curl([
        file_url, "-o", archive_path,
        "-b", cookie_jar,
        *headers,
    ])
    if rc == 35:
        print(
            f"  [warn] TLS 握手失败 (exit 35)，降级 -k 跳过证书校验。"
            f"URL 来自 subhd 上游：{file_url}",
            file=sys.stderr,
        )
        rc, out, err = curl([
            file_url, "-o", archive_path,
            "-b", cookie_jar,
            *headers,
        ], insecure=True)
    if rc != 0:
        os.unlink(archive_path)
        return False, f"file curl exit {rc}: {err.decode(errors='replace')[:200]}"

    # 嗅探：拉到的可能是直接字幕文件（.srt/.ass）而非归档（坑 13）
    with open(archive_path, "rb") as _fp:
        _head = _fp.read(64)
    _lu = file_url.lower()
    _is_sub = _lu.endswith(".srt") or _lu.endswith(".ass")
    if not _is_sub:
        if _head[:3] == b"\xef\xbb\xbf" or _head[:2] == b"\xff\xfe":
            _is_sub = True
        elif _head.lstrip()[:12].lower().startswith(b"[script info]"):
            _is_sub = True
        elif re.search(rb"\d+\s*\r?\n\s*\d{2}:\d{2}:\d{2}", _head):
            _is_sub = True
    if _is_sub:
        with open(archive_path, "rb") as _fp:
            raw = _fp.read()
        if raw.startswith(b"\xff\xfe"):
            text = raw.decode("utf-16-le")
        elif raw.startswith(b"\xef\xbb\xbf"):
            text = raw.decode("utf-8-sig")
        else:
            text = raw.decode("utf-8", errors="replace")
        os.unlink(archive_path)
        fmt = detect_subtitle_format(text)
        if _lu.endswith(".ass") and not text.lstrip()[:64].lower().startswith("[script info]"):
            fmt = "srt"
        mkv_basename = os.path.basename(mkv_path)
        base = mkv_basename[:-4] if mkv_basename.lower().endswith(".mkv") else re.sub(r"\.[^.]+$", "", mkv_basename)
        dst = os.path.join(os.path.dirname(mkv_path), f"{base}.{fmt}")
        with open(dst, "w", encoding="utf-8-sig") as fp:
            fp.write(text)
        return True, dst

    # 解压：先 tar，失败 fallback 7z
    extract_dir = tempfile.mkdtemp(prefix="subhd_extract_")
    r = subprocess.run(["tar", "-xf", archive_path, "-C", extract_dir], capture_output=True)
    rc, err = r.returncode, r.stderr
    if rc != 0:
        seven = None
        for cand in ("7z", "7zz"):
            if subprocess.run(["which", cand], capture_output=True).returncode == 0:
                seven = cand
                break
        if not seven:
            return False, (
                f"tar 解压失败且无 7z：{err.decode(errors='replace')[:200]}；"
                f"请 brew install 7zip"
            )
        r = subprocess.run(
            [seven, "x", archive_path, f"-o{extract_dir}", "-y"],
            capture_output=True,
        )
        rc, err = r.returncode, r.stderr
        if rc != 0:
            return False, f"7z 解压失败：{err.decode(errors='replace')[:200]}"

    os.unlink(archive_path)

    # 找 Simplified+English 的字幕（.srt 或 .ass，坑 10）
    srt_path = None
    for root, _, files in os.walk(extract_dir):
        for f in files:
            low = f.lower()
            if not (low.endswith(".srt") or low.endswith(".ass")):
                continue
            if "cht" in low or "繁体" in f:
                continue
            if ("简体" in f or "chs" in low) and ("英文" in f or "eng" in low):
                srt_path = os.path.join(root, f)
                break
        if srt_path:
            break
    if not srt_path:
        all_files = []
        for root, _, files in os.walk(extract_dir):
            all_files.extend(files)
        return False, f"归档内未找到简英 srt，文件列表：{all_files[:10]}"

    # 转码：UTF-16 LE / UTF-8 BOM / UTF-8 → 写出 UTF-8 BOM
    with open(srt_path, "rb") as fp:
        raw = fp.read()
    if raw.startswith(b"\xff\xfe"):
        text = raw.decode("utf-16-le")
    elif raw.startswith(b"\xef\xbb\xbf"):
        text = raw.decode("utf-8-sig")
    else:
        text = raw.decode("utf-8", errors="replace")

    # 落盘到 mkv 同目录，basename 派生（杜绝双点）；后缀按实际格式（坑 10）
    fmt = detect_subtitle_format(text)
    mkv_basename = os.path.basename(mkv_path)
    if mkv_basename.lower().endswith(".mkv"):
        base = mkv_basename[:-4]
    else:
        base = re.sub(r"\.[^.]+$", "", mkv_basename)
    srt_name = f"{base}.{fmt}"
    dst = os.path.join(os.path.dirname(mkv_path), srt_name)
    with open(dst, "w", encoding="utf-8-sig") as fp:
        fp.write(text)

    subprocess.run(["rm", "-rf", extract_dir], capture_output=True)
    return True, dst


def find_mkv_for_episode(directory, ep_idx):
    """在 directory 中按 SxxExx / SxE / xExx 找第 ep_idx 集的 mkv（坑 12）。"""
    pattern = re.compile(
        rf"(?:[Ss]\d{{1,2}}[Ee]{ep_idx:02d}|\d{{1,2}}[xX]{ep_idx:02d})",
    )
    for f in sorted(os.listdir(directory)):
        if f.lower().endswith(".mkv") and pattern.search(f):
            return os.path.join(directory, f)
    return None


def detect_subtitle_format(text):
    """按内容嗅探字幕格式：返回 'ass' 或 'srt'（坑 10）。"""
    head = text.lstrip()[:64].lower()
    if head.startswith("[script info]") or "v4+ styles" in head or "dialogue:" in head:
        return "ass"
    return "srt"


def main():
    ap = argparse.ArgumentParser(description="subhd.cc 字幕下载参考实现")
    ap.add_argument("--sid", help="单集 sid")
    ap.add_argument("--mkv", help="单集：对应 mkv 路径")
    ap.add_argument("--dir", help="整季：剧集目录（可含通配符，脚本会用 glob 兜底）")
    ap.add_argument("--sids", help="整季：逗号分隔的 sid 列表，按集顺序")
    ap.add_argument("--interval", type=int, default=45, help="集间隔秒数（默认 45）")
    args = ap.parse_args()

    cookie_fd, cookie_jar = tempfile.mkstemp(suffix=".cookie")
    os.close(cookie_fd)
    failures = []

    try:
        if args.sid:
            if not args.mkv:
                print("--sid 模式需要 --mkv", file=sys.stderr)
                sys.exit(2)
            ok, info = download_one(args.sid, args.mkv, cookie_jar)
            if ok:
                print(f"[1/1] {os.path.basename(args.mkv)} ✓ → {info}")
            else:
                print(f"[1/1] {os.path.basename(args.mkv)} ✗ {info}", file=sys.stderr)
                failures.append((args.sid, info))
        elif args.sids and args.dir:
            sids = [s.strip() for s in args.sids.split(",") if s.strip()]
            directory = args.dir
            # 用 glob 兜底（避免目录名带括号时拼写错）
            if not os.path.isdir(directory):
                candidates = glob.glob(directory + "*")
                if candidates:
                    directory = candidates[0]
            if not os.path.isdir(directory):
                print(f"目录不存在：{args.dir}", file=sys.stderr)
                sys.exit(2)
            total = len(sids)
            for i, sid in enumerate(sids, 1):
                ep_idx = i
                mkv = find_mkv_for_episode(directory, ep_idx)
                if not mkv:
                    print(f"[{i}/{total}] E{ep_idx:02d} ✗ 未找到对应 mkv", file=sys.stderr)
                    failures.append((sid, "no mkv"))
                    continue
                print(f"[{i}/{total}] E{ep_idx:02d} {os.path.basename(mkv)} 下载中...")
                ok, info = download_one(sid, mkv, cookie_jar)
                if ok:
                    print(f"[{i}/{total}] E{ep_idx:02d} ✓ → {info}")
                else:
                    if info == "rate_limited":
                        print(f"[{i}/{total}] E{ep_idx:02d} 命中限流，冷却 120s...", file=sys.stderr)
                        time.sleep(120)
                        ok, info = download_one(sid, mkv, cookie_jar)
                        if ok:
                            print(f"[{i}/{total}] E{ep_idx:02d} ✓（重试） → {info}")
                        else:
                            print(f"[{i}/{total}] E{ep_idx:02d} ✗ {info}", file=sys.stderr)
                            failures.append((sid, info))
                    else:
                        print(f"[{i}/{total}] E{ep_idx:02d} ✗ {info}", file=sys.stderr)
                        failures.append((sid, info))
                if i < total:
                    time.sleep(args.interval)
        else:
            print("需要 --sid+--mkv 或 --dir+--sids", file=sys.stderr)
            sys.exit(2)
    finally:
        if os.path.exists(cookie_jar):
            os.unlink(cookie_jar)

    if failures:
        print(f"\n失败 {len(failures)} 条：", file=sys.stderr)
        for sid, reason in failures:
            print(f"  {sid}: {reason}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
