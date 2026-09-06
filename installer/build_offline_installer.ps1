[CmdletBinding()]
param(
    [string]$Version = "0.7.0.14",
    [string]$ModelPackVersion = "0.7.0",
    [ValidateSet("Complete", "Standard")]
    [string]$PackageTier = "Complete",
    [string]$PythonVersion = "3.12.10",
    [string]$PythonArchiveSha256 = "4ACBED6DD1C744B0376E3B1CF57CE906F9DC9E95E68824584C8099A63025A3C3",
    [string]$PythonInstallerSha256 = "67B5635E80EA51072B87941312D00EC8927C4DB9BA18938F7AD2D27B328B95FB",
    [string]$GetPipSha256 = "25B5C39ADE96BAB5EABE6404CE83CAB6DA2DEB5FE3C07D9881F43803EDB6F9C8",
    [string]$WixArchiveSha256 = "6AC824E1642D6F7277D0ED7EA09411A508F6116BA6FAE0AA5F2C7DAA2FF43D31",
    [string]$WhisperModel = "medium",
    [string]$WhisperRevision = "08e178d48790749d25932bbc082711ddcfdfbc4f",
    [string]$WhisperModelSha256 = "9B45E1009DCC4AB601EFF815B61D80E60CE3FD8C74C1A14F4A282258286B51AE",
    [string]$WhisperSmallModel = "small",
    [string]$WhisperSmallRevision = "536b0662742c02347bc0e980a01041f333bce120",
    [string]$WhisperSmallModelSha256 = "3E305921506D8872816023E4C273E75D2419FB89B24DA97B4FE7BCE14170D671",
    [string]$OllamaVersion = "v0.32.5",
    [string]$FfmpegStandardVersion = "8.0",
    [string]$FfmpegStandardArchiveSha256 = "647E467CAF82B9FA200A562769B5FF4D736AAF725804ED2C64EA9752106FA569",
    [string]$FfmpegCompatibilityVersion = "8.0",
    [string]$FfmpegCompatibilityArchiveSha256 = "48CA5E824D2660A94F89FD55287B7C35129B55BBE680C4330EFEED5269C4820F",
    [string]$NotoCjkRevision = "f8d157532fbfaeda587e826d4cd5b21a49186f7c",
    [string]$ArgosModelRoot = "$env:USERPROFILE\.youtube-chinese-localizer\models",
    [string]$ArgosEnZhModelSha256 = "1A039114D9456B6528FABB65B455B6F156319634A0F984B1F6018F7737D67598",
    [string]$ArgosZhEnModelSha256 = "EDD8C8A6863D36959613FF291074627A1635FAB2F51B872EF437E924D238921A",
    [string]$OllamaModelRoot = "$env:USERPROFILE\.ollama\models",
    [string]$QwenModelBlobSha256 = "3E4CB14174460404E7A233E531675303B2FBF7749C02F91864FE311AB6344E4F",
    [string]$CertificateThumbprint = "",
    [switch]$SkipInstaller,
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$env:PYTHONUTF8 = "1"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RuntimeRequirements = Join-Path $PSScriptRoot "requirements-runtime.lock"
$BuildRoot = Join-Path $ProjectRoot "build\offline-installer"
$StageRoot = Join-Path $BuildRoot "stage"
$CacheRoot = Join-Path $BuildRoot "download-cache"
$DistRoot = Join-Path $ProjectRoot "dist"
$PackageTierNormalized = $PackageTier.ToLowerInvariant()
$IsCompletePackage = $PackageTier -eq "Complete"
$IsStandardPackage = $PackageTier -eq "Standard"
$VersionParts = @($Version.Split('.'))
if ($VersionParts.Count -ne 4 -or @($VersionParts | Where-Object { $_ -notmatch '^\d+$' }).Count) {
    throw "Application versions must use four numeric parts, for example 0.7.0.1."
}
if ($ModelPackVersion -ne ($VersionParts[0..2] -join '.')) {
    throw "ModelPackVersion must match the first three application version parts: $($VersionParts[0..2] -join '.')."
}

function Assert-ChildPath([string]$Path, [string]$Parent) {
    $resolvedPath = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $resolvedParent = [IO.Path]::GetFullPath($Parent).TrimEnd('\')
    if (-not $resolvedPath.StartsWith("$resolvedParent\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing generated-file operation outside $resolvedParent`: $resolvedPath"
    }
}

function Reset-GeneratedDirectory([string]$Path) {
    Assert-ChildPath $Path $ProjectRoot
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
}

function Download-File([string]$Url, [string]$Destination) {
    if (Test-Path -LiteralPath $Destination) {
        return
    }
    New-Item -ItemType Directory -Path (Split-Path $Destination) -Force | Out-Null
    $partial = "$Destination.partial"
    & curl.exe --fail --location --retry 5 --retry-delay 2 --output $partial $Url
    if ($LASTEXITCODE -ne 0) {
        throw "Download failed: $Url"
    }
    Move-Item -LiteralPath $partial -Destination $Destination
}

function Download-VerifiedFile([string]$Url, [string]$Destination, [string]$Sha256) {
    Download-File $Url $Destination
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash -ne $Sha256) {
        throw "Downloaded file checksum mismatch: $Destination"
    }
}

function Copy-RequiredDirectory([string]$Source, [string]$Destination) {
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        throw "Required directory is missing: $Source"
    }
    Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
}

function Copy-RequiredFile([string]$Source, [string]$Destination) {
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "Required file is missing: $Source"
    }
    New-Item -ItemType Directory -Path (Split-Path $Destination) -Force | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
}

