#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$AppName = ([char]0x0423).ToString() + [char]0x0412 + [char]0x042B + " control"
$InstallDir = Join-Path $env:LOCALAPPDATA "UVYControl"
$Desktop = [Environment]::GetFolderPath("Desktop")
$StartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"

Write-Host "Removing $AppName..."
$links = @(
    (Join-Path $Desktop "$AppName.lnk"),
    (Join-Path $StartMenu "$AppName.lnk")
)
foreach ($link in $links) {
    if (Test-Path $link) { Remove-Item -Force $link }
}

if (Test-Path $InstallDir) {
    Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "Done."
