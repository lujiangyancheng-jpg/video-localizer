from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = PROJECT_ROOT / "installer"


def test_native_launcher_source_has_a_noninteractive_verification_mode() -> None:
    source = (INSTALLER / "LocalizeStudioLauncher.cs").read_text(encoding="utf-8")

    assert '"--verify"' in source
    assert "pythonw.exe" in source
    assert "YOUTUBE_LOCALIZER_HOME" in source
    assert "Environment.SpecialFolder.MyDocuments" in source


def test_installers_and_launcher_use_the_branded_application_icon() -> None:
    launcher_builder = (INSTALLER / "build_launcher.ps1").read_text(encoding="utf-8")
    for filename in (
        "offline-installer.iss",
        "whisper-model-pack.iss",
        "local-ai-model-pack.iss",
        "super-resolution-pack.iss",
    ):
        script = (INSTALLER / filename).read_text(encoding="utf-8")
        assert "SetupIconFile=" in script
        assert "app-icon.ico" in script

    assert "/win32icon:$IconPath" in launcher_builder
    assert (PROJECT_ROOT / "assets" / "branding" / "app-icon.ico").is_file()
    assert (PROJECT_ROOT / "assets" / "branding" / "app-icon.png").is_file()


def test_base_installer_uses_the_native_launcher_not_a_cmd_wrapper() -> None:
    script = (INSTALLER / "offline-installer.iss").read_text(encoding="utf-8")
    builder = (INSTALLER / "build_offline_installer.ps1").read_text(encoding="utf-8")
    verifier = (INSTALLER / "test_offline_install.ps1").read_text(encoding="utf-8")

    assert 'Filename: "{app}\\Localize Studio.exe"' in script
    assert "Localize Studio Launcher.exe" in script
    assert "InstallLocation" in (INSTALLER / "LocalizeStudioLauncher.cs").read_text(
        encoding="utf-8"
    )
    assert "Launch Localizer.cmd" not in script
    assert "build_launcher.ps1" in builder
    assert 'ArgumentList "--verify"' in verifier


def test_standard_installer_downloads_only_selected_hash_verified_model_packs() -> None:
    script = (INSTALLER / "offline-installer.iss").read_text(encoding="utf-8")
    builder = (INSTALLER / "build_offline_installer.ps1").read_text(encoding="utf-8")
    verifier = (INSTALLER / "test_offline_install.ps1").read_text(encoding="utf-8")

    assert "CreateInputOptionPage" in script
    assert "CreateDownloadPage" in script
    assert "DownloadPage.Add" in script
    assert "ReleaseAssetBaseUrl" in script
    assert "WhisperSmallSetupSha256" in script
    assert "LocalAIBin3Sha256" in script
    assert "SuperResolutionSetupSha256" in script
    assert "CurStep = ssPostInstall" in script
    assert "InstallModelPack" in script
    assert "Get-RequiredReleaseAssetHash" in builder
    assert "Local-AI-Model-Setup" in builder
    assert "AI-Super-Resolution-Setup" in builder
    assert "ModelPackVersion" in builder
    assert "ModelPackVersion" in script
    assert "application_version" in builder
    assert "model_compatibility_version" in builder
    assert "$Manifest.application_version" in verifier


def test_standard_installer_does_not_silently_create_an_unusable_subtitle_install() -> None:
    script = (INSTALLER / "offline-installer.iss").read_text(encoding="utf-8")

    assert "function InstalledWhisperModelExists" in script
    assert "OptionalModelsPage.Values[0] := True" in script
    assert "not WizardSilent" in script
    assert "if not WizardSilent and" in script
    assert "你没有选择 Whisper 语音识别模型" in script
    assert "确定只安装基础版吗" in script
    assert "MB_YESNO, IDNO" in script