function Copy-RequiredOllamaQwenModel([string]$SourceRoot, [string]$DestinationRoot) {
    $manifestRelativePath = "manifests\registry.ollama.ai\library\qwen3\4b"
    $manifestSource = Join-Path $SourceRoot $manifestRelativePath
    if (-not (Test-Path -LiteralPath $manifestSource -PathType Leaf)) {
        throw "The local Qwen3:4b manifest is missing: $manifestSource. Run 'ollama pull qwen3:4b' first."
    }
    $manifestText = Get-Content -LiteralPath $manifestSource -Raw
    $digests = [regex]::Matches($manifestText, '"digest"\s*:\s*"sha256:([a-f0-9]{64})"') |
        ForEach-Object { $_.Groups[1].Value.ToLowerInvariant() } |
        Select-Object -Unique
    $requiredModelDigest = $QwenModelBlobSha256.ToLowerInvariant()
    if ($digests -notcontains $requiredModelDigest) {
        throw "The local qwen3:4b manifest does not reference the pinned Qwen model blob."
    }
    foreach ($digest in $digests) {
        Copy-RequiredFile (Join-Path $SourceRoot "blobs\sha256-$digest") `
            (Join-Path $DestinationRoot "blobs\sha256-$digest")
    }
    Copy-RequiredFile $manifestSource (Join-Path $DestinationRoot $manifestRelativePath)
    return $digests
}

function Get-RequiredReleaseAssetHash([string]$AssetName) {
    $asset = Join-Path $DistRoot $AssetName
    if (Test-Path -LiteralPath $asset -PathType Leaf) {
        return (Get-FileHash -Algorithm SHA256 -LiteralPath $asset).Hash.ToLowerInvariant()
    }
    if ($null -eq $script:ModelPackRelease) {
        $releaseUri = "https://api.github.com/repos/lujiangyancheng-jpg/video-localizer/releases/tags/v$ModelPackVersion"
        $script:ModelPackRelease = Invoke-RestMethod -Uri $releaseUri -Headers @{ Accept = "application/vnd.github+json" }
    }
    $releaseAsset = @($script:ModelPackRelease.assets | Where-Object name -eq $AssetName)
    if ($releaseAsset.Count -ne 1 -or -not $releaseAsset[0].digest) {
        throw "The Standard installer needs model-pack asset $AssetName from release v$ModelPackVersion, but its SHA-256 digest is unavailable."
    }
    $digest = [string]$releaseAsset[0].digest
    if ($digest -notmatch '^sha256:([0-9a-fA-F]{64})$') {
        throw "GitHub returned an invalid digest for model-pack asset $AssetName`: $digest"
    }
    return $Matches[1].ToLowerInvariant()
}

New-Item -ItemType Directory -Path $CacheRoot, $DistRoot -Force | Out-Null
Reset-GeneratedDirectory $StageRoot

$AppRoot = Join-Path $StageRoot "app"
$RuntimeRoot = Join-Path $StageRoot "runtime"
$ModelsRoot = Join-Path $StageRoot "models"
$FontsRoot = Join-Path $StageRoot "fonts"
$LicenseRoot = Join-Path $StageRoot "licenses"
$AssetsRoot = Join-Path $StageRoot "assets"
New-Item -ItemType Directory -Path $AppRoot, $RuntimeRoot, $ModelsRoot, $FontsRoot, $LicenseRoot, $AssetsRoot -Force | Out-Null
$PackageTierNormalized | Set-Content -LiteralPath (Join-Path $StageRoot "package-tier.txt") -Encoding ascii

Write-Host "[1/9] Staging application source..."
foreach ($file in @(
    "main.py",
    "localizer_gui.pyw",
    "config.example.yaml",
    "LICENSE",
    "README.md",
    "TERMS_OF_USE.md",
    "COPYRIGHT_POLICY.md",
    "THIRD_PARTY_NOTICES.md"
)) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $file) -Destination $AppRoot -Force
}
Copy-Item -LiteralPath (Join-Path $ProjectRoot "glossary.example.yaml") -Destination (Join-Path $AppRoot "glossary.yaml") -Force
Copy-RequiredDirectory (Join-Path $ProjectRoot "src") (Join-Path $AppRoot "src")
Copy-RequiredDirectory (Join-Path $ProjectRoot "docs") (Join-Path $AppRoot "docs")
Get-ChildItem -LiteralPath $AppRoot -Directory -Recurse -Filter "__pycache__" |
    ForEach-Object {
        Assert-ChildPath $_.FullName $StageRoot
        Remove-Item -LiteralPath $_.FullName -Recurse -Force
    }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "YouTube Localizer CLI.cmd") -Destination $StageRoot
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "test_offline_install.ps1") `
    -Destination (Join-Path $StageRoot "Verify Offline Install.ps1")
Copy-Item -LiteralPath $RuntimeRequirements -Destination (Join-Path $StageRoot "runtime-dependencies.lock")
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "THIRD_PARTY_MODELS.md") -Destination $LicenseRoot
Copy-RequiredFile (Join-Path $ProjectRoot "assets\branding\app-icon.png") `
    (Join-Path $AssetsRoot "app-icon.png")
