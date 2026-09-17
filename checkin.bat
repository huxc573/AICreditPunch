@echo off
setlocal

rem ===================================================================
rem  AICreditPunch - Windows entry point
rem
rem    (no argument)             run the check-in, auto-install the tasks
rem    --install / --uninstall   (re)install / remove the three tasks
rem    --logs / --help           open the log / show usage
rem
rem  Subcommands are --prefixed; only help also answers to -h, /? and help.
rem  Any other --option goes straight to checkin.py, e.g. --tasks (task check,
rem  read-only), --today (daily view), --status-only, --version.
rem
rem  --auto is internal: the tasks pass it so a triggered run stays silent.
rem  A manual run shows the same text on the console and in the log -
rem  checkin.py prints it and appends it to ACP_RUN_LOG, this file prepends
rem  that temp file to the log.
rem
rem  Tasks managed here (README 3.3): AICreditPunch-Daily (six triggers a day,
rem  StartWhenAvailable), -Startup (at logon), -Resume (on resume from sleep,
rem  registered from scheduled-task.resume.xml - an event trigger cannot be
rem  expressed with New-ScheduledTaskTrigger).
rem
rem  HARD RULES - this file must stay ASCII-only with CRLF line endings: non
rem  ASCII or LF breaks cmd.exe under a GBK code page. Never echo %date% or
rem  %time% (localized text corrupts the UTF-8 log - use :now), and never put
rem  parentheses into echo text inside an if-block (cmd closes the block early
rem  and the script dies with a syntax error).
rem ===================================================================

rem The folder is resolved from this file's own location, so the project
rem can be renamed or moved without editing anything here.
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

rem Change PYEXE if your Python installation lives elsewhere.
set "PYEXE=D:\Dev\Python\Env\Python312\python.exe"
rem Force UTF-8 so Chinese log output is not garbled.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
rem Do NOT set WORKBUDDY_NOTIFY=true here: checkin.py owns notification
rem decision-making and the upstream flag would duplicate every run.
set "LOGFILE=%APPDATA%\AICreditPunch.log"
rem "please open the log" flag written by checkin.py - see :open_log.
rem It sits next to the log file so both sides agree on the location.
for %%I in ("%LOGFILE%") do set "LOG_DIR=%%~dpI"
set "OPEN_LOG_FLAG=%LOG_DIR%.AICreditPunch.openlog"
set "TASK_DAILY=AICreditPunch-Daily"
set "TASK_STARTUP=AICreditPunch-Startup"
set "TASK_RESUME=AICreditPunch-Resume"
set "CHECKIN_TIMES=08:45 11:45 14:45 17:45 20:45 23:45"
rem Host probed while waiting for the network: the real API host, so a
rem successful probe means the check-in itself can go through.
set "PROBE_HOST=www.workbuddy.cn"
set "REG_FAILED="
set "LOG_OPENED="

rem ---------------------------------------------------------------- subcommands
rem Every subcommand is --prefixed, except the help ones: --help, -h, /? and
rem the bare word help all print the usage. The bare words install, uninstall
rem and logs are NOT valid any more.
set "ARG1=%~1"
set "AUTO="

rem Internal switch for the scheduled tasks: keep the triggered run silent.
rem This is a goto, not an if-block: inside a block %1 is expanded before
rem shift takes effect, so ARG1 would still read --auto and get forwarded.
if /i not "%ARG1%"=="--auto" goto after_auto
set "AUTO=1"
rem ACP_QUIET tells checkin.py to skip the console and only fill the log copy.
set "ACP_QUIET=1"
shift
set "ARG1=%~1"
:after_auto

if /i "%ARG1%"=="--uninstall" goto uninstall
if /i "%ARG1%"=="--install"   goto tasks_force
if /i "%ARG1%"=="--logs"      goto logs
if /i "%ARG1%"=="--help"      goto usage
if /i "%ARG1%"=="help"        goto usage
if /i "%ARG1%"=="-h"          goto usage
if /i "%ARG1%"=="/?"          goto usage

rem Anything else starting with -- belongs to checkin.py: forward it as-is.
if "%ARG1:~0,2%"=="--" goto forward

if defined ARG1 (
    echo Unknown argument: %ARG1%
    echo.
    set "RC=2"
    goto usage
)

