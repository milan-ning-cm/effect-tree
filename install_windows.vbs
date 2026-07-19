' Effect Tree shortcut installer (Windows).
' Double-click (or: cscript install_shortcuts.vbs) to create:
'   1) Start Menu > "Effect Tree"        - opens the editor (starts server if needed)
'   2) Startup    > "Effect Tree Server" - silently starts server at logon
' After install: press Win key, search "Effect Tree", right-click > Pin to taskbar.
' ASCII only in this file (cscript reads ANSI; UTF-8 CJK breaks).
Dim fso, here, ws, lnk
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
Set ws = CreateObject("WScript.Shell")

Set lnk = ws.CreateShortcut(ws.SpecialFolders("Programs") & "\Effect Tree.lnk")
lnk.TargetPath = "C:\Windows\System32\wscript.exe"
lnk.Arguments = """" & here & "\launcher.vbs"""
lnk.WorkingDirectory = here
lnk.Description = "Effect Tree editor (auto-starts server, opens browser)"
lnk.IconLocation = "%SystemRoot%\System32\shell32.dll, 41"
lnk.Save

Set lnk = ws.CreateShortcut(ws.SpecialFolders("Startup") & "\Effect Tree Server.lnk")
lnk.TargetPath = "C:\Windows\System32\wscript.exe"
lnk.Arguments = """" & here & "\launcher.vbs"" silent"
lnk.WorkingDirectory = here
lnk.Description = "Effect Tree server (silent autostart at logon)"
lnk.IconLocation = "%SystemRoot%\System32\shell32.dll, 41"
lnk.Save

msg = "Installed:" & vbCrLf & _
  "  Start Menu > Effect Tree  (pin it to taskbar: Win key > search > right-click)" & vbCrLf & _
  "  Startup > Effect Tree Server  (auto-starts at logon)"
If InStr(LCase(WScript.FullName), "cscript") > 0 Then
  WScript.Echo msg          ' agent/terminal run: plain text, no dialog
Else
  MsgBox msg, vbInformation, "Effect Tree"
End If