Copy-RequiredFile (Join-Path $ProjectRoot "assets\branding\app-icon.ico") `
    (Join-Path $AssetsRoot "app-icon.ico")
& (Join-Path $PSScriptRoot "build_launcher.ps1") -OutputPath (Join-Path $StageRoot "Localize Studio.exe")
if ($CertificateThumbprint) {
    & (Join-Path $PSScriptRoot "sign_release.ps1") `
        -CertificateThumbprint $CertificateThumbprint `
        -ArtifactPath (Join-Path $StageRoot "Localize Studio.exe")
}

Write-Host "[2/9] Installing the embedded Python/Tk runtime and application dependencies..."
$PythonArchive = Join-Path $CacheRoot "python-$PythonVersion-embed-amd64.zip"
Download-File "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" $PythonArchive
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $PythonArchive).Hash -ne $PythonArchiveSha256) {
    throw "Embedded Python archive checksum mismatch."
}
$PythonRoot = Join-Path $RuntimeRoot "python"
New-Item -ItemType Directory -Path $PythonRoot -Force | Out-Null
Expand-Archive -LiteralPath $PythonArchive -DestinationPath $PythonRoot -Force
$PythonMinor = ($PythonVersion.Split('.')[0..1] -join '')
$PthFile = Join-Path $PythonRoot "python$PythonMinor._pth"
@(
    "python$PythonMinor.zip"
    "."
    "Lib"
    "Lib\site-packages"
    "import site"
) | Set-Content -LiteralPath $PthFile -Encoding ascii

# The official embeddable distribution omits tkinter and Tcl/Tk. Extract the matching,
# signed CPython installer payload without installing it on the build machine, then add only
# the GUI runtime files to the portable Python directory.
$PythonInstaller = Join-Path $CacheRoot "python-$PythonVersion-amd64.exe"
Download-VerifiedFile `
    "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe" `
    $PythonInstaller `
    $PythonInstallerSha256
$WixArchive = Join-Path $CacheRoot "wix314-binaries.zip"
Download-VerifiedFile `
    "https://github.com/wixtoolset/wix3/releases/download/wix3141rtm/wix314-binaries.zip" `
    $WixArchive `
    $WixArchiveSha256
$WixToolsRoot = Join-Path $BuildRoot "wix-tools"
$PythonBundleRoot = Join-Path $BuildRoot "python-bundle"
$PythonMsiRoot = Join-Path $BuildRoot "python-msi"
Reset-GeneratedDirectory $WixToolsRoot
Reset-GeneratedDirectory $PythonBundleRoot
Reset-GeneratedDirectory $PythonMsiRoot
Expand-Archive -LiteralPath $WixArchive -DestinationPath $WixToolsRoot -Force
$Dark = Join-Path $WixToolsRoot "dark.exe"
& $Dark -nologo -x $PythonBundleRoot $PythonInstaller
if ($LASTEXITCODE -ne 0) { throw "Could not extract the CPython installer payload." }
$AttachedContainer = Join-Path $PythonBundleRoot "AttachedContainer"
foreach ($msiName in @("tcltk.msi", "lib.msi")) {
    $msiPath = Join-Path $AttachedContainer $msiName
    if (-not (Test-Path -LiteralPath $msiPath -PathType Leaf)) {
        throw "The CPython installer payload is missing $msiName."
    }
    $arguments = "/a `"$msiPath`" /qn TARGETDIR=`"$PythonMsiRoot`""
    $process = Start-Process -FilePath "msiexec.exe" -ArgumentList $arguments `
        -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0) {
        throw "Could not extract $msiName (exit code $($process.ExitCode))."
    }
}
foreach ($runtimeFile in @("_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "zlib1.dll")) {
    Copy-Item -LiteralPath (Join-Path $PythonMsiRoot "DLLs\$runtimeFile") `
        -Destination $PythonRoot -Force
}
New-Item -ItemType Directory -Path (Join-Path $PythonRoot "Lib") -Force | Out-Null
Copy-RequiredDirectory (Join-Path $PythonMsiRoot "Lib\tkinter") (Join-Path $PythonRoot "Lib\tkinter")
Copy-RequiredDirectory (Join-Path $PythonMsiRoot "tcl") (Join-Path $PythonRoot "tcl")

