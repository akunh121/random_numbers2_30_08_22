# Pop up a Windows InputBox asking for the GitHub Personal Access Token.
# Writes the entered text to stdout (consumed by setup.bat).

Add-Type -AssemblyName Microsoft.VisualBasic
$nl = [Environment]::NewLine
$msg = "Paste your GitHub Personal Access Token below." + $nl + $nl +
       "Required permissions on this repository:" + $nl +
       "  Contents = Read and write" + $nl + $nl +
       "Create one at https://github.com/settings/tokens?type=beta"
$token = [Microsoft.VisualBasic.Interaction]::InputBox(
    $msg, "Lotto Updater - GitHub Token", ""
)
Write-Output $token
