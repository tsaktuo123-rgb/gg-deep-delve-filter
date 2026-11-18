' GG DEEP DELVE - hidden watcher launcher.
' Runs Price_My_Stash.bat --watch with window style 0 = no visible window,
' no taskbar entry. Used by the "GG Deep Delve Auto Watcher" scheduled task.
' Monitor the live feed with scripts\Monitor_Watcher.cmd (tails watcher.log).
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.Run "cmd /c """ & dir & "\Price_My_Stash.bat"" --watch", 0, False
