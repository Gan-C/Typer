' Typer - silent launcher: starts the app with NO console window.
' Double-click this instead of run_typer.bat for everyday use.
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDir  = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = appDir
' Mainland-China mirror for the one-time model download (harmless elsewhere).
sh.Environment("PROCESS")("HF_ENDPOINT") = "https://hf-mirror.com"
' pythonw.exe runs Python with no console window.
sh.Run "pythonw """ & appDir & "\typer_app.py""", 0, False
