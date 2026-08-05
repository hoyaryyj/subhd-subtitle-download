---
title: "subhd 字幕下载"
summary: "从 subhd.cc 下载中英双语字幕，改名匹配视频文件名实现播放器自动加载"
agent_created: true
read_when:
  - 需要下载美剧/电影的中英双语字幕
  - 字幕要匹配视频文件名以便播放器自动加载
  - 当用户报告从 subhd 直接下载字幕失败、返回 HTML 而非文件时
---

# subhd.cc 字幕下载流程

subhd.cc 近两年改为「内联 + 短时效 token」模式：直接访问 `/a/XXXX` 下载页只返回 HTML，
真正文件要走一段 API 链。token 几秒内失效，步骤 2→3 之间不能停顿，且必须在同一会话(cookie jar)内。

> 仅供个人学习使用，商用需获版权方授权。

## 关键坑（必看）

1. **必须用 curl，不要用 Python urllib**：subhd 走 Cloudflare，对 Python `urllib` 的 TLS 指纹直接返回
   `403 Forbidden`，用 curl（或浏览器）则正常。**整条链所有请求（prepare-download / `/down/` / `/api/sub/down` / `dl.subhd.me` 文件拉取）统一用 `subprocess` 调 curl**，cookie jar 贯穿一次下载。
2. **归档可能是 .7z 也可能是 .zip**，而且**里面常套一层子目录**。解压首选 `tar -xf file`
   （macOS 自带 bsdtar 通常通吃 7z 和 zip）；若报 `Unrecognized archive format`，说明当前 bsdtar 未编入 7z 支持，
   fallback：`which 7z || which 7zz`，没有则 `brew install 7zip`，再用 `7z x file -o<outdir>`。
   解压后用 `os.walk` 递归找文件，别用 `os.listdir`。
3. **双语 SRT 命名不统一**：7z 里常是 `..chs&eng.srt`（简中+英），zip 里常是 `简体&英文.srt`
   （或 `繁体&英文.srt`）。选 Simplified+English 的 `.srt`：`('简体' in f or 'chs' in f.lower()) and ('英文' in f or 'eng' in f.lower())`，
   并排除 `cht`/`繁体`。
4. **部分文件是 UTF-16（LE BOM `ff fe`）**：直接拷贝某些播放器会乱码。落盘前转 UTF-8：
   `ff fe`→`utf-16-le`，`ef bb bf`→`utf-8-sig`，否则 `utf-8`；写出用 `utf-8-sig`（带 BOM）最稳。
5. **限流**：短时间多次下载会触发 `{"msg":"下载频率过高，请稍后再试。"}`（HTTP 200 但 success=false）。
   处理：先停手冷却 2~3 分钟，再每集间隔 ~45s；命中限流时退避 45s 重试。批量别用 25s 以内间隔。
6. **目标文件名别拼出双点**：video basename 形如 `The.Pitt.S02E02.8.00.A.M.1080p...`，`A.M.`/`P.M.` 末尾**已带点**。
   若 `title` 变量含末尾点又拼 `.1080p`，会得到 `8.00.A.M..1080p`（双点），与视频不匹配、播放器不自动加载。
   修法（最稳）：**直接从同目录的 `.mkv` 文件名派生目标 srt 名** —— `dst = mkv_basename + ".srt"`，完全跳过手工拼 title，从根上杜绝双点。
   兜底通用修正：`re.sub(r'\.+', '.', filename)` 处理任意连续多点（不局限于 1080p）。
7. **访问 SMB/网络挂载需告知用户放开沙箱**：`/Volumes/...` 这类网络挂载在 Bash 默认沙箱下 `ls` 会被 SIGKILL 杀死（退出码 137）。
   **不要默认加 `dangerouslyDisableSandbox=true`**——这是高风险操作。正确流程：
   ① 先 `ls /Volumes` 列出挂载点（避开 stale 挂载如断开的旧 `TV`，遍历会卡死）；
   ② 向用户说明「需要读写 `/Volumes/xxx`，沙箱限制阻止了访问」；
   ③ 由用户确认后再加 `dangerouslyDisableSandbox=true`，并仅用于已确认的合法挂载点。
8. **含中文/空格/括号的路径用 glob 定位**：剧集目录如 `The Pitt (2025) Season 2 S02 (1080p ... Vyndros)` 手工拼字符串极易漏括号（曾漏最后 `)` 导致 No such file）。改用 `glob.glob('/Volumes/新加卷/The Pitt (2025) Season 2 S02*')[0]` 让系统匹配真实名最稳；也不要用 `ls -a` 碰 `.timemachine` 等隐藏项（会把 SMB 挂载拖卡）。
9. **长任务优先前台跑**：`run_in_background` 起的下载进程，若长时间无输出或跨会话上下文切换，`TaskOutput` 可能报 "not found" 或拿不到完整结果。**对策**：
   ① 整季等跨分钟任务直接前台跑，给 Bash 工具设大 `timeout`（如 `900000`=15 分钟）让它在本轮内跑完；
   ② 若必须用后台，需周期性 `TaskOutput` 探活，并把 `task_id` 写入临时文件持久化；
   ③ 前台期间若遇限流仍按坑 5 退避即可。