$GetPip = Join-Path $CacheRoot "get-pip.py"
Download-VerifiedFile "https://bootstrap.pypa.io/get-pip.py" $GetPip $GetPipSha256
$EmbeddedPython = Join-Path $PythonRoot "python.exe"
& $EmbeddedPython -c "import tkinter as tk; root=tk.Tk(); root.withdraw(); root.update_idletasks(); root.destroy(); print('embedded tkinter: ok')"
if ($LASTEXITCODE -ne 0) { throw "The embedded Python tkinter runtime is incomplete." }
& $EmbeddedPython $GetPip --no-warn-script-location --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "Could not bootstrap pip in the embedded Python runtime." }
Push-Location $ProjectRoot
try {
    & $EmbeddedPython -m pip install --disable-pip-version-check --no-cache-dir --only-binary=:all: `
        -r $RuntimeRequirements
    if ($LASTEXITCODE -ne 0) { throw "Could not install locked runtime dependencies." }
    & $EmbeddedPython -m pip install --disable-pip-version-check --no-cache-dir --no-deps `
        --no-build-isolation ".[transcription,offline-translation]"
    if ($LASTEXITCODE -ne 0) { throw "Could not install application dependencies." }
} finally {
    Pop-Location
}

# pip and the wheel-build helpers are needed only while assembling the portable runtime.
# Console wrappers other than Deno are also unused because the shipped CLI invokes Python
# directly. Removing them keeps Standard small without removing any application feature.
$BuildOnlyPatterns = @(
    "pip", "pip-*.dist-info",
    "setuptools", "setuptools-*.dist-info", "_distutils_hack", "distutils-precedence.pth",
    "hatchling", "hatchling-*.dist-info",
    "pathspec", "pathspec-*.dist-info",
    "pluggy", "pluggy-*.dist-info",
    "trove_classifiers", "trove_classifiers-*.dist-info"
)
$SitePackages = Join-Path $PythonRoot "Lib\site-packages"
foreach ($pattern in $BuildOnlyPatterns) {
    Get-ChildItem -LiteralPath $SitePackages -Force -Filter $pattern -ErrorAction SilentlyContinue |
        ForEach-Object {
            Assert-ChildPath $_.FullName $PythonRoot
            Remove-Item -LiteralPath $_.FullName -Recurse -Force
        }
}
$ScriptsRoot = Join-Path $PythonRoot "Scripts"
$DenoExecutable = Join-Path $ScriptsRoot "deno.exe"
if (-not (Test-Path -LiteralPath $DenoExecutable -PathType Leaf)) {
    throw "The embedded YouTube JavaScript runtime is missing: $DenoExecutable"
}
Get-ChildItem -LiteralPath $ScriptsRoot -Force |
    Where-Object { $_.Name -ne "deno.exe" } |
    ForEach-Object {
        Assert-ChildPath $_.FullName $PythonRoot
        Remove-Item -LiteralPath $_.FullName -Recurse -Force
    }
Get-ChildItem -LiteralPath $PythonRoot -Directory -Recurse -Force -Filter "__pycache__" |
    Sort-Object FullName -Descending |
    ForEach-Object {
        Assert-ChildPath $_.FullName $PythonRoot
        Remove-Item -LiteralPath $_.FullName -Recurse -Force
    }

Write-Host "[3/9] Whisper recognition models are supplied as optional Small and Medium model packs..."

Write-Host "[4/9] Copying both Argos translation models..."
foreach ($name in @("translate-en_zh-1_9", "translate-zh_en-1_9")) {
    $source = Join-Path $ArgosModelRoot $name
    Copy-RequiredDirectory $source (Join-Path $ModelsRoot $name)
    Copy-Item -LiteralPath (Join-Path $source "README.md") -Destination (Join-Path $LicenseRoot "$name-README.md") -Force
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $ModelsRoot "translate-en_zh-1_9\model\model.bin")).Hash -ne $ArgosEnZhModelSha256) {
    throw "English-to-Chinese Argos model checksum mismatch."
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $ModelsRoot "translate-zh_en-1_9\model\model.bin")).Hash -ne $ArgosZhEnModelSha256) {
    throw "Chinese-to-English Argos model checksum mismatch."
}
Download-File "https://creativecommons.org/licenses/by/4.0/legalcode.txt" (Join-Path $LicenseRoot "CC-BY-4.0.txt")

Write-Host "[5/9] Downloading the bundled subtitle font..."
$FontCacheRoot = Join-Path $CacheRoot "fonts"
New-Item -ItemType Directory -Path $FontCacheRoot -Force | Out-Null
$FontAssets = @(
    @{
        Name = "NotoSansCJKsc-Regular.otf"
        Url = "https://raw.githubusercontent.com/notofonts/noto-cjk/$NotoCjkRevision/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf"
        Sha256 = "2C76254F6FC379FDDFCE0A7E84FB5385BB135D3E399294F6EEB6680D0365B74B"
    }
)
foreach ($font in $FontAssets) {
    $cachedFont = Join-Path $FontCacheRoot $font.Name
    Download-VerifiedFile $font.Url $cachedFont $font.Sha256
    Copy-Item -LiteralPath $cachedFont -Destination (Join-Path $FontsRoot $font.Name) -Force
}
Download-File "https://raw.githubusercontent.com/notofonts/noto-cjk/$NotoCjkRevision/Sans/LICENSE" (Join-Path $LicenseRoot "Noto-Sans-CJK-SC-OFL-1.1.txt")

