$desktop = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)
$shortcutPath = Join-Path $desktop "SanAlgo.lnk"
$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "C:\Users\Admin\Documents\code\Gbot\SanAlgo.exe"
$shortcut.WorkingDirectory = "C:\Users\Admin\Documents\code\Gbot"
$shortcut.IconLocation = "C:\Users\Admin\Documents\code\Gbot\logo.ico,0"
$shortcut.Description = "SanAlgo - VAR_117 Multi-Asset ETF Momentum Rotation Engine"
$shortcut.Save()

if (Test-Path $shortcutPath) {
    Write-Output "SUCCESS: Shortcut created at $shortcutPath"
} else {
    Write-Output "ERROR: Failed to create shortcut"
}
