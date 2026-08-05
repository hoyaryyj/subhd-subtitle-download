# subhd-subtitle-download

从 [subhd.cc](https://www.subhd.cc) 批量下载剧集字幕（简体中文 / 简英双语）的 Agent Skill 与参考脚本。自动走通 subhd 的「内联 + 短时效 token」下载链，选档、转码、按视频同名命名，让 VLC / Infuse / IINA / Plex / Kodi 自动加载。

> 仅供个人学习使用，商用需获版权方授权。

## 内容

- `SKILL.md` — 完整流程说明与踩坑清单（Cloudflare/TLS、7z/zip 解压、直接字幕文件嗅探、限流退避、SMB 挂载、集号正则、整季合集等）。
- `scripts/download_subtitle.py` — 可执行参考实现，支持单集 / 整季批量下载。

## 快速使用

```bash
# 单集：传 sid + 对应 mkv 路径
python3 scripts/download_subtitle.py --sid <SID> --mkv "/path/to/Show.S01E01.mkv"

# 整季：传剧集目录 + sid 列表（按集顺序，逗号分隔）
python3 scripts/download_subtitle.py --dir "/path/to/Show Season 1" --sids 111,222,333
```

依赖：系统 `curl`（必需，subhd 走 Cloudflare，不要用 Python urllib）；`.7z` 归档优先用系统 `tar`/`7z`，参考脚本也支持纯 Python 的 `py7zr`。

## 机制要点

1. `POST /api/sub/prepare-download` → 取 `/down/<sid>`
2. 立刻 `GET /down/<sid>` 激活短时效 token（几秒内失效）
3. `POST /api/sub/down` → 取真实文件 URL（校验 `pass==true`）
4. `GET` 文件，按文件头嗅探是直接字幕（`.srt`/`.ass`）还是归档，再解压 / 转码 / 落盘

选档默认偏好「简体&英文」，落盘为与视频完全相同的 basename（杜绝双点问题），并统一转成 UTF-8 BOM。

详见 [`SKILL.md`](./SKILL.md)。
