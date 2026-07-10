' WorkGraph hidden launcher for Task Scheduler
Option Explicit

Dim shell, fso, scriptDir, args, i, arg, command

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

args = ""
For i = 0 To WScript.Arguments.Count - 1
    arg = WScript.Arguments(i)
    If InStr(arg, """") > 0 Then
        arg = Replace(arg, """", """""")
    End If
    args = args & " """ & arg & """"
Next

command = "cmd.exe /c cd /d """ & scriptDir & """ && uv run python main.py" & args

' Window style 0 = hidden, waitOnReturn False = keep task alive while child process runs.
shell.Run command, 0, False