rem ---------------------------------------------------------------- default run
cd /d "%SCRIPT_DIR%" || exit /b 1

rem ---------------------------------------------------------------- single instance
rem Two concurrent runs fight over the log: :publish_block rewrites the whole file
rem (copy /b into a temp file, then move over the log), so an overlapping run can
rem read a half-written log - that is how a truncated line with broken UTF-8 got
rem into the log. mkdir is atomic (it either creates the directory or fails), so
rem it doubles as a mutex. A run that cannot take the lock exits instead of
rem racing: the other run is doing the same check-in, so nothing is missed. A lock
rem older than 15 minutes is a leftover from a killed run and gets removed.
set "LOCKDIR=%TEMP%\acp_checkin_%USERNAME%.lock"
set "LOCKTRY=0"
:lock_try
mkdir "%LOCKDIR%" 2>nul
if not errorlevel 1 goto lock_ok
set /a LOCKTRY+=1
if %LOCKTRY% GEQ 6 goto lock_old
ping -n 3 127.0.0.1 >nul 2>&1
goto lock_try

:lock_old
for /f "delims=" %%i in ('powershell -NoProfile -Command "if(((Get-Date)-(Get-Item -LiteralPath ($env:TEMP+'\acp_checkin_'+$env:USERNAME+'.lock')).LastWriteTime).TotalMinutes -gt 15){'stale'}else{'busy'}" 2^>nul') do set "LOCKSTATE=%%i"
if /i not "%LOCKSTATE%"=="stale" goto lock_busy
rd /s /q "%LOCKDIR%" >nul 2>&1
mkdir "%LOCKDIR%" 2>nul
if not errorlevel 1 goto lock_ok
:lock_busy
if not defined AUTO echo Another check-in is already running - this run exits.
exit /b 0

:lock_ok

set "TMPLOG=%TEMP%\acp_run_%RANDOM%%RANDOM%.tmp"
set "NEWLOG=%TEMP%\acp_new_%RANDOM%%RANDOM%.tmp"

rem Tell checkin.py that this .bat owns the log: on the first successful
rem check-in of the day it only leaves a flag file, and :open_log below
rem opens the log AFTER this run has been prepended to the top.
set "ACP_LOG_OWNER=bat"
rem Same text on the console and in the log: python prints to this console and
rem tees every line into ACP_RUN_LOG, which :publish_block prepends to the log.
rem Delete any stale file first so a leftover temp file is never reused.
set "ACP_RUN_LOG=%TMPLOG%"
if exist "%TMPLOG%" del "%TMPLOG%" >nul 2>&1

rem ---------------------------------------------------------------- task autosetup
rem Register the tasks when they are missing OR still point at an old
rem folder or file name, e.g. after the project was renamed. A failed
rem registration is non-fatal: the check-in still runs and then the log
rem is opened so the user can see why it failed.
call :tasks_ok
if errorlevel 1 call :register_tasks

rem ---------------------------------------------------------------- wait for network
rem At boot/login WiFi is often not ready yet, so wait for it rather than let the
rem check-in fail against a dead link. ping is the fast gate: a native exe, no
rem interpreter startup, answers within 1s, and the loop leaves the moment it
rem answers. 100 rounds of (<=1s ping + 2s gap) is about 5 minutes; after that
rem the run continues with short timeouts. ICMP can be blocked where TCP still
rem works, so a ping that never answers gets one 443 handshake re-check with a
rem HARD 3s timeout. timeout /t is skipped when stdin is redirected, so a piped
rem or hosted run gives up sooner - it never waits longer than this.
set "N=0"
if not defined AUTO echo Checking the network ...
:waitnet
ping -n 1 -w 1000 %PROBE_HOST% >nul 2>&1
if not errorlevel 1 goto netok
set /a N+=1
if %N% LSS 100 (
    timeout /t 2 /nobreak >nul 2>&1
    goto waitnet
)
rem "if not errorlevel 1" is used below on purpose - %ERRORLEVEL% would be
rem expanded when the block is parsed and would read a stale value.
if exist "%PYEXE%" (
    "%PYEXE%" -c "import socket; socket.create_connection(('%PROBE_HOST%', 443), 3).close()" >nul 2>&1
    if not errorlevel 1 goto netok
)
rem Still dead. Run anyway with short timeouts: on a link that swallows packets
rem every request would burn timeout x attempts + backoff ~= 63s, and the run
rem would pass the task's 10 minute execution limit. Windows then kills it
rem before :publish_block, so the run never reaches the log at all.
set "WORKBUDDY_TIMEOUT=10"
set "WORKBUDDY_RETRIES=0"
call :now
call :say WARN: network not reachable after ~5min, running anyway with short timeouts.
:netok

