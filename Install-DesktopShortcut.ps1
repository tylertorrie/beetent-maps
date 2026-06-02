# Beetent Maps — desktop shortcut installer.
#
# Creates (or overwrites) a "Beetent Maps" shortcut on the current user's
# desktop that launches launch_beetent.bat with assets/logo.ico as the icon.
# Run this once after pulling new code:
#   powershell -ExecutionPolicy Bypass -File .\Install-DesktopShortcut.ps1

$ErrorActionPreference = "Stop"

$repo      = $PSScriptRoot
$target    = Join-Path $repo "launch_beetent.bat"
$iconPath  = Join-Path $repo "assets\logo.ico"
$shortcut  = Join-Path ([Environment]::GetFolderPath("Desktop")) "Beetent Maps.lnk"

if (-not (Test-Path $target)) {
    Write-Error "launch_beetent.bat not found at $target"
    exit 1
}
if (-not (Test-Path $iconPath)) {
    Write-Error "logo.ico not found at $iconPath"
    exit 1
}

$wshell  = New-Object -ComObject WScript.Shell
$lnk     = $wshell.CreateShortcut($shortcut)
$lnk.TargetPath       = $target
$lnk.WorkingDirectory = $repo
$lnk.IconLocation     = "$iconPath,0"
$lnk.Description      = "Beetent Maps - bee shelter layout"
$lnk.WindowStyle      = 7   # 7 = minimised (the .bat flashes, then the GUI takes over)
$lnk.Save()

Write-Host "Created shortcut: $shortcut"
Write-Host "  Target:  $target"
Write-Host "  Icon:    $iconPath"
Write-Host ""
Write-Host "If Windows is caching an old icon, sign out and back in, or run:"
Write-Host "  ie4uinit.exe -ClearIconCache"
