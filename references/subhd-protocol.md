# SubHD 协议与排障

## 下载链

默认站点 `https://www.subhd.cc`。sid 为字母数字字符串，也兼容旧数字 ID；使用页面实际 `/a/<sid>` 地址，不猜长度或转换为整数。

1. 同一 cookie jar：`POST /api/sub/prepare-download`，JSON `{"sid":"<sid>"}`。
2. 校验 JSON `success` 严格为 `true`，把返回 `/down/...` 路径接到站点，立即 GET 激活。
3. 紧接着 `POST /api/sub/down`，同一 JSON sid；校验 `success === true`、`pass === true` 和下载地址。
4. 用 curl GET 下载文件，先检查真实字节，再按格式处理。跳转限于 HTTPS，初始激活地址限于 SubHD，初始文件地址接受 `subhd.cc` 或其 `*.subhd.me` 文件节点。新增站点域名需从官方页面/API 核实后调整，不直接信任任意域名。

各请求带 UA、Referer；API POST 还带 `Content-Type: application/json` 与 `X-Requested-With: XMLHttpRequest`。所有请求用 curl；POST 不在传输层自动重放，明确限流后从 prepare 重新开始。token 视为短时效，不在激活和 down 间等待用户回复。脚本不从其他浏览器复制 cookie 或登录凭据。

2026-10-08 的实时检查：主页/搜索仍有 `/a/<sid>` 条目；官方页面脚本仍调用 `/api/sub/prepare-download`；一次 prepare→激活→down 返回的文件节点为 `dlus.subhd.me`。这解释了旧版只写 `dl.subhd.me` 的局限。下载链、可访问性和节点可能变化，以本次请求结果为准。

官方来源：[SubHD](https://www.subhd.cc)、[页面脚本](https://www.subhd.cc/public/js/subhd.js)。

## 依赖

| 平台 | Python / curl | 遇到 7z/RAR 时 |
| --- | --- | --- |
| macOS | 安装 Python 3.9+；系统通常有 curl | 有 Homebrew 时 `brew install sevenzip`，确认 `7zz` 在 PATH |
| Linux | 安装 Python 3.9+ 与 curl | 发行版提供的 `7zip` 或 `p7zip-full`，确认 `7z/7zz` 可运行 |
| Windows | 安装 Python 3.9+，使用 `py -3` 或 `python`；Windows 10/11 通常有 curl.exe | 安装 [7-Zip](https://www.7-zip.org/)，将 7z.exe 目录加入 PATH 后重新打开终端 |

无需 `pip install`。ZIP 用 `zipfile`，临时目录用 `tempfile`，清理不依赖 shell `rm/which/tar`。Python 本身的安装路径和包管理器由目标环境决定，不照搬作者机器。

## 常见失败

| 现象 | 操作 |
| --- | --- |
| curl 非零、HTTP 4xx/5xx | 依据状态码处理；退出 22 为 HTTP 错误，不是文件已成功下载 |
| 返回 HTML/非 JSON | 停止自动下载，浏览器检查验证或 API 改动 |
| `pass` 不为 `true` | 报告许可/配额/验证未通过，不把结果当文件地址 |
| 明确限流 | 默认退避 120 秒一次；可配置 `--cooldown` / `--rate-retries 0..3`，不无限重试 |
| curl 35/60 | 检查 TLS、系统时间、代理/证书；不自动加 `-k` |
| 无语言标记 | 核验条目/文件预览；确需任意语言时显式 `--language any` |
| 整季包缺集 | 报告实际缺失，寻找对应单集条目，不复用其他集 |
| 同集同语言多个候选 | 检查发布版本或选择更明确的 sid/格式；不要取第一个 |
| 7z/RAR 缺解压器 | 按平台安装 7-Zip；缺少时 ZIP/直接字幕仍可使用 |
| 网络挂载无权限/不可访问 | 核查实际挂载和当前工具权限，按工具的授权流程处理 |

## 内容与限制

支持直接 SRT/ASS/SSA/VTT、ZIP、7z、RAR；按内容识别，错误文件名后缀不会迫使脚本改变格式。解压后递归查找字幕，拒绝越界路径、链接和加密内容。当前限制：下载 64 MiB，解压总计 256 MiB、2000 个成员。7-Zip 在运行前列出成员并检查路径/大小。

语言标记是筛选线索，脚本不把翻译质量或双语行数当作已验证事实。UTF-8、UTF-16 LE/BE、UTF-32 BOM 和 GB18030 可识别，输出 UTF-8 BOM。SRT/ASS 都保留原文本，不清理样式、不做时间轴调整。来源页标签和“整季”标题不能替代解压后的核验。