call :now
if not exist "%PYEXE%" (
    call :say ERROR: python not found: %PYEXE%
    rem Blank line so this block stays separated from the previous run in the log.
    echo.>>"%TMPLOG%"
    call :publish_block
    if defined REG_FAILED call :open_log
    call :release_lock
    exit /b 127
)

rem No redirection: python writes to this console and tees into %TMPLOG%.
"%PYEXE%" checkin.py
set "RC=%ERRORLEVEL%"
call :publish_block
rem Open the log when task registration failed, and also when checkin.py
rem asked for it. Both happen AFTER the prepend above, so notepad shows
rem this run's block at the top instead of the previous one.
if defined REG_FAILED call :open_log
if exist "%OPEN_LOG_FLAG%" (
    del "%OPEN_LOG_FLAG%" >nul 2>&1
    call :open_log
)
if defined AUTO goto done_no_hint
echo.
echo Log written to the top of: "%LOGFILE%"
:done_no_hint
if not defined AUTO call :pause_if_double_clicked
call :release_lock
exit /b %RC%

rem ================================================================ subroutines

:release_lock
rem Drop the single-instance lock; harmless when it was never taken.
rd /s /q "%LOCKDIR%" >nul 2>&1
exit /b 0

:forward
rem A --option meant for checkin.py. Views such as --tasks / --today are
rem read-only, so this path never touches the log.
cd /d "%SCRIPT_DIR%" || exit /b 1
if not exist "%PYEXE%" (
    echo ERROR: python not found: "%PYEXE%"
    call :pause_if_double_clicked
    exit /b 127
)
"%PYEXE%" checkin.py %*
set "RC=%ERRORLEVEL%"
call :pause_if_double_clicked
exit /b %RC%

:open_log
rem Open the log in notepad, at most once per run. Via Start-Process, not
rem "start notepad": "start" hands the child this script's stdout/stderr
rem handles, so a piping caller would block until notepad is closed.
if defined LOG_OPENED exit /b 0
set "LOG_OPENED=1"
if not exist "%LOGFILE%" exit /b 0
powershell -NoProfile -Command "Start-Process notepad -ArgumentList $env:LOGFILE" >nul 2>&1
exit /b 0

:logs
rem checkin.bat --logs: hand the local log file to notepad on request.
rem Reads only - it never writes a log block of its own.
if exist "%LOGFILE%" goto logs_open
echo.
echo Log file not found: "%LOGFILE%"
echo It is created by the first check-in run.
echo.
call :pause_if_double_clicked
exit /b 1
:logs_open
rem Detached launch, same reason as :open_log - never inherit our handles.
powershell -NoProfile -Command "Start-Process notepad -ArgumentList $env:LOGFILE" >nul 2>&1
echo.
echo Opened in notepad: "%LOGFILE%"
echo.
call :pause_if_double_clicked
exit /b 0

:publish_block
rem Each run becomes the FIRST block of the log file - newest first.
rem Concatenate this run's temp file in front of the existing log.
if not exist "%TMPLOG%" exit /b 0
if exist "%LOGFILE%" (
    copy /b /y "%TMPLOG%" + "%LOGFILE%" "%NEWLOG%" >nul 2>&1
    if exist "%NEWLOG%" (
        move /y "%NEWLOG%" "%LOGFILE%" >nul 2>&1
    ) else (
        copy /y "%TMPLOG%" "%LOGFILE%" >nul 2>&1
    )
) else (
    copy /y "%TMPLOG%" "%LOGFILE%" >nul 2>&1
)
del "%TMPLOG%" >nul 2>&1
exit /b 0

