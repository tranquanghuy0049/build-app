# Builds dist\MeetingSummarizer\ and, if Inno Setup is present, a setup.exe.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
#
# Set APP_VERSION to stamp the installer (default 1.0.0). Everything is installed
# into a build-only virtualenv (.venv-build) so a developer's working venv is
# never mutated by a build, and so the bundle contains exactly the dependency set
# requirements-win.txt names — nothing a checkout happened to accumulate.
#
# The Windows counterpart of packaging/build_macos.sh.

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SrcDir    = Split-Path -Parent $ScriptDir
$VenvDir   = Join-Path $SrcDir '.venv-build'
$VenvPy    = Join-Path $VenvDir 'Scripts\python.exe'
$DistDir   = Join-Path $SrcDir 'dist\MeetingSummarizer'
$ExePath   = Join-Path $DistDir 'MeetingSummarizer.exe'

# Inno Setup rejects anything that is not 1-4 dot-separated integers.
$AppVersion = $env:APP_VERSION
if (-not $AppVersion) { $AppVersion = '1.0.0' }
$AppVersion = $AppVersion -replace '^v', ''
if ($AppVersion -notmatch '^\d+(\.\d+){0,3}$') {
    Write-Host "==> '$AppVersion' is not a valid version; using 1.0.0"
    $AppVersion = '1.0.0'
}
$env:APP_VERSION = $AppVersion

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments, [string]$What)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit $LASTEXITCODE)" }
}

Write-Host "==> Building Meeting Summarizer $AppVersion for Windows x64"
Set-Location $SrcDir

# --------------------------------------------------------------------- venv
if (-not (Test-Path $VenvPy)) {
    Write-Host '==> Creating build virtualenv (.venv-build)'
    $host_py = (Get-Command py -ErrorAction SilentlyContinue)
    if ($host_py) { Invoke-Checked 'py' @('-3', '-m', 'venv', $VenvDir) 'venv creation' }
    else          { Invoke-Checked 'python' @('-m', 'venv', $VenvDir) 'venv creation' }
}

Invoke-Checked $VenvPy @('-m', 'pip', 'install', '--upgrade', 'pip', 'wheel') 'pip upgrade'

# --------------------------------------------------------------- dependencies
# CPU wheels, explicitly. The default PyPI torch for Windows carries the bundled
# CUDA runtime — roughly 2 GB of payload that only helps the minority of users
# with an NVIDIA card, and that the app's chunk-at-a-time workload does not need.
# Set BUNDLE_TORCH_CUDA=1 to build a CUDA-capable bundle instead.
if ($env:BUNDLE_TORCH_CUDA -eq '1') {
    Write-Host '==> Installing torch + torchaudio (CUDA build, per BUNDLE_TORCH_CUDA=1)'
    Invoke-Checked $VenvPy @('-m', 'pip', 'install', 'torch>=2.2.0', 'torchaudio') 'torch install'
} else {
    Write-Host '==> Installing torch + torchaudio (CPU-only wheels)'
    Invoke-Checked $VenvPy @(
        '-m', 'pip', 'install', 'torch>=2.2.0', 'torchaudio',
        '--index-url', 'https://download.pytorch.org/whl/cpu'
    ) 'torch install'
}

# --no-deps is load-bearing: chunkformer declares deepspeed, which is
# source-only, has no Windows wheel, and fails to even configure its build here.
# requirements-win.txt names the dependencies the inference path actually needs
# in its place, and explains the reasoning at length.
Write-Host '==> Installing chunkformer (--no-deps; see requirements-win.txt)'
Invoke-Checked $VenvPy @('-m', 'pip', 'install', '--no-deps', 'chunkformer==1.2.2') 'chunkformer install'

Invoke-Checked $VenvPy @('-m', 'pip', 'install', '-r', 'requirements-win.txt') 'requirements install'
Invoke-Checked $VenvPy @('-m', 'pip', 'install', 'pyinstaller>=6.6.0') 'pyinstaller install'

# Guard the CPU choice above. Any package pulled in later that depends on torch
# can quietly drag the CUDA wheel back in, and the first visible symptom would
# otherwise be an installer several gigabytes larger than expected.
& $VenvPy -c "import torch, platform; print(f'torch {torch.__version__} on {platform.machine()}, cuda={torch.cuda.is_available()}')"
if ($env:BUNDLE_TORCH_CUDA -ne '1') {
    $torchVer = (& $VenvPy -c "import torch; print(torch.__version__)").Trim()
    if ($torchVer -notlike '*+cpu*') {
        throw "Expected a +cpu torch build, got '$torchVer'. Something re-resolved torch from PyPI."
    }
}

# The whole point of the --no-deps install: prove the inference entry point
# imports before spending half an hour bundling it. No quotes in the -c snippet
# — PowerShell strips them on the way to a native executable.
Invoke-Checked $VenvPy @('-c', 'from chunkformer import ChunkFormerModel') 'chunkformer import check'
Write-Host '==> chunkformer imports cleanly'

# ---------------------------------------------------------------------- icon
# Generated here rather than committed as a binary: a clean checkout would
# otherwise build with no exe icon and, worse, no static\icon.png, which is what
# the app serves as its favicon. Pillow is a build-time tool only — the spec
# excludes PIL, so it never reaches the bundle.
Write-Host '==> Generating app icon'
Invoke-Checked $VenvPy @('-m', 'pip', 'install', '--quiet', 'pillow') 'pillow install'
Invoke-Checked $VenvPy @('packaging\make_icon.py') 'icon generation'

# --------------------------------------------------------------------- model
# Stage the speech model into models\ so PyInstaller can bundle it. This is what
# makes the shipped app work offline with no first-use download.
Write-Host '==> Staging speech model'
Invoke-Checked $VenvPy @('packaging\fetch_model.py') 'model staging'

# -------------------------------------------------------------------- bundle
if (Test-Path (Join-Path $SrcDir 'build')) { Remove-Item -Recurse -Force (Join-Path $SrcDir 'build') }
if (Test-Path (Join-Path $SrcDir 'dist'))  { Remove-Item -Recurse -Force (Join-Path $SrcDir 'dist') }

Invoke-Checked $VenvPy @(
    '-m', 'PyInstaller', '--noconfirm', '--clean', 'packaging\MeetingSummarizer.spec'
) 'PyInstaller'

if (-not (Test-Path $ExePath)) { throw "ERROR: $ExePath was not produced" }

$bundleMb = [math]::Round(
    ((Get-ChildItem $DistDir -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB), 0)
Write-Host "==> Bundle: $DistDir ($bundleMb MB)"

# ----------------------------------------------------------------- installer
# Optional: without Inno Setup the build still leaves a working dist folder.
# winget's package installs per-user by default, which lands outside both
# Program Files directories.
$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($iscc) {
    Write-Host '==> Building installer with Inno Setup'
    Invoke-Checked $iscc @("/DAppVersion=$AppVersion", 'packaging\installer.iss') 'Inno Setup'
    Get-ChildItem (Join-Path $SrcDir 'dist\*.exe') | ForEach-Object {
        Write-Host ("==> Installer: {0} ({1} MB)" -f $_.FullName, [math]::Round($_.Length / 1MB, 0))
    }
} else {
    Write-Host '==> Inno Setup not found; skipping the installer.'
    Write-Host '    Install it (winget install JRSoftware.InnoSetup) and re-run,'
    Write-Host "    or ship $DistDir as a zip."
}

Write-Host ''
Write-Host '==> Done'
