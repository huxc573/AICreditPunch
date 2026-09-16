' AICreditPunch: run checkin.bat in a hidden console window.
'
' The scheduled tasks point at this wrapper (wscript.exe run-hidden.vbs
' "...\checkin.bat --auto") so the daily runs, the logon run and the wake-up
' run do not flash an empty cmd window on screen. --auto tells the bat that a
' task triggered it: no console output, no pause. Running checkin.bat by hand
' passes no such switch, so it prints the run and pauses on double-click.
'
' window style 0 = hidden; True makes the vbs wait for the bat to finish, so
' the task's "last run time / last result" stays accurate and the 10-minute
' execution limit still applies. The cmd exit code is passed through via
' WScript.Quit.
'
' Keep this file ASCII-only with CRLF line endings, like checkin.bat.
Option Explicit
Dim sh, bat, q, rc
If WScript.Arguments.Count < 1 Then
  WScript.Quit 2
End If
bat = WScript.Arguments(0)
' The bat must receive exactly one silent switch: a second one falls through to
' the "forward to python" branch and stalls the task on its pause. The task
' argument already carries the switch, so only add it when it is missing.
If InStr(1, bat, "--auto", 1) = 0 Then
  bat = bat & " --auto"
End If
Set sh = CreateObject("WScript.Shell")
q = Chr(34)
rc = sh.Run("cmd.exe /c " & q & bat & q, 0, True)
WScript.Quit rc
