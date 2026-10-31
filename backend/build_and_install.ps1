# Lyrr - build a standalone Lyrr.exe and register it to run at login.
# Run from the project folder (where app_qt.py lives):
#   powershell -ExecutionPolicy Bypass -File build_and_install.ps1

Write-Host "Installing PyInstaller..."
pip install pyinstaller

Write-Host "Building Lyrr.exe (this takes a minute or two)..."
pyinstaller --onefile --name Lyrr --icon=NONE app_qt.py

$exePath = Join-Path (Get-Location) "dist\Lyrr.exe"
if (-not (Test-Path $exePath)) {
    Write-Host "Build failed - Lyrr.exe wasn't created. Check the PyInstaller output above."
    exit 1
}

Write-Host "Registering Lyrr to start at login..."
$startupDir = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
$shortcutPath = Join-Path $startupDir "Lyrr.lnk"

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($shortcutPath)
$Shortcut.TargetPath = $exePath
$Shortcut.WorkingDirectory = Split-Path $exePath
$Shortcut.Save()

Write-Host ""
Write-Host "Done. Lyrr.exe is at: $exePath"
Write-Host "Startup shortcut created: $shortcutPath"
Write-Host "It'll launch automatically next time you log in. To test right now, run:"
Write-Host "  $exePath"
