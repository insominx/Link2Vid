# Creates a Desktop shortcut for Doggybag (pin to the taskbar from there).
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

function Resolve-DoggybagPythonw {
    $venvPythonw = Join-Path $repoRoot '.venv\Scripts\pythonw.exe'
    if (Test-Path $venvPythonw) {
        return $venvPythonw
    }

    $pythonwNames = @('pythonw.exe', 'pythonw')
    foreach ($name in $pythonwNames) {
        $candidates = @(Get-Command $name -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
        foreach ($candidate in $candidates) {
            $python = $candidate -replace 'pythonw\.exe$', 'python.exe'
            if (-not (Test-Path $python)) {
                $python = $candidate
            }
            & $python -c 'from doggybag.ui.main_window import VideoDownloaderApp' 2>$null
            if ($LASTEXITCODE -eq 0) {
                return $candidate
            }
        }
    }

    throw 'No Python with Doggybag installed. Run setup_doggybag.bat first.'
}

$pythonw = Resolve-DoggybagPythonw
$entryPoint = Join-Path $repoRoot 'video_downloader.py'
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Doggybag.lnk'

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "`"$entryPoint`""
$shortcut.WorkingDirectory = $repoRoot
$shortcut.Description = 'Doggybag — download videos from URLs'
$shortcut.WindowStyle = 1
$shortcut.Save()

Write-Host "Created shortcut: $shortcutPath"
Write-Host "Right-click it and choose Pin to taskbar, or drag it onto the taskbar."
