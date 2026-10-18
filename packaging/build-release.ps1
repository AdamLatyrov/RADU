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
& $buildPython -m pip install --disable-pip-version-check -r (Join-Path $root "requirements-release.txt")
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
