#ifndef StageDir
  #define StageDir "..\build\offline-installer\stage"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#ifndef AppVersion
  #define AppVersion "0.7.0.14"
#endif
#ifndef ModelPackVersion
  #define ModelPackVersion "0.7.0"
#endif
#ifndef PackageTier
  #define PackageTier "Complete"
#endif

; The Standard installer is also the lightweight online installer.  Its optional
; packages are separate, version-pinned release assets so users download only the
; models they select.  The build script supplies every SHA-256 at compile time.
#if PackageTier == "Standard"
  #ifndef WhisperSmallSetupSha256
    #error Missing WhisperSmallSetupSha256. Build Standard with build_offline_installer.ps1.
  #endif
  #ifndef WhisperSmallBinSha256
    #error Missing WhisperSmallBinSha256. Build Standard with build_offline_installer.ps1.
  #endif
  #ifndef WhisperMediumSetupSha256
    #error Missing WhisperMediumSetupSha256. Build Standard with build_offline_installer.ps1.
  #endif
  #ifndef WhisperMediumBinSha256
    #error Missing WhisperMediumBinSha256. Build Standard with build_offline_installer.ps1.
  #endif
  #ifndef LocalAISetupSha256
    #error Missing LocalAISetupSha256. Build Standard with build_offline_installer.ps1.
  #endif
  #ifndef LocalAIBin1Sha256
    #error Missing LocalAIBin1Sha256. Build Standard with build_offline_installer.ps1.
  #endif
  #ifndef LocalAIBin2Sha256
    #error Missing LocalAIBin2Sha256. Build Standard with build_offline_installer.ps1.
  #endif
  #ifndef LocalAIBin3Sha256
    #error Missing LocalAIBin3Sha256. Build Standard with build_offline_installer.ps1.
  #endif
  #ifndef SuperResolutionSetupSha256
    #error Missing SuperResolutionSetupSha256. Build Standard with build_offline_installer.ps1.
  #endif
#endif

