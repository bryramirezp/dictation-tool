Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
py = "C:\Users\Bryan\AppData\Local\Programs\Python\Python311\pythonw.exe"
If Not fso.FileExists(py) Then py = "pythonw.exe"
CreateObject("WScript.Shell").Run """" & py & """ """ & appDir & "\dictation_tool.py""", 0, False
