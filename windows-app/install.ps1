#Requires -Version 5.1
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$AppName = ([char]0x0423).ToString() + [char]0x0412 + [char]0x042B + " control"
$SourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallDir = Join-Path $env:LOCALAPPDATA "UVYControl"
$Desktop = [Environment]::GetFolderPath("Desktop")
$StartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"

function Write-Step($text) {
    Write-Host ""
    Write-Host "==> $text" -ForegroundColor Yellow
}

function Find-Python {
    foreach ($cmd in @("py", "python", "python3")) {
        $item = Get-Command $cmd -ErrorAction SilentlyContinue
        if (-not $item) { continue }
        try {
            $ver = & $item.Source -c "import sys; assert sys.version_info >= (3, 10); print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $ver) { return $ver.Trim() }
        } catch { }
    }
    return $null
}

function Install-Python {
    Write-Step "Python not found. Installing Python 3.12 via winget..."
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "Python is missing. Install Python 3.12 from https://www.python.org/downloads/ and enable Add python.exe to PATH."
    }
    & winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements --disable-interactivity
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    $py = Find-Python
    if (-not $py) {
        throw "Python installed but PATH is stale. Close this window and run the installer again."
    }
    return $py
}

function New-AppShortcut($path, $target, $arguments, $workdir, $icon) {
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $target
    $lnk.Arguments = $arguments
    $lnk.WorkingDirectory = $workdir
    $lnk.WindowStyle = 7
    $lnk.Description = $AppName
    if (Test-Path $icon) {
        $lnk.IconLocation = "$icon,0"
    }
    $lnk.Save()
}

Write-Host "======================================" -ForegroundColor Yellow
Write-Host "  $AppName installer" -ForegroundColor Yellow
Write-Host "======================================"

$required = @(
    "asic_control.py",
    "requirements.txt",
    "uvy-logo.png",
    "uvy-logo.ico",
    "uninstall.ps1",
    "uninstall.bat"
)
foreach ($name in $required) {
    if (-not (Test-Path (Join-Path $SourceDir $name))) {
        throw "Missing file: $name"
    }
}

$python = Find-Python
if (-not $python) {
    $python = Install-Python
}
Write-Step "Python: $python"

Write-Step "Copy files to $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item -Force (Join-Path $SourceDir "asic_control.py") $InstallDir
Copy-Item -Force (Join-Path $SourceDir "requirements.txt") $InstallDir
Copy-Item -Force (Join-Path $SourceDir "uvy-logo.png") $InstallDir
Copy-Item -Force (Join-Path $SourceDir "uvy-logo.ico") $InstallDir
Copy-Item -Force (Join-Path $SourceDir "uninstall.ps1") $InstallDir
Copy-Item -Force (Join-Path $SourceDir "uninstall.bat") $InstallDir

$venvPython = Join-Path $InstallDir ".venv\Scripts\python.exe"
$venvPythonw = Join-Path $InstallDir ".venv\Scripts\pythonw.exe"

Write-Step "Create venv and install dependencies"
if (-not (Test-Path $venvPython)) {
    & $python -m venv (Join-Path $InstallDir ".venv")
    if ($LASTEXITCODE -ne 0) { throw "venv failed. Install Python from python.org (not Microsoft Store)." }
}
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip failed" }
& $venvPython -m pip install -r (Join-Path $InstallDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "dependency install failed" }

& $venvPython -c "import tkinter, customtkinter, httpx, PIL, pystray, winotify"
if ($LASTEXITCODE -ne 0) {
    throw "Imports failed. Reinstall Python from python.org with tcl/tk enabled."
}

$scriptPath = Join-Path $InstallDir "asic_control.py"
$launcher = Join-Path $InstallDir "launch.vbs"
@"
Set sh = CreateObject("Wscript.Shell")
sh.CurrentDirectory = "$InstallDir"
sh.Run """$venvPythonw"" ""$scriptPath""", 0, False
"@ | Set-Content -Path $launcher -Encoding ASCII

Write-Step "Create desktop shortcut with logo"
$desktopLnk = Join-Path $Desktop "$AppName.lnk"
$startLnk = Join-Path $StartMenu "$AppName.lnk"
$icon = Join-Path $InstallDir "uvy-logo.ico"
$wscript = Join-Path $env:SystemRoot "System32\wscript.exe"
New-AppShortcut $desktopLnk $wscript "`"$launcher`"" $InstallDir $icon
New-AppShortcut $startLnk $wscript "`"$launcher`"" $InstallDir $icon

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "Desktop shortcut: $desktopLnk"
Write-Host "Install folder:   $InstallDir"
Write-Host ""
Write-Host "Launch the desktop shortcut to start the app."
Write-Host "Uninstall: $InstallDir\uninstall.bat"
Write-Host ""
