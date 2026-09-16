' AICreditPunch: run checkin.bat in a hidden console window.
'
' The scheduled tasks point at this wrapper (wscript.exe run-hidden.vbs
' "...\checkin.bat") so the daily runs, the logon run and the wake-up run do
' not flash an empty cmd window on screen. Running checkin.bat by hand is
' unaffected and still shows its output.
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
Set sh = CreateObject("WScript.Shell")
q = Chr(34)
rc = sh.Run("cmd.exe /c " & q & bat & q, 0, True)
WScript.Quit rc
