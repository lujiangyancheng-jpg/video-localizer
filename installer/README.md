# Windows offline installer

`build_offline_installer.ps1` creates self-contained Windows x64 base packages. Both tiers bundle
Python/Tk, hardware-accelerated FFmpeg, both Argos translation models, and one curated Noto Sans
CJK SC font for subtitle rendering. A newly installed copy does not require a system font or
separate system Python/Tk installation.

The package also contains **Localize Studio.exe**, a small Unicode-safe Windows GUI launcher. It
sets the packaged environment and starts `pythonw.exe`, so the normal Start menu entry does not
open a command-prompt window. `Localize Studio.exe --verify` performs a non-interactive launcher
integrity check for release testing.

Standard's component page can also install the separate **AI Super Resolution** pack. It contains
the pinned `waifu2x-ncnn-vulkan` runtime plus photo and CUNet models, and does not increase the base
installer when unchecked. The application probes Vulkan devices and processes bounded frame
batches so one incompatible GPU does not make the whole video job unusable.

- **Standard** is a single-file installer with one pinned compact FFmpeg build. It retains
  libass subtitle rendering plus NVIDIA NVENC, Intel QSV, AMD AMF, and CPU encoding. It supports
  fast offline sentence translation once a user-selected Whisper model pack is installed.
- **Complete** adds the standalone Ollama runtime and Qwen3:4b for local paragraph-aware
  translation. It is intended for higher-quality no-API localization on systems with ample
  storage and memory; it also requires a user-selected Whisper model pack for subtitle jobs.

Build and distribute Whisper recognition models separately so users install only the model their
hardware can run:

- **Whisper Small** is the recommended choice for most computers.
- **Whisper Medium** needs more RAM/VRAM and storage, but provides higher recognition quality.

Build prerequisites:

- Windows x64 with enough free space for caches, staging, and output (22 GB minimum for Standard;
  substantially more for Complete)
- both Argos models present in their default user model directories; Complete also requires
  `qwen3:4b` in its default Ollama model directory
- Inno Setup 6 (`winget install JRSoftware.InnoSetup`)
- `curl`, `ffmpeg`, and `ffprobe` available locally

Build from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\build_offline_installer.ps1
```

Build the smaller package explicitly:

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\build_offline_installer.ps1 -PackageTier Standard
```

Build a selected Whisper model pack after the base package:

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\build_whisper_model_pack.ps1 -Model Small
powershell -ExecutionPolicy Bypass -File .\installer\build_whisper_model_pack.ps1 -Model Medium
```

Build the compatible optional super-resolution pack:

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\build_super_resolution_pack.ps1 -Version 0.7.0
```

The base builder pins Python 3.12.10, every Python runtime dependency, compact FFmpeg 8.0 for
Standard, Ollama v0.32.5 for Complete, and the Noto Sans CJK SC font source revision. The
model-pack builder pins each Whisper revision and validates its `model.bin` hash before it emits
the selected model installer. Standard is one `.exe`; keep every Complete/model-pack `.exe` and
its adjacent `.bin` files together when copying or installing it.

Each build writes `SHA256SUMS-<version>-<tier>.txt`; the super-resolution builder writes
`SHA256SUMS-<version>-super-resolution.txt`. `SHA256SUMS.txt` is a combined list for
all package tiers present in `dist`.

For a signed public release, pass a real certificate thumbprint from `Cert:\CurrentUser\My` to
either builder with `-CertificateThumbprint <thumbprint>`. This signs the native launcher before
it is packaged and signs each generated installer before its SHA-256 manifest is written. Use
`sign_release.ps1` only with a certificate you control; without it, treat the artifacts as unsigned
development builds.

Validate an installed base copy. Complete additionally runs one real Qwen inference on an
isolated local port:

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\test_offline_install.ps1 `
  -InstallRoot "C:\path\to\installed-copy"
```

The installer also adds **Verify YouTube Localizer Installation** to the Start menu. It validates
the installed asset sizes and SHA-256 hashes before loading the desktop application and local
base assets. The shortcut skips the longer Qwen inference; run the command above without
`-SkipInference` to skip inference while retaining file/hash checks. Installed Whisper and Local AI
packs are checked on Standard too. Re-run the latest base installer to repair program files and
retain models. New base-install records preserve model data on uninstall; historical uninstall
records may still own old bundled models, so use an in-place upgrade for older installations.

Large generated models, staging files, and installer binaries are intentionally ignored by Git.
Only the reproducible builder, license inventory, and code are committed.
