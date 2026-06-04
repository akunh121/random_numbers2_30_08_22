# Find an existing LottoUpdaterDaemon installation and print its
# directory (with trailing backslash). Empty output = no existing
# install. Called from setup.bat to know where to import the token
# from before overwriting the scheduled task.

$ErrorActionPreference = 'SilentlyContinue'
try {
    $raw = schtasks /query /tn 'LottoUpdaterDaemon' /xml 2>$null
    if (-not $raw) { return }
    $xml = [xml]$raw
    $args = $xml.Task.Actions.Exec.Arguments
    if ($args -match '"(.+?\\)run_hidden\.vbs') {
        Write-Output $matches[1]
    }
} catch { }
