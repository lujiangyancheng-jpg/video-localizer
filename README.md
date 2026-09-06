# Video Localizer

<div align="center">

<img src="assets/branding/app-icon.png" alt="Video Localizer / Localize Studio" width="112">

### 把授权视频变成可交付的多语言成片

**下载、语音识别、自然段落翻译、字幕预览与压制，在一套 Windows 桌面工作流里完成。**

[![Release](https://img.shields.io/github/v/release/lujiangyancheng-jpg/video-localizer?display_name=tag&label=%E7%A8%B3%E5%AE%9A%E7%89%88)](https://github.com/lujiangyancheng-jpg/video-localizer/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/lujiangyancheng-jpg/video-localizer/total?label=%E4%B8%8B%E8%BD%BD)](https://github.com/lujiangyancheng-jpg/video-localizer/releases)
[![Windows quality gate](https://github.com/lujiangyancheng-jpg/video-localizer/actions/workflows/ci.yml/badge.svg)](https://github.com/lujiangyancheng-jpg/video-localizer/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/lujiangyancheng-jpg/video-localizer)](LICENSE)

[**下载 v0.7.0.14 Standard 正式版**](https://github.com/lujiangyancheng-jpg/video-localizer/releases/download/v0.7.0.14/YouTube-Chinese-Localizer-0.7.0.14-Standard-Offline-Setup.exe)
· [使用说明](docs/USER_GUIDE.zh-CN.md)
· [参加测试](docs/TESTING_GUIDE.zh-CN.md)
· [讨论与求助](https://github.com/lujiangyancheng-jpg/video-localizer/discussions)

</div>

> Video Localizer 是项目名，安装后的桌面程序名为 **Localize Studio**。当前主要支持 Windows 10/11。

> 仅处理你拥有、已获授权、属于公共领域或许可允许本次用途的视频。软件不会绕过 DRM、登录、付费、地区或平台访问限制。

## 30 秒了解它

| 输入 | 本地处理 | 输出 |
| --- | --- | --- |
| YouTube 公开视频 | 最高质量下载、Whisper 识别、翻译、字幕质检 | 无字幕原片、SRT、ASS、硬字幕或软字幕 MP4 |
| 获得授权的公开播放页或媒体直链 | 自动解析；动态页面可在隔离 Edge 窗口中捕获播放器公开媒体 URL | 可恢复的本地项目与最终成片 |
| 本地视频 | 不上传视频；自动选择 NVIDIA、Intel、AMD 或 CPU 安全方案 | 保持原始画质/帧率，或可选本地 AI 超分辨率 |

- 中英快速离线翻译不需要 API；本地 AI 可提供更自然的段落翻译和更多语种。
- 任务可暂停、继续和断点续跑，重启电脑后仍能恢复；API Key 不会写入设置或队列文件。
- 按电脑的显存、内存、CPU、磁盘和编码器能力自动调度，低配置优先稳定，高配置允许安全并行。
- 每个项目生成版权依据与署名记录；版权记录是发布前核对工具，不会替用户判断授权是否合法。

## 三步开始

1. 下载单文件 [Standard Setup.exe](https://github.com/lujiangyancheng-jpg/video-localizer/releases/download/v0.7.0.14/YouTube-Chinese-Localizer-0.7.0.14-Standard-Offline-Setup.exe)。
2. 安装时按需选择 Whisper Small、Whisper Medium、Local AI 和 AI 超分辨率。全新安装默认勾选 Whisper Small；只下载勾选的组件，已有组件升级时不会重复下载。
3. 打开 **Localize Studio**，粘贴一个或多个链接，确认预分析信息后点击“开始本地化”。

| 目标 | 推荐组件 | 是否需要 API |
| --- | --- | --- |
| 最高画质无字幕下载 | Standard | 不需要 |
| 英文 ↔ 简体中文字幕 | Standard + Whisper Small | 不需要 |
| 更自然的中英字幕 | Standard + Whisper + Local AI | 不需要 |
| 日、韩、西、法、德、葡、俄、阿字幕 | Whisper + Local AI，或兼容 API | 两种方案任选 |

**Standard v0.7.0.14 正式版基础包约 289 MiB**，内含程序、Python、精简 FFmpeg、字幕字体、硬件编码支持和两套中英快速翻译模型。Whisper、Local AI 与 AI 超分辨率是可选包；超分组件不塞进基础包。前三段版本相同的程序共用模型，例如 `0.7.0.14` 继续使用 `0.7.0` 组件包。

当前 Windows 安装包尚未使用商业代码签名证书，因此浏览器或 SmartScreen 可能显示来源提示。请只从本仓库的 GitHub Release 下载，并核对 Release 同页提供的 SHA-256。

> 从 `v0.7.0.9` 或更早版本升级：GitHub 网页、Git 和 Release 链接会自动跳转到新仓库，但旧程序的更新检查不会跟随 GitHub API 的 `301` 重定向。如果界面显示“暂时无法检查更新”，请手动安装一次当前版本；现有模型和项目会保留。

## 我们正在招募测试者

这个项目最需要的不是“帮忙点个 Star”，而是来自不同电脑和真实工作流的可复现反馈。即使不会写代码，也可以直接参与：

- **不同硬件**：NVIDIA / AMD / Intel 核显、纯 CPU、8–64 GiB 内存；
- **不同素材**：长短视频、中文或英文语音、4K/高帧率、本地文件与授权公开链接；
- **字幕质量**：术语、断句、翻译自然度、字幕速度和位置；
- **安装升级**：全新安装、模型选装、旧版升级、断网恢复与低磁盘空间；
- **易用性**：任何让你停下来猜“下一步该点哪里”的地方。

从 [15 分钟测试清单](docs/TESTING_GUIDE.zh-CN.md) 开始；发现问题后使用 [Bug 报告模板](https://github.com/lujiangyancheng-jpg/video-localizer/issues/new?template=bug_report.yml)。程序里的“导出诊断包”会生成不包含视频、字幕正文、链接、路径或凭据的脱敏信息，方便定位问题。

## 一起完善

- 想确认用法、分享测试结果或讨论方向：进入 [Discussions](https://github.com/lujiangyancheng-jpg/video-localizer/discussions)。
- 有明确故障或功能建议：创建 [Issue](https://github.com/lujiangyancheng-jpg/video-localizer/issues/new/choose)。
- 能修改代码、文档或测试：阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，从带有 `good first issue` 或 `help wanted` 标签的任务开始。
- 每一版做了什么：[CHANGELOG.md](CHANGELOG.md)；安装与模型说明：[docs/INSTALLATION.zh-CN.md](docs/INSTALLATION.zh-CN.md)。

适合视频学习者、独立创作者、课程/采访/播客编辑和需要可审计项目文件的本地化团队。不适合批量搬运、无人值守转载、规避平台限制，或处理未获授权的内容。

## 当前状态

`0.7.0.14` 修复超分输出帧率与旧结果误用，增加每 300 帧的超分断点恢复、速度与剩余时间显示，并补齐 Standard 可选模型完整性检查。项目仍处于积极开发期；新安装默认使用稳定更新通道，希望提前验证新功能的测试者可在应用内切换“开发”通道。已知限制、可复现命令和开发细节见下文。

---

## 面向开发者的详细说明

This production-oriented local Python application localizes authorized English- or Chinese-language
videos in either direction. Each reusable project contains normalized subtitles, optional bilingual
subtitles, and a validated hard-subtitled MP4. The pipeline is resumable, keeps intermediate files,
and never uploads content. YouTube caption tracks are never downloaded or consumed.

## Legal-use notice

Use this tool only for:

- videos you own;
- public-domain material;
- Creative Commons material whose license permits your intended use; or
- material for which you have explicit translation and redistribution permission.

It does not bypass DRM, paywalls, private-video controls, age gates, region/authentication
requirements, or platform access controls. You are responsible for verifying the license,
attribution requirements, and publishing-platform rules.

For non-YouTube sources, paste an authorized public page that explicitly declares its media in
HTML5 `<video>`/`<source>`, Open Graph, Twitter Card, or VideoObject JSON-LD, or paste the public
direct media URL (`.mp4`, `.webm`, `.mov`, `.mkv`, `.m3u8`, `.mpd`, or an explicitly typed
extensionless response). The resolver does not execute or deobfuscate scripts, traverse third-party
iframes, copy browser cookies, solve anti-bot challenges, or bypass login, paywall, region, or DRM
controls. For a dynamic page, the desktop UI can instead open Microsoft Edge with a fresh temporary
profile and observe the public HTTP(S) URL selected by the player after the user starts playback.
This browser-assisted flow doesn't attach to the normal Edge profile or retain cookies, credentials,
request headers, response bodies, or DRM data; any browser challenge must be completed by the user.

## Phase 1 capabilities

- Public YouTube metadata inspection with `yt-dlp` before download
- Bounded public-page media resolution from standard declarative HTML metadata
- Isolated Edge-assisted capture of the public media URL actually selected by a dynamic player
- Asynchronous per-video pre-analysis and a visual task centre with independent status/progress
- Highest-quality video plus audio direct-download mode with no subtitle processing
- Optional bounded-batch, cross-vendor NCNN/Vulkan video super resolution for footage or animation
- Consistent local English or Chinese transcription without YouTube caption requests
- Local video validation and safe copying
- VTT/SRT/ASS parsing and normalized UTF-8 SRT output
- Rolling-caption overlap cleanup and conservative English cleanup
- `faster-whisper` English or Chinese transcription with VAD and word timestamps
- CUDA runtime preflight and bundled-runtime discovery for reliable GPU Whisper acceleration
- Responsiveness-preserving CPU Whisper fallback (six threads by default)
- Manual Markdown/JSONL translation chunks with strict cue/timestamp validation
- Local offline English↔Simplified Chinese translation with directional model selection and caching
- OpenAI-compatible subtitle translation with retries and deterministic response caching
- English or Simplified Chinese target SRT and styled ASS output
- Target-only, English-above-Chinese, and Chinese-above-English subtitle modes
- H.264/AAC hard-subtitled MP4 rendering with verified NVIDIA, Intel Quick Sync, AMD AMF, or CPU fallback
- Selectable soft-subtitle MP4 output without recompressing the source video or audio
- Preview rendering and output stream/duration/decode validation
- Final-target subtitle quality report for fast-reading, duplicate, flash, and line-length cues
- Atomic state/report writes and hash-based resume decisions
- Structured JSONL file logs plus readable console output
- Conservative publishing metadata drafts marked for human review
- Per-project user-supplied rights declarations and ready-to-copy attribution records
- Offline unit tests and an FFmpeg synthetic-video integration test

Advanced retiming, thumbnail overlays, and polished platform-specific metadata generation remain
future enhancements. Timestamps are deliberately not changed in Phase 1.

## Who this is for

- Learners who want a private, offline way to understand English YouTube videos with Chinese
  subtitles.
- Creators and editors working with videos they own or are authorized to localize, who need
  Chinese, English, bilingual, hard-subtitle, and selectable-subtitle deliverables.
- Course, interview, and podcast editors who need an auditable project folder, a terminology
  glossary, resumable processing, and focused subtitle-review flags.

It is not intended for mass unattended redistribution, bypassing platform restrictions, or use
with material that you are not authorized to translate or publish.

## Windows quick start

For a normal Windows installation, download and run the single Standard setup `.exe`. Complete
and optional model packs are split sets, so keep their setup `.exe` and all adjacent `.bin` files
together. Choose the package that fits the computer and workflow:

- **Standard** includes its own Python, FFmpeg, both fast offline translation models, and
  subtitle fonts. Its installer can optionally download Small/Medium Whisper and Local AI
  model packs; unchecked models are not downloaded or installed.
- **Complete** additionally includes the Ollama runtime and Qwen3:4b for local paragraph-aware
  translation. It remains the fully offline choice for computers with sufficient disk space and
  memory.

The Standard installer can download one or more selected model packs automatically. For offline
installation, install the matching external Whisper pack afterward: **Small** is the recommended
balanced choice for most computers; **Medium** needs more RAM/VRAM but improves recognition
quality. The app detects installed model packs before a subtitle job and never silently downloads
a model at runtime.

Both packages stay offline on first use. Before every job, the app checks available storage,
installed package tier, GPU memory, and model availability, then selects a safe installed fallback
when necessary. See [installer/README.md](installer/README.md) for reproducible build
instructions. Chinese installation notes: [docs/INSTALLATION.zh-CN.md](docs/INSTALLATION.zh-CN.md).

The following steps are for running directly from a source checkout:

The recommended interpreter is Python 3.11, 3.12, or 3.13. Python 3.14 may run the core
application, but `faster-whisper`/CTranslate2 wheels may not yet be available for it.

1. Install [Python](https://www.python.org/downloads/windows/) and enable **Add Python to PATH**.
2. Install FFmpeg with one of these methods:

   ```powershell
   winget install Gyan.FFmpeg
   ```

   Or download an FFmpeg build that includes `ffprobe` and the `subtitles`/libass filter,
   extract it, and add its `bin` directory to `PATH`. Open a new terminal afterward.
   The application also detects `tools\ffmpeg\bin` inside the project, or explicit
   `FFMPEG_PATH` and `FFPROBE_PATH` environment variables.

3. In this project directory, create and activate a virtual environment:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   python -m pip install -e ".[transcription,offline-translation]"
   ```

4. Check the environment:

   ```powershell
   python main.py doctor
   ```

5. Process a video you are authorized to localize:

   ```powershell
   python main.py process "D:\Videos\owned-video.mp4"
   python main.py process "https://www.youtube.com/watch?v=VIDEO_ID"
   ```

   To inspect the effective offline model, hardware plan, and required temporary space before
   processing, run:

   ```powershell
   python main.py preflight "D:\Videos\owned-video.mp4"
   ```

The command-line default translation provider is `manual`. Add `--translation-provider
offline` for a no-API end-to-end run. The desktop interface defaults to offline translation.

## Smart acceleration and output controls

The desktop interface now uses one automatic high-quality path instead of asking users to choose
technical performance presets. Whisper uses NVIDIA CUDA only after a runtime check, then safely
uses CPU `int8` when CUDA is unavailable. Final rendering verifies NVIDIA NVENC (including the
bundled older-driver compatibility encoder), Intel Quick Sync, and AMD AMF before using one; it
uses CPU/libx264 only when none pass the real probe. The default is Medium Whisper plus the
highest final encode quality.

The desktop app keeps its helper command windows hidden and turns real pipeline messages into
progress: download percentage, local-AI paragraph count, and FFmpeg rendering percentage. It also
downloads up to four DASH/HLS fragments concurrently and groups more complete spoken paragraphs
for local AI, reducing model-request overhead without sacrificing context. The compatibility
encoder is selected only after a real probe succeeds, so supported older drivers retain GPU
rendering without changing driver versions; CPU encoding remains the safe fallback.

Completed projects also include `logs/subtitle_quality.json`. When only a few cues need attention,
`subtitles/review_required.srt` contains exactly the flagged timestamps for focused proofreading.

Source downloads remain ranked by resolution, then frame rate, bitrate, and file size. For the
hard-subtitled MP4, the settings panel offers only three clear output choices:

- **Output quality:** Highest (default), High/smaller file, or Standard/smaller file.
- **Output FPS:** Keep the source rate (default), cap at 60 FPS, or cap at 30 FPS. The app never
  creates fake frames by converting a 30 FPS source to 60 FPS.
- **Output resolution:** Keep the source dimensions (default), or cap at 4K, 1440p, 1080p, or
  720p. A lower-resolution source is never upscaled.

The equivalent CLI command is:

```powershell
python main.py process "D:\Videos\authorized-video.mp4" `
  --translation-provider offline --processing-profile auto `
  --output-quality best --output-fps 60 --output-height 2160
```

## Windows paste-a-link desktop interface

After completing the Windows installation above, double-click `Start Localizer.cmd` in the
project folder. The streamlined downloader-style interface makes **Paste Link** the primary
action, keeps the empty state distraction-free, and reveals the current task only after a video
is added. Processing settings, API fields, and the run log stay collapsed until needed. You can then:

1. paste an authorized public YouTube URL, declarative HTML5 media page, direct media URL, or select a local video;
2. choose an English/Chinese source and its target language;
3. choose target-only or bilingual subtitles;
4. confirm that you have the required rights or permission; and
5. click **开始本地化**.

The window streams progress from the existing resumable pipeline and provides a button for
opening the `output` folder. Closing or stopping a run keeps completed stages so the same
input can be resumed later. There is no YouTube-caption option: both directions always transcribe
the source audio with a locally installed Whisper model pack.

YouTube downloads default to the highest-resolution video stream and the best available audio
stream without restricting the source codec to MP4/M4A. Formats are ranked by resolution,
frame rate, bitrate, and file size; FFmpeg still produces the final hard-subtitled MP4. The
project dependency set includes Deno and `yt-dlp-ejs`; `python main.py doctor` must report
`yt-dlp JavaScript support: ok` so YouTube's complete format list can be discovered.

An authorized public playback page can be pasted in the same field when it explicitly publishes
the media through HTML5 `<video>`/`<source>`, Open Graph, Twitter Card, or VideoObject JSON-LD.
Resolution is read-only, limited to 2 MiB of HTML and five validated public redirects, and blocks
localhost/private-network targets. Pages that require Cloudflare/browser challenges, JavaScript
deobfuscation, cookies, login, iframe traversal, or DRM are reported as unsupported instead of
being bypassed.

An authorized direct media address can also be pasted. It may point to an actual
MP4/WebM/MOV/MKV file or HLS/DASH manifest (`.m3u8`/`.mpd`). An extensionless CDN URL is accepted
when its response identifies it as video or a playlist. The desktop's dedicated **Direct media**
dialog rejects truncated display text and known-expired epoch signatures before queueing. If a CDN
rejects `HEAD`, validation safely falls back to a streamed byte-range request without consuming the
media body. If a signed media URL
expires, paste its refreshed direct URL and keep **resume** enabled; the project is matched by its
stable media path rather than the temporary query string.

The desktop interface offers four translation modes:

- **Free/manual mode** downloads the video, obtains or transcribes source-language subtitles, and
  exports translation chunks. You translate and import those chunks before rendering.
- **Local fast mode** translates in the selected direction on this computer and continues
  through hard-subtitle rendering without an API. The offline installer includes both directional
  models. Rolling transcript fragments are grouped into complete paragraphs before translation;
  configured glossary terms are enforced.
- **Local AI paragraph mode** uses the bundled `qwen3:4b` and standalone Ollama runtime on
  a dedicated loopback port. It needs no API key and sends no subtitle text to a cloud service.
  This dedicated service prevents an existing system Ollama installation from hiding the bundled
  model. The model
  translates a complete spoken paragraph first; the application then segments the natural target
  paragraph at target-language punctuation and maps it across the source time range.
- **API automatic mode** continues through target-language translation and hard-subtitle
  rendering. It requires an OpenAI-compatible endpoint, model name, and API key. Values
  entered in the window are passed to the processing run. The API key is never saved;
  endpoint and model settings may be recorded in the local project's resolved configuration
  so an interrupted run can be resumed.

For Japanese, Korean, Spanish, French, German, Portuguese, Russian, and Arabic output, choose
**Local AI paragraph mode** (a capable local Ollama model is required) or **API automatic mode**.
The fast bundled models and the Chinese-English bilingual layouts intentionally remain limited to
English↔Simplified Chinese. Extra-language jobs create target-only subtitle files such as
`subtitles/es.srt`, `subtitles/es.ass`, and `rendered/es_hardsub.mp4`.

ChatGPT Plus cannot be used by the local program as an API and does not include API credits.
The interface can also be opened from PowerShell with:

```powershell
python main.py gui
```

The equivalent no-API command is:

```powershell
python main.py process "https://www.youtube.com/watch?v=VIDEO_ID" `
  --translation-provider offline

python main.py process "D:\Videos\authorized-Chinese-video.mp4" `
  --translation-direction zh-to-en --translation-provider offline

# Higher-quality local paragraph translation; no cloud API or API key.
python main.py process "https://www.youtube.com/watch?v=VIDEO_ID" `
  --translation-provider ollama

# Chinese speech to Spanish subtitles using a local Ollama model.
python main.py process "D:\Videos\authorized-Chinese-video.mp4" `
  --translation-direction zh-to-es --translation-provider ollama
```

The offline models are stored under
`~/.youtube-chinese-localizer/models/translate-en_zh-1_9` and
`~/.youtube-chinese-localizer/models/translate-zh_en-1_9`. They are derived from OPUS-MT and
distributed with CC-BY 4.0 model notices in their included `README.md`. Local model translation
is convenient and private, but names, jokes, and specialized terminology still need review.

## Manual ChatGPT translation workflow (no API billing)

**ChatGPT Plus does not include OpenAI API credits. OpenAI API usage is billed separately.**
If you do not want separate API billing, use this workflow:

1. Start processing:

   ```powershell
   python main.py process "D:\Videos\owned-video.mp4"
   ```

2. Open the printed project path, then locate
   `subtitles\translation_chunks\chunk_001.md`, `chunk_002.md`, and so on.
3. Upload one chunk to ChatGPT. The file already contains the strict translation rules.
4. Save ChatGPT's JSONL response as a UTF-8 text file. It must retain every `id`, `start`,
   `end`, and `en` field and fill every `zh` field.
5. Import each response:

   ```powershell
   python main.py translate-import "output\PROJECT_NAME" "D:\Translations\chunk_001.txt"
   python main.py translate-import "output\PROJECT_NAME" "D:\Translations\chunk_002.txt"
   ```

6. When the CLI reports all cues imported, render:

   ```powershell
   python main.py render "output\PROJECT_NAME"
   python main.py validate "output\PROJECT_NAME"
   ```

You can regenerate chunks at any time without calling an API:

```powershell
python main.py translate-export "output\PROJECT_NAME"
```

The importer rejects unknown/duplicate cue IDs, changed timestamps, changed English source
text, malformed JSON, and empty translations. Partial imports are saved and merged.

## OpenAI-compatible API workflow

Copy `.env.example` to `.env` for reference, but set secrets in the current environment or
your secret manager. The application never logs the API key.

```powershell
$env:OPENAI_COMPATIBLE_API_KEY = "your-key"
$env:OPENAI_COMPATIBLE_ENDPOINT = "https://api.openai.com/v1"
$env:OPENAI_COMPATIBLE_MODEL = "your-compatible-model"
python main.py process "D:\Videos\owned-video.mp4" --translation-provider openai-compatible
```

You may instead set `translation.endpoint` and `translation.model` in a private YAML config.
Successful batch responses are cached under the project `temp\translation_cache` directory,
so an unchanged paid request is not repeated. Endpoint behavior and billing depend on your
chosen provider.

## Commands

```text
python main.py process INPUT
python main.py INPUT
python main.py process INPUT --subtitle-mode bilingual_en_zh
python main.py process INPUT --translation-provider manual
python main.py process INPUT --translation-provider offline --prefer-youtube-chinese
python main.py process INPUT --resume
python main.py process INPUT --force-step transcribe
python main.py --batch inputs.txt
python main.py batch inputs.txt --parallel-jobs 2
python main.py inspect PROJECT_PATH
python main.py transcribe PROJECT_PATH
python main.py translate PROJECT_PATH
python main.py translate-export PROJECT_PATH
python main.py translate-import PROJECT_PATH TRANSLATED_FILE
python main.py render PROJECT_PATH
python main.py preview PROJECT_PATH --start 60 --duration 15
python main.py metadata PROJECT_PATH
python main.py validate PROJECT_PATH
python main.py clean PROJECT_PATH
python main.py doctor
```

`clean` removes only temporary/cache/partial files and preserves source and final assets.
It asks for confirmation unless `--yes` is provided.

`batch --parallel-jobs 2` overlaps downloads for up to two videos. Whisper transcription, local
translation, and hard-subtitle rendering automatically wait for one shared performance slot, so
they do not compete for GPU VRAM or the CPU encoder. Use `--parallel-jobs 1` for fully sequential
processing.

## Configuration

Copy `config.example.yaml` to a new filename and edit it; the example is never overwritten:

```powershell
Copy-Item config.example.yaml config.local.yaml
python main.py process "D:\Videos\owned-video.mp4" --config config.local.yaml
```

Important settings:

```yaml
# Keep source, cache, and rendered video files outside OneDrive whenever possible.
output_directory: ~/Videos/YouTube Chinese Localizer
subtitle_mode: chinese  # download_only, chinese, bilingual_en_zh, bilingual_zh_en

transcription:
  model: medium
  device: auto          # auto, cpu, cuda
  compute_type: auto
  cpu_threads: 0        # 0 adapts to CPU cores and RAM; set 1-32 to override
  beam_size: 5
  vad_filter: true
  word_timestamps: true

translation:
  direction: en-to-zh   # en-to-zh / zh-to-en; extra targets: ja, ko, es, fr, de, pt, ru, ar
  provider: ollama      # manual, offline, ollama, or openai-compatible
  batch_size: 40
  offline_device: auto  # auto uses reliable CPU; cuda must be selected explicitly
  ollama_endpoint: http://localhost:11434
  ollama_model: qwen3:4b
  ollama_context_tokens: 4096  # avoids an unnecessarily large VRAM reservation on 12 GB GPUs

download:
  format: bestvideo+bestaudio/best
  format_sort: [res, fps, br, size]
  concurrent_fragment_downloads: 4

subtitles:
  font: Noto Sans CJK SC
  font_size: 48
  max_chinese_chars_per_line: 20

render:
  codec: auto           # verifies NVIDIA, Intel, AMD, then falls back to libx264
  crf: 17
  preset: medium
  soft_subtitles: true  # also create a selectable-subtitle MP4 without re-encoding media
  output_height: null   # keep the source dimensions; 2160/1440/1080/720 cap the result
  output_fps: null      # keep source FPS; 60/30 only cap higher frame-rate sources
```

Each project stores `config.resolved.json` so later project commands use the same settings.

To download the best available video and audio without transcription, translation, or subtitle
rendering:

```powershell
python main.py process "https://www.youtube.com/watch?v=VIDEO_ID" --subtitle-mode download_only
```

The desktop interface uses this same local Videos-folder default and lets you choose another
location per task. It warns before starting when the selected folder is in OneDrive or has less
than 20 GiB free, because syncing high-bitrate source and rendered files can noticeably slow the
pipeline.

The merged original is saved in the project's `source` directory.

## Project folder

```text
output/
  sanitized_title_videoid/
    source/
      source_video.mp4       # extension may reflect the actual compatible container
      metadata.json
      metadata.raw.json
      thumbnail.jpg
    subtitles/
      source.en.vtt
      source.zh.vtt
      english.cleaned.srt
      english.ass
      chinese.srt
      chinese.ass
      bilingual.srt
      bilingual.ass
      transcription.raw.json
      translation_chunks/
    audio/
      transcription_audio.wav
    rendered/
      chinese_hardsub.mp4
      english_hardsub.mp4
      chinese_softsub.mp4
      english_softsub.mp4
      preview_START_DURATION.mp4
    publishing/
      title.txt
      description.txt
      tags.txt
      metadata_localized.json
    logs/
      pipeline.log
      report.json
      report.md
      subtitle_quality.json
    temp/
    config.resolved.json
    pipeline_state.json
```

The local original is never modified. A project copy is made using a temporary file followed
by an atomic rename. Download partials are retained so `--resume` can continue.

## Resume, overwrite, and force

- Default: refuse to overwrite an existing project.
- `--resume`: reuse a project with the same video ID/local-path ID and skip completed steps
  only when input hash, relevant configuration hash, and output files still match.
- `--overwrite`: remove and recreate only the exact matching project directory.
- `--force-step NAME`: repeat a selected stage. Supported names are `acquire`,
  `english_subtitles`, `chinese_subtitles`, `transcribe`, `translate`, and `render`. Unknown or
  misspelled names are rejected.

Every state entry records start/end times, status, hashes, outputs, errors, elapsed time, and
retry count. Failed runs are explicitly marked `failed`; manual runs are marked
`awaiting_manual_translation`.

## Subtitle styling

Hard subtitles use ASS styling with a dark outline and bottom alignment. The offline installer
bundles one carefully selected font, `Noto Sans CJK SC`, under the SIL Open Font License. The
desktop app keeps that consistent typeface and lets users select a small, standard (48), large,
or extra-large subtitle size. FFmpeg loads the bundled font directly from the application, so it
does not need to be installed system-wide. Source-checkout users can still set `subtitles.font`
to any installed family. Render a short preview before the full video:

Before starting a task, the desktop app also offers **Preview and adjust subtitles**. Drag the
subtitle box to set its horizontal/vertical position, then drag the green lower-right handle to
set an exact size. The selected values are passed to the renderer as `position_x_percent`,
`position_y_percent`, and `font_size`, so the final ASS and hard-subtitle video use the layout
you approved. The preview is a layout canvas; it does not download a video or start processing.

After a project finishes, use the desktop toolbar's **字幕审核** button, choose that project's
folder, and edit individual target-language cues without changing their timestamps. **保存修改**
rebuilds the affected SRT/ASS (including bilingual tracks), while **从此处预览 12 秒** renders a
separate `review_preview_*.mp4` around the selected cue. The final video is never overwritten by
the review preview; run the normal full render only after you approve the result.

```powershell
python main.py preview "output\PROJECT_NAME" --start 60 --duration 15
```

The readability pass wraps long Chinese text without changing timestamps and reports high
characters-per-second cues. ASS canvas width and Chinese line length follow the source video's
display aspect ratio, including rotation metadata, so portrait video does not crop a landscape
subtitle layout. Bilingual output projects the translated paragraph onto the same local Whisper
timeline, so independent platform-caption boundaries cannot drift apart. The pass does not invent
or rewrite factual content.

Every completed localized project also attempts to produce a selectable-subtitle MP4. Its video
and audio are stream-copied, so it is much faster than a hard-subtitle render and lets viewers
turn captions on or off in compatible players. If a source container cannot be remuxed safely,
the hard-subtitle video still succeeds and the report records the soft-subtitle warning.
`logs/subtitle_quality.json` and the corresponding section in
`logs/report.md` list only the cues that warrant review: overly fast reading, flashes under 0.7s,
overlong lines, and adjacent duplicate text. These findings do not alter the subtitle automatically.

## Optional NVIDIA/CUDA setup

`faster-whisper` uses CTranslate2. The Complete package includes the CUDA 12 runtime via its
bundled Ollama runtime; the Standard package uses CPU safely unless compatible CUDA libraries are
available on the computer. The app registers a compatible runtime before starting Whisper, so an
NVIDIA GPU is used only after a real DLL preflight.
Check the chosen device with:

```powershell
python main.py doctor
```

With `transcription.device: auto`, CUDA is selected only when CTranslate2 detects a device and
the CUDA 12 libraries can be loaded. Otherwise Whisper starts directly on CPU `int8`, avoiding an
unsafe partial GPU attempt. If a later GPU execution error occurs, transcription restarts on CPU.
CPU fallback chooses a conservative thread count from the available cores and RAM, leaving the
desktop responsive. Set `transcription.cpu_threads` to `1`–`32` only when an explicit override is
needed. Avoid `large-v3` on CPU unless you understand the RAM/runtime cost.

For a source-checkout installation, install Ollama or set `YOUTUBE_LOCALIZER_CUDA_RUNTIME` to a
folder that contains the CUDA 12 libraries. The offline installer needs no separate CUDA setup.

Offline subtitle translation prioritizes reliability: `translation.offline_device: auto` uses
CPU `int8`. Set it to `cuda` only when the CTranslate2 CUDA and cuDNN runtime is fully installed;
Whisper and video rendering keep their independent device settings.

For rendering, leave `render.codec: auto` to test NVIDIA NVENC, Intel Quick Sync, and AMD AMF on
the current computer. NVENC is checked in the normal FFmpeg and then the bundled compatibility
encoder. If no hardware path works, the application retries with `libx264`. You may still set an
explicit codec such as `h264_nvenc`, `h264_qsv`, `h264_amf`, or `libx264` for a reproducible setup.

## Release verification

Version 0.7.0.14 fixes enhancement frame-rate selection and stale output reuse, adds verified segment resume and progress estimates, and verifies optional models on Standard. Version 0.7.0.13 adds optional bounded-batch NCNN/Vulkan super resolution, verified device fallback, per-project rights declarations, and attribution records. Version 0.7.0.12 defaults fresh installations to Whisper Small, preserves existing Whisper models during upgrades, and confirms intentional base-only installations. Version 0.7.0.11 redesigned the desktop UI around a focused source bar, a clearer empty-state start card, compact adaptive task rows, and a settings view that remains usable at the minimum supported window size. Version 0.7.0.10 renamed the GitHub project to Video Localizer, migrated every in-app release/model URL, and added a tester-focused project home, contribution guide, testing checklist, Discussions, and structured issue forms. Version 0.7.0.9 was the first stable 0.7.0-series release, defaulted new installations to the stable update channel, and bound browser-capture continuation to the exact requested queue. Version 0.7.0.8 prevents dynamic playback pages from bypassing browser capture and automatically resumes the requested task after a successful capture. Version 0.7.0.7 adds isolated Edge-assisted media capture for dynamic playback pages, automatic Cloudflare-page recovery, and URL-only DevTools filtering. Version 0.7.0.6 added a dedicated direct-media entry point, signed-URL diagnostics, and safer extensionless-CDN validation. Version 0.7.0.5 added bounded public-page media resolution with explicit authorization and access-control boundaries. Version 0.7.0.4 added restart-safe desktop queues, per-task pause/continue and rendered-video actions, shared inspection caching, hardware-adaptive queue concurrency, transfer ETA, and completion attention. Version 0.7.0.3 added asynchronous media pre-analysis, a per-video task centre, and non-secret desktop preference persistence. Version 0.7.0.2 replaced recurring startup guidance with a persistent in-app help center, added model/environment readiness status and actionable failure summaries, and linked four-part application iterations to their compatible three-part model release. Version 0.7.0.1 added integrity-aware resume, adaptive heavy-work scheduling, stable/development update channels, and four-part application iterations that reuse compatible three-part model packs. Version 0.7.0 made Standard a compact single-file installer and added complete application branding; version 0.6.9 added selectable, hash-verified model downloads in the Standard installer; version 0.6.8 added a native Windows desktop launcher, matching-version model-pack validation,
safe two-job GUI scheduling, and bounded retry handling for temporary public-source limits. It also preserves the
compressed Standard distribution. It also includes local-AI/API-only translation paths from English
or Simplified Chinese to eight additional target languages, extensionless CDN video URL validation,
and the interactive subtitle layout preview. Install one Whisper pack after the base application; a
packaged app blocks subtitle processing with a clear instruction when no model pack is present. See [CHANGELOG.md](CHANGELOG.md) for the complete
per-version history.
After installation, run **Verify YouTube Localizer Installation** from the Start menu to validate
the packaged base manifest and load the desktop app without processing a video. The full repository
verification command also starts bundled Qwen once:

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\test_offline_install.ps1 `
  -InstallRoot "C:\path\to\YouTube Chinese Localizer"
```

For a diagnostic ZIP that omits video and subtitle text and redacts links, paths, and credentials:

```powershell
python main.py support-bundle "C:\Users\you\Videos\YouTube Chinese Localizer\project_name"
```

## Running tests

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
```

The integration test creates a two-second audio/video fixture with local FFmpeg, burns an
ASS subtitle, and validates the output. It is skipped when FFmpeg or ffprobe is unavailable
and never uses the internet.

## Troubleshooting

### `ffmpeg` or `ffprobe` is missing

Install FFmpeg, ensure its `bin` directory is on `PATH`, open a new terminal, and rerun
`python main.py doctor`. Use a build with libass; otherwise the `subtitles`/`ass` filter may
be missing.

### `faster-whisper` cannot be installed

Use Python 3.11-3.13 in a new virtual environment. The rest of the pipeline works without
it when usable source-language subtitles already exist. On a new Python release, CTranslate2 wheels
may not yet be published.

### Whisper runs out of GPU memory

Set `transcription.model: small` or `medium`, set `device: cpu`, or select a lower-memory
compute type. The error message identifies this recovery path.

### Offline translation model cannot download or load

Install the lightweight runtime with `python -m pip install -e ".[offline-translation]"`.
The offline installer already contains both directional models. A source-checkout installation
downloads a missing model only on its first offline run. `offline_device: auto` uses CPU `int8` to
avoid partial CUDA installations stalling translation; select `cuda` explicitly only after its
runtime is complete.

### YouTube video is unavailable

Confirm it is a public single-video URL. Private, authenticated, age-restricted, DRM, and
currently-live sources are intentionally unsupported. The application does not accept
cookies or browser-authentication bypasses.

### YouTube video download returns HTTP 429

The application does not request YouTube captions, so caption-endpoint rate limits no longer apply.
If the video request itself is rate-limited, wait before rerunning the same input with `--resume`;
repeated immediate retries can extend a temporary rate limit.

### FFmpeg cannot render subtitles

Check `ffmpeg -filters` for `ass` and `subtitles`, install a libass-enabled build, confirm
the configured font is installed, and run `preview` before rendering the whole video.

If FFmpeg reports Windows status `0xC000013A` (decimal `3221225786`), rendering was stopped
by the Stop button, a closed localizer window, or another interruption; it is not a codec
failure. Keep the window open and resume the same project. Rendering now reports percentage,
encoded timestamp, and speed every two seconds, and resume repeats only the unfinished stage.

### A manual import is rejected

Ask ChatGPT to return JSONL only. Do not change `id`, `start`, `end`, or the source field
(`en` or `zh`); fill every target field (`zh` or `en`). Re-export the original chunk if needed.

### Existing project error

Use `--resume` to continue safely. Use `--overwrite` only when you intend to replace the
exact matching project.

## Known limitations

- Public YouTube extraction depends on the current `yt-dlp` release and YouTube behavior.
- Phase 1 does not authenticate to YouTube or bypass restrictions.
- Translation quality and name choices require human review, especially for niche proper nouns
  and humor. Local AI paragraph mode is more fluent than the compact fast model but still cannot
  recover names that were seriously corrupted by automatic speech recognition.
- Publishing drafts are conservative and deliberately mark titles/license text for review.
- No automatic upload is implemented.
- No automatic advanced subtitle retiming is performed.
- Soft-subtitle MP4 and thumbnail text overlays are not yet exposed in Phase 1.
- Source videos with no audio stream cannot pass final output validation.