10. **归档实际格式可能与页面标签不符，落盘前必须按文件头嗅探定后缀**：subhd 条目标签写的 `SRT`/`ASS` 不保证等于归档内真实格式——实测某整季合集标签写「SRT ASS」但归档里**只有 .ass**。若脚本无脑把内容写成 `.srt` 后缀，文件头却是 `[Script Info]`，部分播放器会解析失败或丢样式。**正确做法**：解压后读文件头前 32 字节——含 `[Script Info]` → ASS，以序号 `1\n` 开头或含 `00:00:01,000 -->` 时间轴 → SRT；按真实格式选 `.ass`/`.srt` 后缀。`download_one` 落盘时据此决定后缀，不要写死 `.srt`。
11. **exit 35（TLS 握手失败）不止出现在步骤 4，全链路都可能撞上**：原版只对步骤 4（拉文件）做 `-k` 降级，实测步骤 3（`POST /api/sub/down`）也会 exit 35（`SSL_ERROR_SYSCALL`）。**修法**：把 `curl` 包装函数改成「exit 35 且未加 `-k` 时自动降级重试一次」，覆盖所有步骤，避免每步单独写降级分支。
12. **整季合集优先 + 本地命名格式兼容**：
    - **整季合集识别**：搜索结果里标题**只含 `.S01.` 不含 `E0x`** 的条目（如 `Memory.of.a.Killer.S01.2160p.STAN.WEB-DL...`）通常是整季打包，一个 sid 解压即得全部集字幕，比逐集下 N 个 sid 高效得多（还省限流配额）。优先选它；归档内文件名通常带 `S01E0x`，按集号匹配本地即可。
    - **本地命名格式多样**：`find_mkv_for_episode` 原只匹配 `S\d{2}E\d{2}`，但发布组常用 `1x01`/`S1E1` 格式（如 Pir8 组的 `Memory.Of.A.Killer.1x01.Pilot...mkv`），会匹配失败。**修法**：匹配正则扩展为 `[Ss]\d{1,2}[Ee]\d{2}|\d{1,2}[xX]\d{2}`，覆盖 `S01E01`/`S1E1`/`1x01` 三种。
    - **subhd 的 sid 已改为 6 位短哈希**（如 `Xn5QKb`，字母数字混合），不再是纯数字。搜索页解析用 `/a/([A-Za-z0-9]{6,})'`，API body `{"sid":"Xn5QKb"}` 直接传字符串，无需转数字。
13. **步骤 4 拉到的可能是「直接字幕文件」而非归档（务必先嗅探，别无脑解压）**：`/api/sub/down` 返回的 `url` 不总是 `.zip`/`.7z`，很多条目（尤其 `.chs` 单文件档）直接是 `https://dl.subhd.me/.../xxxx.srt`（或 `.ass`），内容就是纯字幕（如 `1\n00:00:00,090 --> ...` 或 `[Script Info]`）。若脚本无脑 `tar -xf` 会报 `Unrecognized archive format`，py7zr 会报 `not a 7z file`。**修法**：步骤 4 落盘后先读文件头前 64 字节嗅探——① URL 以 `.srt`/`.ass` 结尾，或 ② BOM (`ef bb bf` / `ff fe`) 后跟 `数字\n时间轴`，或 ③ 以 `[Script Info]` 开头——任一命中即判定为直接字幕，直接转码落盘，跳过解压。2026-08-04 Matlock S01 实测 E10/E11/E12/E13/E14/E16 等 6 集均为直接 `.srt`，无此嗅探会全部失败。
14. **整季打包条目常名不副实 + 早期集无独立条目 + 集号正则陷阱**：
    - **整季打包可能缺你要的集**：标题写「Season 1 S01 / 整季」的条目（如 `tSseqy`）实测只含 E04/E13/E14/E18/E19，**根本没 E01**。下载解压后**必须按真实集号核验归档内文件**，找不到目标集就如实报告，绝不许把别的集（如 E18）错存成目标集名。
    - **早期集（如 S01E01）在 subhd 搜索常无独立条目**：直接搜 `S01E01` 可能只返回「字幕组」发布页（如 擦枪字幕组 `GkaPyU`）。进该 `/a/<sid>` 页，文件列表里含每集多语言档（`Chs`/`ChsEng`/`Cht`/`Eng` × `srt`/`ass`），按集号+语言挑即可。
    - **集号正则必须精确**：匹配 S01E01 用 `[Ss]0?1[Ee]01`（集号两位 `01`）；**禁用** `[Ss]0?1[Ee]0?1`——后者会把 E10–E19 的十位 `1` 误判成 E01，导致张冠李戴（曾把 E18 内容存成 E01 文件名）。
    - **落盘前先比对目录已有同名 srt 的 md5**：若与 subhd 下载结果完全一致，说明本来就有正确字幕，**无需覆盖**（也避免误删/误写）；仅当确实缺失或需换语言档时才落盘。
    - **SMB/网络挂载上删除文件**：`rm` 被 genie-trash 的 `DYLD` 拦截器拦死（移入废纸篓在 SMB 上失败→拒绝删除，连 `command rm`/`\rm` 都逃不掉）。绕过：`DYLD_INSERT_LIBRARIES= /bin/rm -f <file>`（仍需用户确认 + 沙箱放行，且仅限已确认的合法挂载点）。

