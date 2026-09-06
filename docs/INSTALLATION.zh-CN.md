# Windows 离线安装说明

## 选择基础安装包

- **标准版（Standard）**：适合大多数电脑。内置 Python、FFmpeg、英译中/中译英快速离线翻译模型和字幕字体；体积更小。安装时可勾选需要的 Whisper、本地 AI 与 AI 超分辨率组件，未勾选的组件不会下载或安装。
- **完整版（Complete）**：在标准版基础上增加本地 Ollama 与 Qwen3:4b，用于更自然的本地段落翻译；适合无网络安装，需要更多磁盘空间和内存。

两个基础安装包都不再强制携带 Whisper 语音识别模型。安装基础程序后，请按电脑配置安装一个模型包：

- **Whisper Small**：大多数电脑推荐，速度较快，对内存和显存要求较低。
- **Whisper Medium**：识别质量更高，但需要更多内存、显存、磁盘空间与处理时间。

程序在开始字幕任务前会检测模型包。没有模型时会明确提示安装 Small 或 Medium，不会在后台偷偷下载。

## 安装顺序

1. 在 `dist` 中选择同一版本的 Standard 或 Complete 基础安装包。
2. Standard 只需下载一个 `.exe`。Complete 和独立模型包仍需把 `.exe` 与其全部同名 `.bin` 分卷放在同一个文件夹，不要重命名或遗漏分卷。
3. 双击 Standard 时，在“选择本地模型”页按需勾选 Whisper Small、Whisper Medium、Local AI 或 AI 超分辨率；全新安装默认选择 Small，已有 Whisper 的升级不会重复选择。安装器会从前三段兼容的组件 Release 下载所选内容并校验 SHA-256，例如程序 `0.7.0.14` 使用 `0.7.0` 组件包。主动取消全部 Whisper 时会确认仅安装基础下载功能。
4. 若还没有 Whisper，选择一个模型安装包：`Whisper-Small-Model-Setup.exe` 或 `Whisper-Medium-Model-Setup.exe`。
5. 把模型包安装到与基础程序相同的文件夹；默认位置通常正确。需要本地 AI 而未使用 Complete 时，安装前三段兼容版本的 `Local-AI-Model-Setup.exe` 与全部分卷。
6. 从开始菜单或桌面打开 **YouTube Chinese Localizer**。

AI 超分辨率组件是约 30 MiB 的独立安装包，包含通用实拍和动画两套本地模型。它不需要 API；不开启超分时不会重新编码无字幕下载。开启后耗时和功耗会明显增加，程序会自动探测可用的 NVIDIA、AMD 或 Intel Vulkan 设备，并以有限批次运行。

Small 与 Medium 可以同时安装，软件会优先按你选择的处理方式和当前硬件使用合适的模型。只下载无字幕视频时不需要 Whisper 模型。

不要把 Complete 或不同模型包的 `.bin` 分卷混在一起。安装包未做代码签名时，Windows 可能显示来源提示；请只运行来自项目官方发布页并已核对哈希的文件。

## 本地 AI 大模型提示

“本地 AI 段落翻译”除 Whisper 外还需要 Qwen3:4b 大语言模型：

- 安装 **完整版**：Qwen3:4b 已内置，不需要 API Key，也不需要另行下载大语言模型。
- 安装 **标准版**：安装时勾选“本地 AI 段落翻译”，或之后安装前三段兼容的 `Local-AI-Model-Setup.exe`，即可使用 Qwen3:4b；未安装时仍可直接使用快速离线翻译。

## 校验完整性

每个基础包和模型包都有对应的 SHA-256 清单。在 PowerShell 中进入文件夹后可运行：

```powershell
Get-FileHash .\YouTube-Chinese-Localizer-*-Setup* -Algorithm SHA256
```

将输出与对应 `SHA256SUMS-<版本>-*.txt` 清单逐行比较。安装基础程序后还可从开始菜单运行 **Verify YouTube Localizer Installation**，它会校验基础资产哈希、桌面界面和离线翻译模型。

## 开始任务前的自动预检

图形界面每次开始任务都会检查：

- 是否已安装适合的 Whisper 模型；
- 基础包层级和本地 AI 模型是否齐全；
- 开启 AI 超分辨率时是否安装了对应组件；
- 可用磁盘空间是否足够存放源视频、音频、字幕和最终高质量输出；
- NVIDIA 显存或 CPU 内存是否适合当前模型。

缺少模型或空间不足时，任务会在下载前停止，并把详细结果写到项目目录的 `logs/preflight.json`。命令行可提前执行：

```powershell
python main.py preflight "D:\Videos\已授权的视频.mp4"
```

## 建议

- 追求最小下载量：安装标准版 + Whisper Small。
- 追求无 API 的自然段落翻译：安装 Standard 时勾选 Local AI + Whisper Small 或 Medium；无网络时使用 Complete + Whisper。
- 长视频尽量输出到本机 SSD，而不是 OneDrive 同步目录或移动硬盘。
- 多视频队列会根据逻辑 CPU 和系统内存自动启用 1–4 个下载／预处理任务；AI 与压制仍会安全排队，无需手动选择“性能模式”。
- 仅下载无字幕最高质量视频时，选择“无字幕直接下载”；这不需要识别、翻译或重新编码。