:say
rem One ASCII-only line into this run's temp log, echoed too on a manual run.
rem Redirect first, then echo - the other order leaves a trailing space on
rem every log line. Keep the message free of parentheses.
set "SAYLINE=%*"
rem Bail out before echo: "echo" with an empty value prints "ECHO is on."
if not defined SAYLINE exit /b 0
if defined NOW set "SAYLINE=[%NOW%] %*"
if defined AUTO goto say_log_only
echo %SAYLINE%
:say_log_only
>>"%TMPLOG%" echo %SAYLINE%
set "SAYLINE="
exit /b 0

:now
rem Locale-independent timestamp. "-Format s" is the sortable ISO form
rem 2026-09-14T21:44:12 - no inner quotes, no localized names. Retry once,
rem then give up quietly: %date% would corrupt the UTF-8 log.
set "NOW="
for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format s" 2^>nul') do set "NOW=%%i"
if not defined NOW for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format s" 2^>nul') do set "NOW=%%i"
if defined NOW set "NOW=%NOW:T= %"
exit /b 0

:tasks_force
cd /d "%SCRIPT_DIR%" || exit /b 1
set "TMPLOG=%TEMP%\acp_run_%RANDOM%.tmp"
set "NEWLOG=%TEMP%\acp_new_%RANDOM%.tmp"
call :now
call :say Reinstalling scheduled tasks on request ...
call :register_tasks
rem Blank line so this block stays separated from the previous run in the log.
echo.>>"%TMPLOG%"
call :publish_block
if defined REG_FAILED call :open_log
echo.
echo Log written to the top of: "%LOGFILE%"
echo.
call :pause_if_double_clicked
if defined REG_FAILED exit /b 1
exit /b 0

:register_tasks
call :now
call :say Scheduled tasks missing or out of date - registering ...
rem Register through PowerShell: no elevation needed and it supports
rem StartWhenAvailable (catch up a run missed while the PC was off). [char]34
rem is a double quote, which avoids nested quotes. The action goes through
rem run-hidden.vbs: wscript is a GUI-subsystem host, so cmd starts hidden.
set "PS_SCRIPT_DIR=%SCRIPT_DIR%"
set "PSREG=$d=$env:PS_SCRIPT_DIR;$x=[char]34;"
set "PSREG=%PSREG%$arg=$x+$d+'\run-hidden.vbs'+$x+' '+$x+$d+'\checkin.bat --auto'+$x;"
set "PSREG=%PSREG%$a=New-ScheduledTaskAction -Execute 'wscript.exe' -Argument $arg;"
set "PSREG=%PSREG%$s=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10);"
set "PSREG=%PSREG%$p=New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited;"
set "PSREG=%PSREG%$t=@();foreach($h in '08:45','11:45','14:45','17:45','20:45','23:45'){$t+=New-ScheduledTaskTrigger -Daily -At $h};"
set "PSREG=%PSREG%Register-ScheduledTask -TaskName '%TASK_DAILY%' -Action $a -Settings $s -Principal $p -Trigger $t -Force -ErrorAction Stop|Out-Null;"
set "PSREG=%PSREG%Register-ScheduledTask -TaskName '%TASK_STARTUP%' -Action $a -Settings $s -Principal $p -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) -Force -ErrorAction Stop|Out-Null;"
rem The resume task needs an EVENT trigger, which New-ScheduledTaskTrigger
rem cannot express, so it comes from the XML template. $d = project folder.
set "PS_RESUME_XML=%SCRIPT_DIR%\scheduled-task.resume.xml"
set "PSREG=%PSREG%$rx=[IO.File]::ReadAllText($env:PS_RESUME_XML);"
set "PSREG=%PSREG%$rx=$rx.Replace('__CHECKIN_VBS__',$d+'\run-hidden.vbs').Replace('__CHECKIN_BAT__',$d+'\checkin.bat --auto').Replace('__USER__',$env:USERNAME);"
set "PSREG=%PSREG%Register-ScheduledTask -TaskName '%TASK_RESUME%' -Xml $rx -Force -ErrorAction Stop|Out-Null"
powershell -NoProfile -ExecutionPolicy Bypass -Command "%PSREG%" >nul 2>&1
rem Judge by the command's own exit code: :tasks_ok only checks that a task
rem exists and points here, so a FAILED re-register would look like success.
set "PSREG_RC="
if errorlevel 1 set "PSREG_RC=1"
call :now
call :tasks_ok
if errorlevel 1 set "PSREG_RC=1"
if defined PSREG_RC goto reg_failed
call :say Scheduled tasks registered OK: %TASK_DAILY% six times a day, %TASK_STARTUP% at logon, %TASK_RESUME% on wake.
exit /b 0
:reg_failed
set "REG_FAILED=1"
call :say WARN: task registration failed - opening the log in notepad.
call :say WARN: run this once from an account that may manage tasks; see README section 3.
exit /b 0