## 下载链（同一会话顺序执行，所有请求统一用 curl）

> cookie jar 用 `mktemp` 生成临时路径，下载完成后 `rm -f` 清理，避免遗留 session token。

1. `POST https://www.subhd.cc/api/sub/prepare-download`  body `{"sid":"XXXXXX"}`
   → `{"success":true,"url":"/down/XXXXXX"}`（若返回「频率过高」说明被限流，需冷却）
2. **立刻** `GET https://www.subhd.cc/down/XXXXXX`（激活 token，必须返回 200；单独访问会 403 失效）
3. `POST https://www.subhd.cc/api/sub/down`  body `{"sid":"XXXXXX"}`
   → `{"success":true,"pass":true,"url":"https://dl.subhd.me/.../.7z 或 .zip"}`
   - **必须校验 `pass==true`**：若 `pass` 为 false（可能下载配额未通过），按坑 5 退避 45s 重试一次；仍 false 则记录并跳过该 sid。
4. `GET` 上面的文件 URL。
   - **默认不带 `-k`**，先正常 TLS 握手。
   - 仅当出现 curl exit 35（TLS 握手失败）时降级加 `-k` 跳过证书校验，**并在日志中明确告警「本次跳过 TLS 校验，URL 来自 subhd 上游」**。
   - 推荐加 `--retry 5 --retry-delay 4` 抗抖动。

调用时带上 UA、`Referer`、`X-Requested-With: XMLHttpRequest`，POST 的 `Content-Type: application/json`。

## 选档与命名

- 选 `简体&英文`/`chs&eng` 的 `.srt`（大陆用户默认简中+英）。
- 改名成**与视频完全相同 basename** + `.srt`（如 `xxx.Vyndros.mkv` → `xxx.Vyndros.srt`），
  播放器(VLC/Infuse/IINA/Plex/Kodi)即自动加载，无需手动选择。
- subhd 的 srt 可能带 `{\an8\fs14}` 这类 ASS 样式标签，主流播放器正常解析。

## 找 sid

- **搜索路由**：`GET https://www.subhd.cc/search/<剧名>`（URL 编码，不是 `?q=`；`?q=` 会 404）。搜索结果页**直接就是 `/a/<sid>` 字幕条目**（不是 `/d/<id>` 剧集页），每条带标题与语言标签，无需再进二级页。
- 想要 Amazon WEB-DL 双语就挑标题含「Amazon」「双语」的条目（HBOMax 条时间轴可能不同，优先 Amazon）。subhd 的「Bilingual版本 / 双语」条目通常含简体+繁体+英文，脚本默认选「简体&英文」组合 srt。
- **批量整季场景**：某集在 subhd 找不到对应 sid 是常态，应跳过+记录到失败列表，不要中断整批流程。

## 进度反馈与错误处理

- **批量下载时务必 `print` 进度**：每集开始/完成/失败都打印 `[N/Total] xxx ✓` / `✗ 原因`，避免长时间无输出让用户以为卡死。
- **错误处理模型**：
  - HTTP 非 200 → 记录状态码 + 响应体前 200 字，跳过该集；
  - JSON `success=false` → 按返回 `msg` 区分：限流按坑 5 退避；其他原因记录后跳过；
  - `pass==false` → 见步骤 3；
  - 解压后找不到匹配 srt → 记录归档内文件列表，跳过；
  - 网络异常（curl exit 7/28/35）→ 重试 3 次后跳过。

## 参考实现

完整可执行的下载脚本见 `scripts/download_subtitle.py`，支持单集/整季批量下载、自动选档、UTF-8 转码、限流退避、失败记录。直接调用：

```bash
# 单集
python3 scripts/download_subtitle.py --sid 123456 --mkv "/path/to/Show.S01E01.mkv"

# 整季（sid 按集顺序逗号分隔）
python3 scripts/download_subtitle.py --dir "/path/to/Show Season 1" --sids 111,222,333
```
