# Build the Lyra backend as a one-folder, windowed PyInstaller bundle.
#   cd <Lyra1>
#   powershell -ExecutionPolicy Bypass -File .\scripts\build_backend.ps1            # build
#   powershell -ExecutionPolicy Bypass -File .\scripts\build_backend.ps1 -Smoke     # build, start it, wait for /health, stop it
# Uses engine\venv's own python.exe, never the system Python. Output: engine\dist\lyra-backend\
param([switch]$Smoke)
$ErrorActionPreference = "Stop"
$engine = Join-Path (Split-Path -Parent $PSScriptRoot) "engine"
Set-Location -LiteralPath $engine

$py = Join-Path $engine "venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $py)) {
    throw "venv not found at $py. Create it first: py -3.13 -m venv venv ; & '.\venv\Scripts\python.exe' -m pip install -r requirements.txt"
}

# Always bring the venv up to date with requirements.txt first (fast when nothing changed). Without this, a venv made before a
# package was added (for example windows-capture, which the recorder needs) builds an exe that asks for "pip install ...".
Write-Host "Installing requirements.txt into the venv..."
& $py -m pip install -r (Join-Path $engine "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install -r requirements.txt failed" }

Write-Host "Installing/Updating PyInstaller in the venv..."
& $py -m pip install --upgrade pyinstaller pyinstaller-hooks-contrib
if ($LASTEXITCODE -ne 0) { throw "pip failed" }

Write-Host "Building lyra-backend (a few minutes)..."
& $py -m PyInstaller --noconfirm --clean --distpath "dist" --workpath "build" "lyra-backend.spec"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$exe = Join-Path $engine "dist\lyra-backend\lyra-backend.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "Build finished but $exe is missing" }
$mb = [math]::Round(((Get-ChildItem -LiteralPath (Split-Path $exe) -Recurse | Measure-Object Length -Sum).Sum) / 1MB)
Write-Host "OK: $exe  ($mb MB folder)"

if ($Smoke) {
    Write-Host "Smoke test: starting the backend, waiting up to 90 s for http://127.0.0.1:8765/health ..."
    $p = Start-Process -FilePath $exe -PassThru
    $ok = $false
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 1
        try {
            $r = Invoke-RestMethod -Uri "http://127.0.0.1:8765/health" -TimeoutSec 2
            if ($r.ok) { $ok = $true; Write-Host ("health: " + ($r | ConvertTo-Json -Compress)); break }
        } catch { }
        if ($p.HasExited) { break }
    }
    if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force }
    if ($ok) { Write-Host "Smoke test PASSED. Log: $env:APPDATA\Lyra\ui.log" }
    else { Write-Host "Smoke test FAILED. Read $env:APPDATA\Lyra\ui.log"; exit 1 }
}
