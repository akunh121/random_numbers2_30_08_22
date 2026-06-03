' Launch update_local.bat fully hidden (no console window).
' Used by Task Scheduler so the daemon runs invisibly in the background.

Dim fso, ws, scriptDir
Set fso = CreateObject("Scripting.FileSystemObject")
Set ws  = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
ws.CurrentDirectory = scriptDir

' 0 = WindowStyle Hidden; False = don't wait for completion.
ws.Run """" & scriptDir & "\update_local.bat""", 0, False