def test_standard_uses_one_pinned_compact_ffmpeg_runtime() -> None:
    installer = (INSTALLER / "offline-installer.iss").read_text(encoding="utf-8")
    builder = (INSTALLER / "build_offline_installer.ps1").read_text(encoding="utf-8")
    verifier = (INSTALLER / "test_offline_install.ps1").read_text(encoding="utf-8")

    assert "DiskSpanning=no" in installer
    assert 'PackageTier == "Standard"' in installer
    assert "ffmpeg-$FfmpegStandardVersion-essentials_build.zip" in builder
    assert "FfmpegStandardArchiveSha256" in builder
    assert "if ($IsStandardPackage)" in builder
    assert "if ($IsCompletePackage)" in verifier
    assert "runtime\\ffmpeg-nvenc-compat\\bin\\ffmpeg.exe" in verifier
    assert "assert not nvenc_compatibility_ffmpeg()" in builder


def test_builder_prunes_build_only_python_tools_but_keeps_deno() -> None:
    builder = (INSTALLER / "build_offline_installer.ps1").read_text(encoding="utf-8")

    assert "$BuildOnlyPatterns" in builder
    assert 'Where-Object { $_.Name -ne "deno.exe" }' in builder


def test_desktop_install_verification_isolated_from_the_users_saved_queue() -> None:
    builder = (INSTALLER / "build_offline_installer.ps1").read_text(encoding="utf-8")
    verifier = (INSTALLER / "test_offline_install.ps1").read_text(encoding="utf-8")

    assert "$previousLocalAppData = $env:LOCALAPPDATA" in builder
    assert "$env:LOCALAPPDATA = $SmokeLocalAppData" in builder
    assert "$env:LOCALAPPDATA = $VerificationLocalAppData" in verifier


def test_model_pack_refuses_missing_or_mismatched_base_installations() -> None:
    for filename in (
        "whisper-model-pack.iss",
        "local-ai-model-pack.iss",
        "super-resolution-pack.iss",
    ):
        script = (INSTALLER / filename).read_text(encoding="utf-8")

        assert "function BaseInstallationError" in script
        assert "offline-assets.json" in script
        assert '"version"' in script
        assert '"{#AppVersion}"' in script
        assert "function PrepareToInstall" in script


def test_local_ai_pack_only_copies_qwen_files_referenced_by_its_manifest() -> None:
    builder = (INSTALLER / "build_local_ai_model_pack.ps1").read_text(encoding="utf-8")
    base_builder = (INSTALLER / "build_offline_installer.ps1").read_text(encoding="utf-8")

    assert "Copy-RequiredOllamaModel" in builder
    assert '"digest"\\s*:' in builder
    assert "Copy-RequiredOllamaQwenModel" in base_builder
    assert "Copy-RequiredDirectory $OllamaModelRoot" not in base_builder


def test_release_signing_is_optional_but_requires_a_real_certificate() -> None:
    signer = (INSTALLER / "sign_release.ps1").read_text(encoding="utf-8")
    base_builder = (INSTALLER / "build_offline_installer.ps1").read_text(encoding="utf-8")
    model_builder = (INSTALLER / "build_whisper_model_pack.ps1").read_text(encoding="utf-8")
    local_ai_builder = (INSTALLER / "build_local_ai_model_pack.ps1").read_text(encoding="utf-8")
    super_resolution_builder = (INSTALLER / "build_super_resolution_pack.ps1").read_text(
        encoding="utf-8"
    )

    assert "Cert:\\CurrentUser\\My" in signer
    assert "Get-AuthenticodeSignature" in signer
    assert "CertificateThumbprint" in base_builder
    assert "CertificateThumbprint" in model_builder
    assert "CertificateThumbprint" in local_ai_builder
    assert "CertificateThumbprint" in super_resolution_builder


def test_super_resolution_pack_is_pinned_and_probes_safe_device_fallbacks() -> None:
    builder = (INSTALLER / "build_super_resolution_pack.ps1").read_text(encoding="utf-8")
    script = (INSTALLER / "super-resolution-pack.iss").read_text(encoding="utf-8")

    assert 'RuntimeVersion = "20250915"' in builder
    assert "7425BE94B94E4C8F37A1E433AC0E0100C43790E2C37418F4B65D8235ADFBDC87" in builder
    assert "foreach ($GpuId in @(0, 1, -1))" in builder
    assert "models-upconv_7_photo" in builder
    assert "models-cunet" in builder
    assert "super-resolution-pack.json" in script