:tasks_ok
rem errorlevel 1 = a task is missing, or points at a different folder or file
schtasks /query /tn "%TASK_DAILY%" >nul 2>&1
if errorlevel 1 exit /b 1
schtasks /query /tn "%TASK_STARTUP%" >nul 2>&1
if errorlevel 1 exit /b 1
call :task_points_here "%TASK_DAILY%"
if errorlevel 1 exit /b 1
call :task_points_here "%TASK_STARTUP%"
if errorlevel 1 exit /b 1
schtasks /query /tn "%TASK_RESUME%" >nul 2>&1
if errorlevel 1 exit /b 1
call :task_points_here "%TASK_RESUME%"
if errorlevel 1 exit /b 1
exit /b 0

:task_points_here
schtasks /query /tn "%~1" /fo LIST /v 2>nul | findstr /i /l /c:"%SCRIPT_DIR%\checkin.bat" >nul
exit /b %ERRORLEVEL%

:uninstall
echo.
echo Removing AICreditPunch scheduled tasks ...
echo.
set "RC=0"
for %%T in (%TASK_DAILY% %TASK_STARTUP% %TASK_RESUME%) do call :delete_task "%%T"
echo.
if "%RC%"=="0" (
    echo Done. The log file was kept: "%LOGFILE%"
    echo Run this file without arguments to install the tasks again.
) else (
    echo Some tasks could not be removed - run this from an account that may manage tasks.
)
echo.
call :pause_if_double_clicked
exit /b %RC%

:delete_task
schtasks /query /tn "%~1" >nul 2>&1
if errorlevel 1 (
    echo   [skip] %~1 - not installed
    exit /b 0
)
schtasks /delete /f /tn "%~1" >nul 2>&1
if errorlevel 1 (
    echo   [FAIL] %~1 - delete failed
    set "RC=1"
) else (
    echo   [ OK ] %~1 - removed
)
exit /b 0

:usage
echo.
echo AICreditPunch - daily check-in for WorkBuddy + Trae
echo.
echo   checkin.bat                 run the check-in and auto-install the tasks
echo   checkin.bat --install       reinstall the scheduled tasks
echo   checkin.bat --uninstall     remove all three scheduled tasks
echo   checkin.bat --logs          open the local log in notepad
echo   checkin.bat --tasks         check the scheduled tasks, read-only
echo   checkin.bat --today         daily view: today status + last run
echo   checkin.bat --test-notify   send one test notification
echo   checkin.bat --help          show this help
echo.
echo Any other --option goes straight to checkin.py, for example:
echo   checkin.bat --status-only   query the platforms without claiming
echo   checkin.bat --init-trae     open the browser to log in to Trae
echo   checkin.bat --version       print the version
echo.
echo Scheduled tasks used by this script:
echo   %TASK_DAILY%    six times a day: %CHECKIN_TIMES%
echo   %TASK_STARTUP%  once at user logon
echo   %TASK_RESUME%   on resume from sleep or hibernate
echo.
echo Log file : %LOGFILE%  - newest run on top
echo Folder   : %SCRIPT_DIR%
echo Python   : %PYEXE%
echo.
call :pause_if_double_clicked
if defined RC exit /b %RC%
exit /b 0

:pause_if_double_clicked
rem Pause only when Explorer started this file: its command line ends with a
rem trailing space (cmd.exe /c ""X:\path\checkin.bat" "), while a scheduled
rem task's does not. Matching on the path alone hits both, which used to leave
rem triggered runs waiting on a keypress forever.
set "DBLCLK="
set "CMDL=%CMDCMDLINE:"=%"
if "%CMDL:~-1%"==" " set "DBLCLK=1"
if defined DBLCLK pause
set "CMDL="
set "DBLCLK="
exit /b 0
