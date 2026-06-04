# Find an existing LottoUpdaterDaemon installation and print its
# directory (with trailing backslash). Empty output = no existing
# install. Called from setup.bat to know where to import the token
# from before overwriting the scheduled task.
#
# Why regex-on-text and not [xml]: Task Scheduler XML uses a default
# namespace (http://schemas.microsoft.com/windows/2004/02/mit/task) so
# $xml.Task.Actions.Exec.Arguments returns $null and the dotted-path
# trick silently fails. Searching the raw text avoids the namespace
# trap entirely and works for either of the two launchers we have
# shipped (run_hidden.vbs from the latest installer, or update_local.bat
# from earlier ones).

$ErrorActionPreference = 'SilentlyContinue'
try {
    $raw = (schtasks /query /tn 'LottoUpdaterDaemon' /xml 2>$null) -join "`n"
    if (-not $raw) { return }
    if ($raw -match '([A-Za-z]:\\[^<"]*?\\)(?:run_hidden\.vbs|update_local\.bat)') {
        Write-Output $matches[1]
    }
} catch { }
