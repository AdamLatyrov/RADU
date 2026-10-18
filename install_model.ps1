$ErrorActionPreference = 'Stop'
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$modelRoot = Join-Path $base 'model'
$modelPath = Join-Path $modelRoot 'vosk-model-small-ru-0.22'
$archivePath = Join-Path $modelRoot 'vosk-model-small-ru-0.22.zip'
$stagingPath = Join-Path $modelRoot 'extract_tmp'
$sourcePath = Join-Path $stagingPath 'vosk-model-small-ru-0.22'
$url = 'https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip'

New-Item -ItemType Directory -Path $modelRoot -Force | Out-Null
if (Test-Path -LiteralPath (Join-Path $modelPath 'am\final.mdl')) {
    Write-Host 'Vosk model already exists.'
    exit 0
}

Write-Host 'Downloading the official Russian Vosk small model.'
Invoke-WebRequest -Uri $url -OutFile $archivePath
Expand-Archive -LiteralPath $archivePath -DestinationPath $stagingPath -Force
if (-not (Test-Path -LiteralPath (Join-Path $sourcePath 'am\final.mdl'))) {
    throw 'The downloaded archive does not contain the expected Vosk model files.'
}
Move-Item -LiteralPath $sourcePath -Destination $modelPath
Remove-Item -LiteralPath $archivePath -Force
Write-Host 'Russian Vosk model is ready.'
