# subhd-subtitle-download

一个可独立运行的 SubHD 字幕下载 skill。输入作品名称让 Agent 搜索，也可以直接传 SubHD 条目地址；支持电影、单集和整季包，按真实季集号配对视频，默认保留已有字幕。

支持 macOS / Linux / Windows 的 Python 环境。Python 3.9+ 与 curl 为必需依赖；ZIP 和直接字幕不需要 pip 包，7z/RAR 按需使用 7z/7zz。网络请求仍会受 SubHD 的验证、配额和接口变化影响，不能保证每台机器都免验证下载。

## 安装为 skill

下载并解压本项目，将整个 `subhd-subtitle-download` 文件夹放入所用 Agent 的 skill 目录。**保留 `scripts/`、`references/` 和 `agents/`，不要只复制 SKILL.md。** 安装位置以目标 Agent 当前支持的目录为准；安装后开启新会话或刷新 skills。

也可以先用 Git 获取项目，再复制到目标目录：

```bash
git clone https://github.com/hoyaryyj/subhd-subtitle-download.git
```

Codex 当前支持项目级 `.agents/skills/subhd-subtitle-download/` 和用户级 `~/.agents/skills/subhd-subtitle-download/`；Windows 用户级路径为 `%USERPROFILE%\.agents\skills\subhd-subtitle-download\`。可在仓库已更新到本版本后直接克隆到该文件夹，也可把解压后的完整文件夹复制过去。路径依据 [OpenAI 官方说明](https://learn.chatgpt.com/docs/build-skills)。其他 Agent 按其支持的 skill 目录安装。

无法识别 skills 的环境也可以直接让 Agent 读取本项目的 `SKILL.md`，或独立运行下面的 CLI，无需另一项 skill 或浏览器插件。

示例请求：

> 给这部电影找简体中文字幕，保存到指定目录，保留已有文件。
>
> 给这个目录的第一季配简英双语字幕，优先匹配 AMZN WEB-DL 版本，告诉我缺了哪些集。
>
> 这是一个 SubHD 字幕地址，请下载英文版，文件名与我的 MP4 相同。

## 五分钟开始

在项目目录执行；从别的目录调用时把脚本路径换为绝对路径。Windows 按实际安装用 `py -3` 或 `python` 替代 `python3`，路径含空格或中文时始终加引号。

```bash
# 1. 检查依赖（不联网，不安装软件）
python3 scripts/download_subtitle.py --doctor

# 2. 搜索候选，返回标题、sid、URL，供核验语言和发布版本
python3 scripts/download_subtitle.py --search "作品名称 年份 S01"

# 3. 传真实 sid 或完整 /a/ 地址，为本地视频下载字幕
python3 scripts/download_subtitle.py --sid "<sid>" --video "/media/Show.S01E01.mp4" --language chs-eng
```

`<sid>` 是占位符，请替换为搜索结果的实际值。脚本不自动选择搜索第一条，避免同名作品、跨季或版本误配。也可以直接传 `https://www.subhd.cc/a/<sid>`。

## 常用场景

```bash
# 电影：纯简体中文字幕
python3 scripts/download_subtitle.py --sid "<sid>" --video "/media/Movie.2025.mkv" --language chs

# 整季包：一个 sid 只下载一次，按包内实际季集号配对
python3 scripts/download_subtitle.py --sid "<sid>" --dir "/media/Show Season 1" --season 1 --language chs-eng --report "/outputs/new-report.json"

# 没有本地视频：指定输出基本名，无需创建视频
python3 scripts/download_subtitle.py --sid "<sid>" --name "Movie.2025" --output-dir "/outputs/subtitles" --language eng

# 保留旧参数：从 E05 起，对应同一季 E05、E06
python3 scripts/download_subtitle.py --dir "/media/Show" --sids "<sid-E05>,<sid-E06>" --season 1 --start-episode 5

# Windows 示例（路径与 sid 换成自己的）
py -3 scripts/download_subtitle.py --sid "<sid>" --video "D:\Movies\Movie.mp4" --language chs
```

旧 `--mkv` 是 `--video` 的别名，也可以接收支持的其他视频格式。旧 `--sids` 默认从 E01 起按连续集号映射，多季或同集多个视频会要求明确目标；不再根据目录排序猜测。非连续集分别用 `--sid + --video`。