if ($IsCompletePackage) {
    Write-Host "[6/9] Copying Qwen3:4b and downloading the standalone Ollama runtime..."
    $qwenModelDigests = Copy-RequiredOllamaQwenModel $OllamaModelRoot (Join-Path $ModelsRoot "ollama")
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $ModelsRoot "ollama\blobs\sha256-$($QwenModelBlobSha256.ToLowerInvariant())")).Hash -ne $QwenModelBlobSha256) {
        throw "Qwen3:4b model checksum mismatch."
    }
    $QwenLicenseDigest = $qwenModelDigests |
        Where-Object { $_ -eq "d18a5cc71b84bc4af394a31116bd3932b42241de70c77d2b76d69a314ec8aa12" } |
        Select-Object -First 1
    if (-not $QwenLicenseDigest) { throw "Could not locate the Qwen Apache-2.0 license blob." }
    Copy-RequiredFile (Join-Path $ModelsRoot "ollama\blobs\sha256-$QwenLicenseDigest") `
        (Join-Path $LicenseRoot "Qwen3-Apache-2.0.txt")

    $OllamaRelease = Invoke-RestMethod -Uri "https://api.github.com/repos/ollama/ollama/releases/tags/$OllamaVersion"
    $OllamaAsset = $OllamaRelease.assets | Where-Object { $_.name -eq "ollama-windows-amd64.zip" } | Select-Object -First 1
    $ChecksumAsset = $OllamaRelease.assets | Where-Object { $_.name -eq "sha256sum.txt" } | Select-Object -First 1
    if (-not $OllamaAsset -or -not $ChecksumAsset) { throw "The latest Ollama release is missing Windows assets." }
    $OllamaArchive = Join-Path $CacheRoot "$($OllamaRelease.tag_name)-ollama-windows-amd64.zip"
    $OllamaChecksums = Join-Path $CacheRoot "$($OllamaRelease.tag_name)-sha256sum.txt"
    Download-File $OllamaAsset.browser_download_url $OllamaArchive
    Download-File $ChecksumAsset.browser_download_url $OllamaChecksums
    $ChecksumLine = Get-Content -LiteralPath $OllamaChecksums | Where-Object { $_ -match "ollama-windows-amd64\.zip" } | Select-Object -First 1
    if (-not $ChecksumLine) { throw "Ollama checksum entry is missing." }
    $ExpectedChecksum = ($ChecksumLine -split '\s+')[0].ToUpperInvariant()
    $ActualChecksum = (Get-FileHash -Algorithm SHA256 -LiteralPath $OllamaArchive).Hash
    if ($ActualChecksum -ne $ExpectedChecksum) { throw "Ollama archive checksum mismatch." }
    $OllamaRoot = Join-Path $RuntimeRoot "ollama"
    New-Item -ItemType Directory -Path $OllamaRoot -Force | Out-Null
    Expand-Archive -LiteralPath $OllamaArchive -DestinationPath $OllamaRoot -Force
    Download-File "https://raw.githubusercontent.com/ollama/ollama/main/LICENSE" (Join-Path $LicenseRoot "Ollama-MIT.txt")
} else {
    Write-Host "[6/9] Standard package: Omitting Qwen3:4b and Ollama; fast offline translation remains included."
}

Write-Host "[7/9] Staging the hardware-accelerated FFmpeg runtime..."
$FfmpegBin = Join-Path $RuntimeRoot "ffmpeg\bin"
New-Item -ItemType Directory -Path $FfmpegBin -Force | Out-Null
if ($IsStandardPackage) {
    # Standard uses one pinned essentials build. It contains libass, common codecs, NVENC,
    # Intel QSV and AMD AMF while using the older NVENC API that was previously bundled as
    # a second compatibility copy.
    $FfmpegStandardArchive = Join-Path $CacheRoot "ffmpeg-$FfmpegStandardVersion-essentials_build.zip"
    Download-VerifiedFile `
        "https://github.com/GyanD/codexffmpeg/releases/download/$FfmpegStandardVersion/ffmpeg-$FfmpegStandardVersion-essentials_build.zip" `
        $FfmpegStandardArchive `
        $FfmpegStandardArchiveSha256
    $FfmpegStandardExtract = Join-Path $BuildRoot "ffmpeg-standard-extract"
    Reset-GeneratedDirectory $FfmpegStandardExtract
    Expand-Archive -LiteralPath $FfmpegStandardArchive -DestinationPath $FfmpegStandardExtract -Force
    $FfmpegExe = (Get-ChildItem -LiteralPath $FfmpegStandardExtract -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1).FullName
    $FfprobeExe = (Get-ChildItem -LiteralPath $FfmpegStandardExtract -Recurse -Filter "ffprobe.exe" | Select-Object -First 1).FullName
    if (-not $FfmpegExe -or -not $FfprobeExe) {
        throw "The Standard FFmpeg archive is missing ffmpeg.exe or ffprobe.exe."
    }
    $FfmpegDistributionRoot = Split-Path (Split-Path $FfmpegExe)
    Copy-Item -LiteralPath $FfmpegExe, $FfprobeExe -Destination $FfmpegBin -Force
    Copy-Item -LiteralPath (Join-Path $FfmpegDistributionRoot "LICENSE") -Destination (Join-Path $LicenseRoot "FFmpeg-GPLv3.txt") -Force
    Copy-Item -LiteralPath (Join-Path $FfmpegDistributionRoot "README.txt") -Destination (Join-Path $LicenseRoot "FFmpeg-build-README.txt") -Force
} else {
    $FfmpegExe = (Get-Command ffmpeg -ErrorAction Stop).Source
    $FfprobeExe = (Get-Command ffprobe -ErrorAction Stop).Source
    Copy-Item -LiteralPath $FfmpegExe, $FfprobeExe -Destination $FfmpegBin -Force
    $FfmpegDistributionRoot = Split-Path (Split-Path $FfmpegExe)
    Copy-Item -LiteralPath (Join-Path $FfmpegDistributionRoot "LICENSE") -Destination (Join-Path $LicenseRoot "FFmpeg-GPLv3.txt") -Force
    Copy-Item -LiteralPath (Join-Path $FfmpegDistributionRoot "README.txt") -Destination (Join-Path $LicenseRoot "FFmpeg-build-README.txt") -Force

    $FfmpegCompatibilityArchive = Join-Path $CacheRoot "ffmpeg-$FfmpegCompatibilityVersion-full_build.zip"
    Download-VerifiedFile `
        "https://github.com/GyanD/codexffmpeg/releases/download/$FfmpegCompatibilityVersion/ffmpeg-$FfmpegCompatibilityVersion-full_build.zip" `
        $FfmpegCompatibilityArchive `
        $FfmpegCompatibilityArchiveSha256
    $FfmpegCompatibilityExtract = Join-Path $BuildRoot "ffmpeg-nvenc-compat-extract"
    Reset-GeneratedDirectory $FfmpegCompatibilityExtract
    Expand-Archive -LiteralPath $FfmpegCompatibilityArchive -DestinationPath $FfmpegCompatibilityExtract -Force
    $FfmpegCompatibilityExe = Get-ChildItem -LiteralPath $FfmpegCompatibilityExtract -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
    $FfprobeCompatibilityExe = Get-ChildItem -LiteralPath $FfmpegCompatibilityExtract -Recurse -Filter "ffprobe.exe" | Select-Object -First 1
    if (-not $FfmpegCompatibilityExe -or -not $FfprobeCompatibilityExe) {
        throw "The NVENC compatibility FFmpeg archive is missing ffmpeg.exe or ffprobe.exe."
    }
    $FfmpegCompatibilityBin = Join-Path $RuntimeRoot "ffmpeg-nvenc-compat\bin"
    New-Item -ItemType Directory -Path $FfmpegCompatibilityBin -Force | Out-Null
    Copy-Item -LiteralPath $FfmpegCompatibilityExe.FullName, $FfprobeCompatibilityExe.FullName -Destination $FfmpegCompatibilityBin -Force
    $FfmpegCompatibilityRoot = Split-Path (Split-Path $FfmpegCompatibilityExe.FullName)
    Copy-Item -LiteralPath (Join-Path $FfmpegCompatibilityRoot "README.txt") `
        -Destination (Join-Path $LicenseRoot "FFmpeg-NVENC-Compatibility-README.txt") -Force
}

