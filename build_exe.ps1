# Lyra - one command to build the Windows installer (Lyra_1.0.0_x64-setup.exe).
#
#   cd <this folder>
#   powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
#
# Options:
#   -SkipBackend   reuse the last backend build (only rebuild the UI + installer)
#   -Smoke         start the freshly built backend and wait for /health before packaging
#
# Steps: 1) PyInstaller builds backend\dist\lyra-backend\   2) copy it to ui\src-tauri\binaries\lyra-backend\
#        3) npm install + tauri build (bundles the UI, the Rust shell and the backend into one NSIS .exe)
param([switch]$SkipBackend, [switch]$Smoke)
$ErrorActionPreference = "Stop"
$root    = $PSScriptRoot
$backend = Join-Path $root "backend"
$ui      = Join-Path $root "ui"
$bundled = Join-Path $ui "src-tauri\binaries\lyra-backend"

function Need($cmd, $hint) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "'$cmd' not found. $hint" }
}
Need "node"  "Install Node 20+ from https://nodejs.org"
Need "npm"   "Comes with Node."
Need "cargo" "Install Rust from https://rustup.rs (choose the MSVC toolchain + 'Desktop development with C++' build tools)."

# ---- 1) Python backend ------------------------------------------------------
if (-not $SkipBackend) {
    $venvPy = Join-Path $backend "venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPy)) {
        Need "py" "Install Python 3.13 (not 3.14: PyAudio has no wheels there yet)."
        Write-Host "Creating backend venv (Python 3.13)..."
        & py -3.13 -m venv (Join-Path $backend "venv")
        if ($LASTEXITCODE -ne 0) { throw "Could not create the venv. Is Python 3.13 installed?" }
        & $venvPy -m pip install --upgrade pip
        & $venvPy -m pip install -r (Join-Path $backend "requirements.txt")
        if ($LASTEXITCODE -ne 0) { throw "pip install -r requirements.txt failed" }
    }
    $args = @("-ExecutionPolicy", "Bypass", "-File", (Join-Path $backend "build_backend.ps1"))
    if ($Smoke) { $args += "-Smoke" }
    & powershell @args
    if ($LASTEXITCODE -ne 0) { throw "Backend build failed" }
}

$dist = Join-Path $backend "dist\lyra-backend"
if (-not (Test-Path -LiteralPath (Join-Path $dist "lyra-backend.exe"))) {
    throw "backend\dist\lyra-backend\lyra-backend.exe is missing. Run without -SkipBackend."
}

# ---- 2) put the backend where Tauri bundles it ------------------------------
Write-Host "Copying backend into the Tauri bundle..."
if (Test-Path -LiteralPath $bundled) { Remove-Item -LiteralPath $bundled -Recurse -Force }
New-Item -ItemType Directory -Path $bundled | Out-Null
Copy-Item -Path (Join-Path $dist "*") -Destination $bundled -Recurse -Force

# ---- 3) UI + Rust shell + installer -----------------------------------------
Push-Location $ui
try {
    Write-Host "npm install..."
    npm install
    if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
    Write-Host "Type-checking the UI..."
    npm run typecheck
    if ($LASTEXITCODE -ne 0) { Write-Warning "TypeScript reported errors (see above). Continuing: vite does not need a clean typecheck, but please fix them." }
    Write-Host "tauri build (first run compiles Rust: 5-15 min)..."
    npm run tauri:build
    if ($LASTEXITCODE -ne 0) { throw "tauri build failed" }
} finally { Pop-Location }

$setup = Get-ChildItem -Path (Join-Path $ui "src-tauri\target\release\bundle\nsis") -Filter "*.exe" -ErrorAction SilentlyContinue |
         Sort-Object LastWriteTime -Descending | Select-Object -First 1
$portable = Join-Path $ui "src-tauri\target\release\lyra.exe"
Write-Host ""
if ($setup) { Write-Host "INSTALLER: $($setup.FullName)" } else { Write-Host "No installer found - check the tauri output above." }
Write-Host "(Bare app exe, needs the backend folder next to it: $portable)"