[Setup]
AppId={{96FB8698-9622-4824-9224-87C402D0BA9E}
AppName=YouTube Chinese Localizer
AppVersion={#AppVersion}
AppPublisher=Video Localizer contributors
AppPublisherURL=https://github.com/lujiangyancheng-jpg/video-localizer
DefaultDirName={localappdata}\Programs\YouTube Chinese Localizer
DefaultGroupName=YouTube Chinese Localizer
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=YouTube-Chinese-Localizer-{#AppVersion}-{#PackageTier}-Offline-Setup
#if PackageTier == "Standard"
; Standard contains no multi-gigabyte LLM blob, so high LZMA2 compression substantially reduces
; the download without removing a runtime, model, font, or hardware fallback.
Compression=lzma2/ultra64
SolidCompression=yes
#else
; The Complete package contains a large already-compressed Qwen blob. Recompressing it gives
; negligible savings while significantly increasing build and install time.
Compression=none
SolidCompression=no
#endif
#if PackageTier == "Standard"
; Standard is intentionally a single-file installer so users do not need to manually keep an
; adjacent .bin payload beside Setup.exe. Optional model packs remain separate downloads.
DiskSpanning=no
#else
DiskSpanning=yes
DiskSliceSize=1900000000
#endif
WizardStyle=modern
SetupIconFile={#SourcePath}\..\assets\branding\app-icon.ico
UninstallDisplayIcon={app}\Localize Studio.exe
VersionInfoVersion={#AppVersion}
VersionInfoProductName=YouTube Chinese Localizer Offline
VersionInfoDescription=Offline English-Chinese video localization application

#if PackageTier == "Standard"
InfoBeforeFile={#SourcePath}\standard-package-notice.txt
#else
InfoBeforeFile={#SourcePath}\complete-package-notice.txt
#endif

[Dirs]
Name: "{userappdata}\YouTube Chinese Localizer"
Name: "{userdocs}\YouTube Localizer Projects"

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Excludes: "models\*,runtime\ollama\*,runtime\ffmpeg-nvenc-compat\*"; Flags: ignoreversion recursesubdirs createallsubdirs
; Model data survives base-program removal; optional pack uninstallers own their components.
Source: "{#StageDir}\models\*"; DestDir: "{app}\models"; Flags: ignoreversion recursesubdirs createallsubdirs uninsneveruninstall
Source: "{#StageDir}\runtime\ollama\*"; DestDir: "{app}\runtime\ollama"; Flags: ignoreversion recursesubdirs createallsubdirs uninsneveruninstall skipifsourcedoesntexist
Source: "{#StageDir}\runtime\ffmpeg-nvenc-compat\*"; DestDir: "{app}\runtime\ffmpeg-nvenc-compat"; Flags: ignoreversion recursesubdirs createallsubdirs uninsneveruninstall skipifsourcedoesntexist
; Keep the visible shortcut target outside a custom Unicode install directory. The relay launcher
; resolves the true installation root through the per-user uninstall record using Unicode APIs.
Source: "{#StageDir}\Localize Studio.exe"; DestDir: "{userappdata}\YouTube Chinese Localizer"; DestName: "Localize Studio Launcher.exe"; Flags: ignoreversion

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\Localize Studio Launcher.exe"; ValueType: string; ValueName: ""; ValueData: "{userappdata}\YouTube Chinese Localizer\Localize Studio Launcher.exe"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\Localize Studio Launcher.exe"; ValueType: string; ValueName: "Path"; ValueData: "{userappdata}\YouTube Chinese Localizer"; Flags: uninsdeletevalue

[UninstallDelete]
Type: files; Name: "{userappdata}\YouTube Chinese Localizer\Localize Studio Launcher.exe"

[Icons]
Name: "{autoprograms}\YouTube Chinese Localizer"; Filename: "{userappdata}\YouTube Chinese Localizer\Localize Studio Launcher.exe"; WorkingDir: "{userdocs}\YouTube Localizer Projects"
Name: "{autoprograms}\YouTube Localizer CLI"; Filename: "{app}\YouTube Localizer CLI.cmd"; WorkingDir: "{userdocs}\YouTube Localizer Projects"
Name: "{autoprograms}\Verify YouTube Localizer Installation"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\Verify Offline Install.ps1"" -InstallRoot ""{app}"" -SkipInference"; WorkingDir: "{app}"
Name: "{userdesktop}\YouTube Chinese Localizer"; Filename: "{userappdata}\YouTube Chinese Localizer\Localize Studio Launcher.exe"; WorkingDir: "{userdocs}\YouTube Localizer Projects"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\Localize Studio.exe"; Description: "Launch YouTube Chinese Localizer"; WorkingDir: "{userdocs}\YouTube Localizer Projects"; Flags: postinstall nowait skipifsilent

#if PackageTier == "Standard"
[Code]
const
  ReleaseAssetBaseUrl =
    'https://github.com/lujiangyancheng-jpg/video-localizer/releases/download/v{#ModelPackVersion}/';
  WhisperSmallSetup = 'YouTube-Chinese-Localizer-{#ModelPackVersion}-Whisper-Small-Model-Setup.exe';
  WhisperSmallBin = 'YouTube-Chinese-Localizer-{#ModelPackVersion}-Whisper-Small-Model-Setup-1.bin';
  WhisperMediumSetup = 'YouTube-Chinese-Localizer-{#ModelPackVersion}-Whisper-Medium-Model-Setup.exe';
  WhisperMediumBin = 'YouTube-Chinese-Localizer-{#ModelPackVersion}-Whisper-Medium-Model-Setup-1.bin';
  LocalAISetup = 'YouTube-Chinese-Localizer-{#ModelPackVersion}-Local-AI-Model-Setup.exe';
  LocalAIBin1 = 'YouTube-Chinese-Localizer-{#ModelPackVersion}-Local-AI-Model-Setup-1.bin';
  LocalAIBin2 = 'YouTube-Chinese-Localizer-{#ModelPackVersion}-Local-AI-Model-Setup-2.bin';
  LocalAIBin3 = 'YouTube-Chinese-Localizer-{#ModelPackVersion}-Local-AI-Model-Setup-3.bin';
  SuperResolutionSetup = 'YouTube-Chinese-Localizer-{#ModelPackVersion}-AI-Super-Resolution-Setup.exe';

var
  OptionalModelsPage: TInputOptionWizardPage;
  DownloadPage: TDownloadWizardPage;
  InstallWhisperSmall: Boolean;
  InstallWhisperMedium: Boolean;
  InstallLocalAI: Boolean;
  InstallSuperResolution: Boolean;
  ModelDefaultsInitialized: Boolean;

function InstalledWhisperModelExists(const InstallDir: String): Boolean;
begin
  Result :=
    FileExists(AddBackslash(InstallDir) + 'models\faster-whisper-small\model.bin') or
    FileExists(AddBackslash(InstallDir) + 'models\faster-whisper-medium\model.bin');
end;

procedure InitializeWizard;
begin
  OptionalModelsPage := CreateInputOptionPage(wpSelectTasks,
    '选择本地模型', '按需下载，随时可补装',
    '基础程序已经包含下载、视频处理和快速中英离线翻译。勾选的模型会在安装时从本项目的兼容模型 GitHub Release 下载，并逐个校验 SHA-256。' + #13#10 + #13#10 +
    'Whisper 是制作字幕必需的语音识别模型；本地 AI 模型可带来更自然的段落翻译和更多目标语种；超分辨率组件用于修复并放大低清视频。',
    False, False);
  OptionalModelsPage.Add('Whisper Small（推荐多数电脑；字幕识别）');
  OptionalModelsPage.Add('Whisper Medium（更高识别质量；占用更多磁盘和内存/显存）');
  OptionalModelsPage.Add('本地 AI 段落翻译：Qwen3:4b + Ollama（多语种与更自然翻译；体积较大）');
  OptionalModelsPage.Add('AI 超分辨率：通用实拍 + 动画模型（NVIDIA / AMD / Intel，按需安装）');
  ModelDefaultsInitialized := False;

  DownloadPage := CreateDownloadPage('下载已选模型',
    '正在下载并校验所选的本地模型。请保持网络连接；你可在此步骤取消并稍后重新运行安装器。', nil);
  DownloadPage.ShowBaseNameInsteadOfUrl := True;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = OptionalModelsPage.ID) and not ModelDefaultsInitialized then begin
    { A fresh subtitle installation should not silently become a base-only installation.
      Existing Small/Medium installations remain untouched during application upgrades. }
    if not InstalledWhisperModelExists(WizardDirValue) and not WizardSilent then
      OptionalModelsPage.Values[0] := True;
    ModelDefaultsInitialized := True;
  end;
end;

function AnyOptionalModelSelected: Boolean;
begin
  Result := InstallWhisperSmall or InstallWhisperMedium or InstallLocalAI or InstallSuperResolution;
end;

procedure QueueDownload(const FileName, ExpectedSha256: String);
begin
  DownloadPage.Add(ReleaseAssetBaseUrl + FileName, FileName, ExpectedSha256);
end;

function DownloadSelectedModelPacks: Boolean;
var
  Error: String;
begin
  Result := True;
  if not AnyOptionalModelSelected then
    exit;

  DownloadPage.Clear;
  if InstallWhisperSmall then begin
    QueueDownload(WhisperSmallSetup, '{#WhisperSmallSetupSha256}');
    QueueDownload(WhisperSmallBin, '{#WhisperSmallBinSha256}');
  end;
  if InstallWhisperMedium then begin
    QueueDownload(WhisperMediumSetup, '{#WhisperMediumSetupSha256}');
    QueueDownload(WhisperMediumBin, '{#WhisperMediumBinSha256}');
  end;
  if InstallLocalAI then begin
    QueueDownload(LocalAISetup, '{#LocalAISetupSha256}');
    QueueDownload(LocalAIBin1, '{#LocalAIBin1Sha256}');
    QueueDownload(LocalAIBin2, '{#LocalAIBin2Sha256}');
    QueueDownload(LocalAIBin3, '{#LocalAIBin3Sha256}');
  end;
  if InstallSuperResolution then
    QueueDownload(SuperResolutionSetup, '{#SuperResolutionSetupSha256}');

  DownloadPage.Show;
  try
    try
      DownloadPage.Download;
    except
      if DownloadPage.AbortedByUser then
        Log('Optional model download was cancelled by the user.')
      else begin
        Error := Format('%s: %s', [DownloadPage.LastBaseNameOrUrl, GetExceptionMessage]);
        SuppressibleMsgBox(AddPeriod(Error), mbCriticalError, MB_OK, IDOK);
      end;
      Result := False;
    end;
  finally
    DownloadPage.Hide;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = OptionalModelsPage.ID then begin
    InstallWhisperSmall := OptionalModelsPage.Values[0];
    InstallWhisperMedium := OptionalModelsPage.Values[1];
    InstallLocalAI := OptionalModelsPage.Values[2];
    InstallSuperResolution := OptionalModelsPage.Values[3];
    if not WizardSilent and
       not InstallWhisperSmall and not InstallWhisperMedium and
       not InstalledWhisperModelExists(WizardDirValue) then begin
      Result := SuppressibleMsgBox(
        '你没有选择 Whisper 语音识别模型。安装完成后仍可下载无字幕视频，' +
        '但不能识别、翻译或压制字幕。' + #13#10 + #13#10 +
        '确定只安装基础版吗？',
        mbConfirmation, MB_YESNO, IDNO) = IDYES;
    end;
  end else if CurPageID = wpReady then
    Result := DownloadSelectedModelPacks;
end;

function InstallModelPack(const SetupName, DisplayName: String): Boolean;
var
  ResultCode: Integer;
  Parameters: String;
begin
  Parameters := '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="' + WizardDirValue + '"';
  Result := Exec(ExpandConstant('{tmp}\' + SetupName), Parameters, '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
  if not Result then
    MsgBox(DisplayName + ' 没有成功安装（退出代码：' + IntToStr(ResultCode) + '）。基础程序仍已安装；请从 Releases 重新下载安装该模型包。',
      mbError, MB_OK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    if InstallWhisperSmall and not InstallModelPack(WhisperSmallSetup, 'Whisper Small') then
      RaiseException('Whisper Small model-pack installation failed.');
    if InstallWhisperMedium and not InstallModelPack(WhisperMediumSetup, 'Whisper Medium') then
      RaiseException('Whisper Medium model-pack installation failed.');
    if InstallLocalAI and not InstallModelPack(LocalAISetup, '本地 AI 模型') then
      RaiseException('Local AI model-pack installation failed.');
    if InstallSuperResolution and not InstallModelPack(SuperResolutionSetup, 'AI 超分辨率组件') then
      RaiseException('AI super-resolution pack installation failed.');
  end;
end;
#endif
