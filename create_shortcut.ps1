param([Parameter(Mandatory = $true)][string]$Target)

if (-not (Test-Path $Target)) { Write-Error "App not found: $Target"; exit 1 }

$name  = "Music Sync Checker"
$shell = New-Object -ComObject WScript.Shell
$links = @(
    (Join-Path ([Environment]::GetFolderPath('Programs')) "$name.lnk"),  # Start Menu
    (Join-Path ([Environment]::GetFolderPath('Desktop'))  "$name.lnk")   # Desktop
)

foreach ($path in $links) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath       = $Target
    $lnk.WorkingDirectory = Split-Path $Target
    $lnk.IconLocation     = "$Target,0"
    $lnk.Description      = "Check that songs on your MP3 player are in sync with your master music folder"
    $lnk.Save()
    Write-Host "    created $path"
}
