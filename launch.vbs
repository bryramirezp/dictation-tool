' Starts Kara with no console window.
' Put a shortcut to this file in shell:startup to have it run at login.
Option Explicit

Dim fso, shell, appDir, script, sysRoot, cmd
Set fso   = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

appDir  = fso.GetParentFolderName(WScript.ScriptFullName)
script  = """" & appDir & "\kara.py"""
sysRoot = shell.ExpandEnvironmentStrings("%SystemRoot%")

' Pin a specific interpreter with:  setx KARA_PYTHON "C:\Path\To\pythonw.exe"
' DICTATION_PYTHON is the old name, still honoured after the rename.
Dim pinned
pinned = shell.ExpandEnvironmentStrings("%KARA_PYTHON%")
If pinned = "%KARA_PYTHON%" Then
    pinned = shell.ExpandEnvironmentStrings("%DICTATION_PYTHON%")
    If pinned = "%DICTATION_PYTHON%" Then pinned = ""
End If
If pinned <> "" And fso.FileExists(pinned) Then
    cmd = """" & pinned & """ " & script

' The py launcher ships with every python.org install and picks the newest
' interpreter itself, so this works regardless of which one is on PATH.
ElseIf fso.FileExists(sysRoot & "\pyw.exe") Then
    cmd = """" & sysRoot & "\pyw.exe"" -3 " & script
Else
    cmd = "pythonw.exe " & script
End If

On Error Resume Next
shell.Run cmd, 0, False
If Err.Number <> 0 Then
    MsgBox "Python 3.10 or newer was not found." & vbCrLf & vbCrLf & _
           "Install it from https://www.python.org/downloads/" & vbCrLf & _
           "and tick ""Add python.exe to PATH"" during setup.", _
           48, "Kara"
End If
