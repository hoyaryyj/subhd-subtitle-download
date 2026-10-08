---
name: subhd-subtitle-download
description: Use when a user wants to find or download movie or TV subtitles from SubHD (subhd.cc), match subtitles to local video filenames, or troubleshoot SubHD downloads that return HTML, fail to extract, or select the wrong episode. 适用于 SubHD 字幕搜索、下载、电影/剧集字幕配对、整季批量处理、简繁中文及中英双语字幕。
---

# SubHD 字幕下载

找到合适的 SubHD 字幕，验证内容后保存为可播放的文件。支持电影、单集、整季包；支持 SRT、ASS、SSA、VTT，保留实际格式和样式。

## 先确定目标

从用户的话和可访问的文件名中提取片名、年份、季集、字幕语言、视频版本和输出位置。已有信息直接采用；同名作品、季号不明、多个发布版本等会影响正确性时再问。默认简英双语，用户指定纯中文、英文或繁体时以用户为准。

- 有本地视频：从实际路径派生字幕名，不手工重建、折叠双点或修改视频名。支持 MKV、MP4、AVI、MOV、M4V、WMV、TS、M2TS、WEBM。
- 没有本地视频：仍可下载，用 `--name` 指定输出基本名、`--output-dir` 指定目标目录；不要创建假视频。
- 只有作品名：先搜索和核验候选，无需要求用户自己找 sid。条目标题、语言标签、适配版本和季集共同决定候选；分辨率或“整季”标签不能证明时间轴相同或全集齐全。

## 运行入口与环境检查

将 `<skill-dir>` 替换成当前读到的本 skill 文件夹绝对路径，所有路径加引号。不要依赖当前工作目录、作者的主目录、另一项 skill 或专属工具名。

```text
python3 "<skill-dir>/scripts/download_subtitle.py" --doctor
python3 "<skill-dir>/scripts/download_subtitle.py" --help
```

Windows 按实际安装使用 `py -3` 或 `python` 替代 `python3`。Python 3.9+、curl 为运行依赖；ZIP 和直接字幕无需 pip 包，7z/RAR 按需安装 7-Zip。`--doctor` 只检查本地可执行程序，不代表已联网成功。依赖缺失时给出对应平台的安装方式，不擅自安装全局软件。

## 搜索与选择

```text
python3 "<skill-dir>/scripts/download_subtitle.py" --search "作品名称 年份 S01"
```

命令返回 JSON 候选（标题、sid、条目 URL），不自动下载第一条。必要时用环境中可用的浏览器查看候选 `/a/<sid>` 页中的说明及文件列表。搜索结果只证明存在候选，不证明归档已包含所需字幕。

HTTP 403、登录页、验证码或搜索解析失败时，用浏览器正常访问进行核查；需要用户验证时说明具体障碍。不要把 HTML 当字幕、反复请求绕过限制，或声称脚本已下载成功。

## 下载与配对

```text
# 电影或单集（--mkv 仍作为 --video 的兼容别名）
python3 "<skill-dir>/scripts/download_subtitle.py" --sid "<sid 或 SubHD 条目 URL>" --video "/media/Show.S01E01.mp4" --language chs-eng

# 一个整季包，下载一次并按真实季集号逐个匹配
python3 "<skill-dir>/scripts/download_subtitle.py" --sid "<sid>" --dir "/media/Show Season 1" --season 1 --language chs --report "/outputs/new-report.json"

# 没有视频，只下载选中的电影/单集字幕
python3 "<skill-dir>/scripts/download_subtitle.py" --sid "<sid>" --name "Movie.2025" --output-dir "/outputs/subtitles" --language eng
```

同条件候选不唯一时，核验归档候选列表后可用 `--select-file "Eng/Show.S01E01.srt"` 指定包内相对路径；仍会检查季集、语言和格式，不用于绕过匹配。归档将语言标记放在子目录里时也会读取该标记。

语言值：`chs-eng`（默认简英）、`cht-eng`（繁英）、`chs`、`cht`、`zh-eng`（未区分简繁的中英）、`zh`（未区分简繁的中文）、`eng`、`any`。脚本根据文件名语言标记筛选，不能保证翻译质量；无标记时先核验，再按用户意图使用 `any`，不要把未知语言报告成指定语言。

`--format auto` 按内容识别格式并优先 SRT；可指定 `srt/ass/ssa/vtt`，这是筛选，不是格式转换。源编码默认识别 UTF BOM、UTF-8、GB18030；已知其他编码用 `--encoding`，例如 `big5`。不会用替换字符掩盖解码失败。

整季包使用完整的季集号匹配，支持 `S01E01`、`S1E1`、`1x01`。缺集、跨季或同条件候选不唯一时保留失败项；不要把 E10/E18 改名成 E01。没有季集标记、合并集或非标准命名需人工明确对应关系，不能靠排序猜测。单集 sid 中唯一且没有季集标记的字幕仅可在已核验条目对应关系后使用。

旧 `--dir + --sids A,B,C` 保留：默认对应同一季 E01 起的连续集号，用 `--start-episode` 改起始集，多季目录用 `--season`。非连续任务分别使用显式的 `--sid + --video`。目录按原路径访问，默认不递归，按需加 `--recursive`。

## 文件与请求边界

- 同名同内容返回 `unchanged`；不同内容已有文件返回 `skipped_existing`。只有用户明确要求替换时添加 `--overwrite`，否则保留已有字幕。
- 默认在视频旁写入；`--output-dir` 可改位置。远程/NAS 路径遵守当前环境的访问权限；失败时说明路径和权限问题，不改沙箱配置或绕过文件删除保护。
- 文件头和结构决定字幕/归档格式，ZIP 用标准库解压，7z/RAR 用可用的 7z/7zz。拒绝 HTML、路径越界、链接和加密归档；下载与解压有大小限制。临时文件及 cookie 自动清理，不记录 token 或签名下载地址。
- HTTPS 默认验证证书。TLS 错误先检查时间、代理和证书；仅在用户明确接受本次跳过证书验证时使用 `--insecure`。
- 多 sid 默认间隔 45 秒；明确限流默认冷却 120 秒后重启整条下载链一次。403、验证码和下载许可未通过会报告失败；这些间隔是保守默认值，并非站点承诺。

下载协议细节、异常和平台依赖说明见 [references/subhd-protocol.md](references/subhd-protocol.md)，仅在排障或调整实现时阅读。

## 验证与交付

以脚本实际结果和磁盘文件为准。报告成功/保留/缺集/失败数量、输出路径和候选来源；有报告文件就提供链接。`--report` 要求新路径，避免覆盖旧报告。退出码 `0` 表示没有失败项（可能包含保留已有文件），`1` 表示部分或全部下载/匹配失败，`2` 表示参数、依赖或执行错误，`130` 表示用户中断。

检查输出文件实际格式和编码、季集对应关系；下载成功不能证明视频时间轴已同步。字幕来自第三方，匹配版本后仍可能需要用户播放检查。仅按用户请求取得字幕，版权与使用权限由相应权利方决定。
