# Creates a Desktop shortcut for the packaged Doggybag build (release\Doggybag).
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$appDir = Join-Path $repoRoot 'release\Doggybag'
$launcher = Join-Path $appDir 'Doggybag.bat'

if (-not (Test-Path $launcher)) {
    throw "Packaged app not found. Run build_windows.bat first. Expected: $launcher"
}

$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Doggybag (packaged).lnk'

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcher
$shortcut.WorkingDirectory = $appDir
$shortcut.Description = 'Doggybag — packaged Windows build'
$shortcut.WindowStyle = 1
$shortcut.Save()

Write-Host "Created shortcut: $shortcutPath"
Write-Host "Shortcut starts Doggybag via release\Doggybag\Doggybag.bat"