## 参数与默认行为

| 选项 | 行为 |
| --- | --- |
| `--language` | 默认 `chs-eng`；支持 `cht-eng`、`chs`、`cht`、`zh-eng`、`zh`、`eng`、`any` |
| `--format` | 默认 `auto` 按内容识别并优先 SRT；可筛选 `srt/ass/ssa/vtt`，不转换格式 |
| `--output-dir` | 默认在视频旁；指定后写入该目录，保留视频基本名 |
| `--overwrite` | 显式允许替换同名文件；不指定则保留不同内容的已有字幕 |
| `--select-file` | 显式选择归档内相对路径，如 `Eng/Show.S01E01.srt`；仍校验季集、语言和实际格式 |
| `--encoding` | 默认识别 UTF BOM、UTF-8、GB18030；可指定 `big5` 等源编码 |
| `--recursive` | 目录默认只扫描一层，指定后递归 |
| `--report` | 将每个目标的成功/保留/失败结果保存为新的 JSON 文件，父目录需存在 |
| `--interval` | 多 sid 间隔默认 45 秒 |
| `--cooldown` / `--rate-retries` | 明确限流默认冷却 120 秒后重试一次；最多可设置 3 次 |
| `--insecure` | 仅在明确接受风险时跳过证书验证；TLS 失败不会自动开启 |

完整选项用 `--help` 查看。语言筛选基于文件名和归档内的语言子目录：`zh-eng` 表示中文与英文但未标注简繁，不能当作纯英文；`any` 允许无语言标记，仍拒绝多候选歧义。默认不回退到用户没要求的语言。

支持视频：MKV、MP4、AVI、MOV、M4V、WMV、TS、M2TS、WEBM。支持直接字幕和 ZIP/7z/RAR；嵌套目录会递归查找。整季包的视频/字幕名应含完整 `S01E01`、`S1E1` 或 `1x01`，缺集和不确定对应关系会报告失败。多候选需核验并选择更明确的条目/格式。

输出统一为 UTF-8 BOM，后缀由实际内容决定。已有相同字节返回 `unchanged`；同名不同内容返回 `skipped_existing`。退出码 `0` 代表没有失败项，`1` 代表下载或匹配有失败项，`2` 代表参数/依赖/执行错误，`130` 为用户中断。文件写入成功不等于时间轴已与视频同步，必要时播放检查。

## 依赖和排障

7z/RAR 需要 7-Zip：有 Homebrew 的 macOS 可运行 `brew install sevenzip`；Linux 使用发行版的 `7zip`/`p7zip-full`；Windows 安装 [7-Zip](https://www.7-zip.org/) 并把 7z.exe 加入 PATH。无需为了 ZIP 安装 7-Zip。

403、验证码、登录页或 `pass=false` 会停止当前条目并报告；可用浏览器正常核查。不要反复请求或把 HTML 当字幕。当前下载限制为 64 MiB，解压限制为 256 MiB / 2000 个成员，加密归档和链接不支持。协议与平台说明见 [references/subhd-protocol.md](references/subhd-protocol.md)。

## 维护与验证

```bash
python3 -m unittest discover -s tests -v
```

测试涵盖错集、防覆盖、语言筛选、编码、实际格式、归档路径和 API/HTTP 失败。发现可用 7-Zip 时会实测创建与解压 7z，否则跳过该项。CI 配置覆盖 macOS、Linux、Windows 的 Python 3.9 和 3.13，不做实时站点请求；CI 以各次实际运行结果为准；本地通过不能代替其他平台的验证。

入口为 [SKILL.md](SKILL.md)，CLI 位于 [scripts/download_subtitle.py](scripts/download_subtitle.py)，本地处理位于 [scripts/subtitle_files.py](scripts/subtitle_files.py)。字幕版权归相应权利方，使用应符合用户取得的授权。

---

English: A self-contained Agent Skill and CLI for SubHD subtitles. Requires Python 3.9+ and curl; ZIP/direct subtitles need no pip packages. 7z/RAR require optional 7-Zip. Copy the complete folder into your agent's supported skill directory, or run `python scripts/download_subtitle.py --doctor` and `--help`. Supports movies, episodes, season bundles, language selection, exact episode matching and preservation of existing files. Live access may require browser verification.
