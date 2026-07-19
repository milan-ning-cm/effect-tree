' Effect Tree launcher (no console window).
' Usage: double-click = start server + open browser.
'        "silent" arg  = start server only (for Startup folder auto-run).
' ASCII only in this file: cscript/wscript reads ANSI/UTF-16, UTF-8 CJK breaks.
Dim fso, here, ws, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
Set ws = CreateObject("WScript.Shell")
ws.CurrentDirectory = here
cmd = "py """ & here & "\tree.py"""
If WScript.Arguments.Count > 0 Then
  If WScript.Arguments(0) = "silent" Then cmd = cmd & " --no-browser"
End If
ws.Run cmd, 0, False
