# Creates a Desktop shortcut for the packaged Link2Vid build (release\Link2Vid).
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$appDir = Join-Path $repoRoot 'release\Link2Vid'
$launcher = Join-Path $appDir 'Link2Vid.bat'

if (-not (Test-Path $launcher)) {
    throw "Packaged app not found. Run build_windows.bat first. Expected: $launcher"
}

$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Link2Vid (packaged).lnk'

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcher
$shortcut.WorkingDirectory = $appDir
$shortcut.Description = 'Link2Vid — packaged Windows build'
$shortcut.WindowStyle = 1
$shortcut.Save()

Write-Host "Created shortcut: $shortcutPath"
Write-Host "Shortcut starts Link2Vid via release\Link2Vid\Link2Vid.bat"
