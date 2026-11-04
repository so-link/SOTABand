param(
    [string]$Uv = 'uv',
    [string]$Wheelhouse = '',
    [switch]$WheelhouseOnly,
    [switch]$WithVerification
)
$ErrorActionPreference = 'Stop'
if ($WheelhouseOnly -and -not $Wheelhouse) { throw '-WheelhouseOnly requires -Wheelhouse' }
$repo = Split-Path -Parent $PSScriptRoot
$previousCache = $env:UV_CACHE_DIR
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
if (-not $env:UV_CACHE_DIR) { $env:UV_CACHE_DIR = Join-Path $repo '.runtime/uv-cache' }
$env:TEMP = Join-Path $repo '.runtime/install-temp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force $env:UV_CACHE_DIR, $env:TEMP | Out-Null
Push-Location $repo
try {
    $uvOptions = @()
    $packageOptions = @()
    if ($Wheelhouse) { $packageOptions += @('--find-links', $Wheelhouse) }
    if ($WheelhouseOnly) {
        $uvOptions += '--offline'
        $packageOptions += '--no-index'
    }
    & $Uv @uvOptions python install 3.12.11
    if ($LASTEXITCODE) { throw 'Python installation failed' }
    foreach ($item in @(@('.venv-platform', 'requirements/platform.lock'), @('.venv-hypoxemia', 'requirements/hypoxemia-cpu.lock'))) {
        $interpreter = Join-Path $item[0] 'Scripts/python.exe'
        if (-not (Test-Path -LiteralPath $interpreter)) {
            & $Uv @uvOptions venv --python 3.12.11 $item[0]
            if ($LASTEXITCODE) { throw 'Environment creation failed' }
        }
        & $interpreter -c 'import sys; assert sys.version_info[:3] == (3, 12, 11)'
        if ($LASTEXITCODE) { throw 'Unexpected interpreter version' }
        & $Uv @uvOptions pip sync --python $interpreter $item[1] --index-strategy unsafe-best-match @packageOptions
        if ($LASTEXITCODE) { throw "Dependency installation failed: $($item[0])" }
    }
    if ($WithVerification) {
        & $Uv @uvOptions pip sync --python '.venv-platform/Scripts/python.exe' requirements/platform-verification.lock @packageOptions
        if ($LASTEXITCODE) { throw 'Verification dependency installation failed' }
    }
    & '.venv-hypoxemia/Scripts/python.exe' -c 'import sys, torch, numpy, pandas, sklearn; print(sys.version); print(torch.__version__)'
    if ($LASTEXITCODE) { throw 'Compute environment check failed' }
    Write-Output 'Installed. Start the platform with .venv-platform/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8002'
} finally {
    $env:UV_CACHE_DIR = $previousCache
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
    Pop-Location
}