$FfmpegFilters = (& (Join-Path $FfmpegBin "ffmpeg.exe") -hide_banner -filters 2>&1 | Out-String)
$FfmpegEncoders = (& (Join-Path $FfmpegBin "ffmpeg.exe") -hide_banner -encoders 2>&1 | Out-String)
foreach ($filter in @("subtitles", "ass", "scale", "fps")) {
    if ($FfmpegFilters -notmatch "\b$filter\b") { throw "Bundled FFmpeg is missing filter: $filter" }
}
foreach ($encoder in @("libx264", "libx265", "h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv", "h264_amf", "aac")) {
    if ($FfmpegEncoders -notmatch "\b$encoder\b") { throw "Bundled FFmpeg is missing encoder: $encoder" }
}

Write-Host "[8/9] Writing a checksummed offline asset manifest..."
$ManifestAssets = @(
    @{ name = "argos-en-zh-1.9"; path = "models/translate-en_zh-1_9/model/model.bin"; license = "CC-BY-4.0" },
    @{ name = "argos-zh-en-1.9"; path = "models/translate-zh_en-1_9/model/model.bin"; license = "CC-BY-4.0" },
    @{ name = "runtime-dependencies-lock"; path = "runtime-dependencies.lock"; license = "N/A" }
    @{ name = "noto-sans-cjk-sc-regular"; path = "fonts/NotoSansCJKsc-Regular.otf"; license = "OFL-1.1" }
    @{ name = "ffmpeg-runtime"; path = "runtime/ffmpeg/bin/ffmpeg.exe"; license = "GPL-3.0" }
    @{ name = "application-icon"; path = "assets/app-icon.png"; license = "Project artwork" }
)
if ($IsCompletePackage) {
    $ManifestAssets = @(
        @{ name = "qwen3-4b-q4_k_m"; path = "models/ollama/blobs/sha256-3e4cb14174460404e7a233e531675303b2fbf7749c02f91864fe311ab6344e4f"; license = "Apache-2.0" }
    ) + $ManifestAssets
}
$Manifest = foreach ($asset in $ManifestAssets) {
    $assetPath = Join-Path $StageRoot ($asset.path.Replace('/', '\'))
    if (-not (Test-Path -LiteralPath $assetPath -PathType Leaf)) { throw "Manifest asset is missing: $assetPath" }
    $item = Get-Item -LiteralPath $assetPath
    [ordered]@{
        name = $asset.name
        relative_path = $asset.path
        bytes = $item.Length
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $assetPath).Hash.ToLowerInvariant()
        license = $asset.license
    }
}
[ordered]@{
    application = "YouTube Chinese Localizer"
    # The legacy version field is the three-part model ABI, so already published model-pack
    # installers remain usable throughout a 0.7.0.x application iteration series.
    version = $ModelPackVersion
    application_version = $Version
    model_compatibility_version = $ModelPackVersion
    package_tier = $PackageTierNormalized
    generated_utc = [DateTime]::UtcNow.ToString("o")
    assets = $Manifest
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $StageRoot "offline-assets.json") -Encoding utf8

if (-not $SkipSmokeTest) {
    Write-Host "[9/9] Smoke-testing the staged application with network-free model resolution..."
    $previousHome = $env:YOUTUBE_LOCALIZER_HOME
    $previousModels = $env:YOUTUBE_LOCALIZER_MODELS
    $previousFonts = $env:YOUTUBE_LOCALIZER_FONTS
    $previousFfmpeg = $env:FFMPEG_PATH
    $previousFfprobe = $env:FFPROBE_PATH
    $previousLocalAppData = $env:LOCALAPPDATA
    $SmokeLocalAppData = Join-Path $BuildRoot "smoke-local-app-data"
    Reset-GeneratedDirectory $SmokeLocalAppData
    try {
        $env:YOUTUBE_LOCALIZER_HOME = $StageRoot
        $env:YOUTUBE_LOCALIZER_MODELS = $ModelsRoot
        $env:YOUTUBE_LOCALIZER_FONTS = $FontsRoot
        $env:FFMPEG_PATH = Join-Path $FfmpegBin "ffmpeg.exe"
        $env:FFPROBE_PATH = Join-Path $FfmpegBin "ffprobe.exe"
        # The staged desktop test must not read or overwrite the developer's real saved queue.
        $env:LOCALAPPDATA = $SmokeLocalAppData
        if ($IsCompletePackage) {
            & $EmbeddedPython -c "from youtube_localizer.resources import bundled_fonts_directory, bundled_ollama_models, ollama_executable, nvenc_compatibility_ffmpeg; assert bundled_fonts_directory(); assert bundled_ollama_models(); assert ollama_executable(); assert nvenc_compatibility_ffmpeg(); print('complete offline runtime smoke test: ok')"
        } else {
            & $EmbeddedPython -c "from youtube_localizer.resources import application_icon_path, bundled_fonts_directory, nvenc_compatibility_ffmpeg; assert application_icon_path(); assert bundled_fonts_directory(); assert not nvenc_compatibility_ffmpeg(); print('standard offline runtime smoke test: ok')"
        }
        if ($LASTEXITCODE -ne 0) { throw "Staged offline runtime smoke test failed." }
        & $EmbeddedPython -c "import tkinter as tk; from youtube_localizer.gui import LocalizerWindow; root=tk.Tk(); root.attributes('-alpha', 0.0); window=LocalizerWindow(root); root.update(); assert root.title().startswith('Localize Studio'); assert (root.winfo_width(), root.winfo_height()) == (1180, 820); assert tuple(root.minsize()) == (960, 700); assert window.empty_state.winfo_ismapped(); assert not window.settings_panel.winfo_ismapped(); assert window.task_tree; assert window.paste_button; assert window.browser_capture_button; root.destroy(); print('staged desktop interface and task centre: ok')"
        if ($LASTEXITCODE -ne 0) { throw "Staged desktop interface smoke test failed." }
        & $EmbeddedPython (Join-Path $AppRoot "main.py") --help | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Staged CLI smoke test failed." }
        $Launcher = Join-Path $StageRoot "Localize Studio.exe"
        $LauncherProcess = Start-Process -FilePath $Launcher -ArgumentList "--verify" -PassThru -Wait
        if ($LauncherProcess.ExitCode -ne 0) { throw "Staged native GUI launcher smoke test failed." }
    } finally {
        $env:YOUTUBE_LOCALIZER_HOME = $previousHome
        $env:YOUTUBE_LOCALIZER_MODELS = $previousModels
        $env:YOUTUBE_LOCALIZER_FONTS = $previousFonts
        $env:FFMPEG_PATH = $previousFfmpeg
        $env:FFPROBE_PATH = $previousFfprobe
        $env:LOCALAPPDATA = $previousLocalAppData
    }
} else {
    Write-Host "[9/9] Smoke test skipped."
}

if (-not $SkipInstaller) {
    $Iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
    if (-not $Iscc) {
        $DefaultIscc = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
        if (Test-Path -LiteralPath $DefaultIscc) { $Iscc = $DefaultIscc }
    }
    if (-not $Iscc) {
        $UserIscc = Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"
        if (Test-Path -LiteralPath $UserIscc) { $Iscc = $UserIscc }
    }
    if (-not $Iscc) {
        throw "Inno Setup 6 is required. Install it with: winget install JRSoftware.InnoSetup"
    }
    $isccArguments = @(
        "/DStageDir=$StageRoot",
        "/DOutputDir=$DistRoot",
        "/DAppVersion=$Version",
        "/DModelPackVersion=$ModelPackVersion",
        "/DPackageTier=$PackageTier"
    )
    if ($IsStandardPackage) {
        $optionalAssets = @(
            @{ Define = "WhisperSmallSetupSha256"; Name = "YouTube-Chinese-Localizer-$ModelPackVersion-Whisper-Small-Model-Setup.exe" },
            @{ Define = "WhisperSmallBinSha256"; Name = "YouTube-Chinese-Localizer-$ModelPackVersion-Whisper-Small-Model-Setup-1.bin" },
            @{ Define = "WhisperMediumSetupSha256"; Name = "YouTube-Chinese-Localizer-$ModelPackVersion-Whisper-Medium-Model-Setup.exe" },
            @{ Define = "WhisperMediumBinSha256"; Name = "YouTube-Chinese-Localizer-$ModelPackVersion-Whisper-Medium-Model-Setup-1.bin" },
            @{ Define = "LocalAISetupSha256"; Name = "YouTube-Chinese-Localizer-$ModelPackVersion-Local-AI-Model-Setup.exe" },
            @{ Define = "LocalAIBin1Sha256"; Name = "YouTube-Chinese-Localizer-$ModelPackVersion-Local-AI-Model-Setup-1.bin" },
            @{ Define = "LocalAIBin2Sha256"; Name = "YouTube-Chinese-Localizer-$ModelPackVersion-Local-AI-Model-Setup-2.bin" },
            @{ Define = "LocalAIBin3Sha256"; Name = "YouTube-Chinese-Localizer-$ModelPackVersion-Local-AI-Model-Setup-3.bin" },
            @{ Define = "SuperResolutionSetupSha256"; Name = "YouTube-Chinese-Localizer-$ModelPackVersion-AI-Super-Resolution-Setup.exe" }
        )
        foreach ($asset in $optionalAssets) {
            $isccArguments += "/D$($asset.Define)=$(Get-RequiredReleaseAssetHash $asset.Name)"
        }
    }
    & $Iscc @isccArguments (Join-Path $PSScriptRoot "offline-installer.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed to build the installer." }
    $SetupFiles = Get-ChildItem -LiteralPath $DistRoot -File |
        Where-Object { $_.Name -like "YouTube-Chinese-Localizer-$Version-$PackageTier-Offline-Setup*" } |
        Sort-Object Name
    if ($CertificateThumbprint) {
        & (Join-Path $PSScriptRoot "sign_release.ps1") `
            -CertificateThumbprint $CertificateThumbprint `
            -ArtifactPath @($SetupFiles | Where-Object { $_.Extension -eq ".exe" } | ForEach-Object FullName)
    }
    $Checksums = foreach ($file in $SetupFiles) {
        "{0}  {1}" -f (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant(), $file.Name
    }
    $Checksums | Set-Content `
        -LiteralPath (Join-Path $DistRoot "SHA256SUMS-$Version-$PackageTierNormalized.txt") `
        -Encoding ascii
    $ReleaseFiles = Get-ChildItem -LiteralPath $DistRoot -File |
        Where-Object { $_.Name -like "YouTube-Chinese-Localizer-$Version-*-Setup*" } |
        Sort-Object Name
    $ReleaseChecksums = foreach ($file in $ReleaseFiles) {
        "{0}  {1}" -f (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant(), $file.Name
    }
    $ReleaseChecksums | Set-Content -LiteralPath (Join-Path $DistRoot "SHA256SUMS.txt") -Encoding ascii
}

$StageBytes = (Get-ChildItem -LiteralPath $StageRoot -File -Recurse | Measure-Object Length -Sum).Sum
Write-Host ("Offline stage ready: {0} ({1:N2} GiB)" -f $StageRoot, ($StageBytes / 1GB))
if (-not $SkipInstaller) {
    Write-Host "Installer output: $DistRoot"
}
