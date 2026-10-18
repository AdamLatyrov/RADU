$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$output = Join-Path $root "artifacts\RADU-Windows"
$engineOutput = Join-Path $root "artifacts\engine"
$work = Join-Path $env:TEMP "radu-engine-pyinstaller-work"
$buildEnvironment = Join-Path $env:TEMP "radu-release-build-venv"
$buildPython = Join-Path $buildEnvironment "Scripts\python.exe"
$model = Join-Path $root "model\vosk-model-small-ru-0.22"

if (-not (Test-Path (Join-Path $model "am\final.mdl"))) {
    throw "The Russian Vosk model is missing from the build environment."
}

Remove-Item $output, $engineOutput -Recurse -Force -ErrorAction SilentlyContinue
New-Item $output -ItemType Directory -Force | Out-Null

if (-not (Test-Path $buildPython)) {
    python -m venv $buildEnvironment
}
& $buildPython -m pip install --disable-pip-version-check -r (Join-Path $root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Could not prepare the isolated packaging environment." }

& $buildPython -m PyInstaller --noconfirm --clean (Join-Path $PSScriptRoot "RADU.Engine.spec") `
    --distpath $engineOutput --workpath $work
if ($LASTEXITCODE -ne 0) { throw "The packaged speech engine build failed." }

$engineFolder = Join-Path $engineOutput "RADU.Engine"
Copy-Item (Join-Path $engineFolder "*") $output -Recurse -Force
New-Item (Join-Path $output "model") -ItemType Directory -Force | Out-Null
Copy-Item $model (Join-Path $output "model\vosk-model-small-ru-0.22") -Recurse -Force

dotnet publish (Join-Path $root "prototypes\Radu.Wpf\Radu.Wpf.csproj") `
    -c Release -r win-x64 --self-contained true -o $output
if ($LASTEXITCODE -ne 0) { throw "The Windows desktop application build failed." }

$archive = Join-Path $root "artifacts\RADU-Windows.zip"
Remove-Item $archive -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $output "*") -DestinationPath $archive -CompressionLevel Optimal
Write-Output "Created $archive"

$isccPath = (Get-Command "ISCC.exe" -ErrorAction SilentlyContinue).Source
if (-not $isccPath) {
    $isccPath = Join-Path ([Environment]::GetFolderPath("ProgramFilesX86")) "Inno Setup 6\ISCC.exe"
    if (-not (Test-Path $isccPath)) { $isccPath = $null }
}
if (-not $isccPath) { throw "Inno Setup 6 is required to create RADU-Setup.exe." }

$appVersion = "0.1.0"
if ($env:GITHUB_REF_NAME -match "^v?(\d+\.\d+\.\d+)") { $appVersion = $Matches[1] }
$env:RADU_PACKAGE_DIR = $output
$env:RADU_APP_VERSION = $appVersion
& $isccPath (Join-Path $PSScriptRoot "RADU.Setup.iss")
if ($LASTEXITCODE -ne 0) { throw "The RADU installer build failed." }
$installer = Join-Path $root "artifacts\RADU-Setup.exe"
if (-not (Test-Path $installer)) { throw "The RADU installer was not created." }
Write-Output "Created $installer"
