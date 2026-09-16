@echo off
setlocal

rem ===================================================================
rem  AICreditPunch - Windows entry point
rem
rem    checkin.bat               run the check-in and auto-install the tasks
rem    checkin.bat --install     reinstall the scheduled tasks
rem    checkin.bat --uninstall   remove all three scheduled tasks
rem    checkin.bat --logs        open the local log in notepad
rem    checkin.bat --help        show usage
rem
rem  Three scheduled tasks are managed here (see README section 3.3):
rem    AICreditPunch-Daily     six daily triggers, StartWhenAvailable
rem    AICreditPunch-Startup   at user logon
rem    AICreditPunch-Resume    on resume from sleep or hibernate; it is
rem                            registered from scheduled-task.resume.xml
rem                            because New-ScheduledTaskTrigger cannot
rem                            express an event trigger
rem
rem  Every subcommand is --prefixed. Only help keeps a short spelling:
rem  --help, -h, /? and plain help all print the usage. The bare words
rem  install, uninstall and logs are NOT valid any more.
rem
rem  Any other --option is forwarded to checkin.py unchanged, e.g.
rem    checkin.bat --tasks         check the three scheduled tasks, read-only
rem    checkin.bat --today         daily view - today's status and last run
rem    checkin.bat --status-only   query the platforms without claiming
rem    checkin.bat --version       print the script version
rem
rem  Keep this file ASCII-only with CRLF line endings: non-ASCII or LF
rem  breaks parsing under cmd.exe with a GBK code page. Never put
rem  parentheses inside echo text that sits in an if-block, or cmd
rem  will close the block early and the script dies with a syntax error.
rem
rem  Every line this file writes into the log must be ASCII. Never echo
rem  %date% or %time%: under a Chinese code page they emit localized
rem  text such as the Chinese word for Monday and corrupt the UTF-8 log.
rem
rem  When task registration fails this file opens the log in notepad, so
rem  the user sees the WARN line without hunting for the file.
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
set "REG_FAILED="
set "LOG_OPENED="

rem ---------------------------------------------------------------- subcommands
rem Every subcommand is --prefixed, except the help ones: --help, -h, /? and
rem the bare word help all print the usage. The bare words install, uninstall
rem and logs are NOT valid any more.
set "ARG1=%~1"

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

set "TMPLOG=%TEMP%\acp_run_%RANDOM%.tmp"
set "NEWLOG=%TEMP%\acp_new_%RANDOM%.tmp"

rem Tell checkin.py that this .bat owns the log: on the first successful
rem check-in of the day it only leaves a flag file, and :open_log below
rem opens the log AFTER this run has been prepended to the top.
set "ACP_LOG_OWNER=bat"

rem ---------------------------------------------------------------- task autosetup
rem Register the tasks when they are missing OR still point at an old
rem folder or file name, e.g. after the project was renamed. A failed
rem registration is non-fatal: the check-in still runs and then the log
rem is opened so the user can see why it failed.
call :tasks_ok
if errorlevel 1 call :register_tasks

rem ---------------------------------------------------------------- wait for network
rem At boot/login WiFi is often not ready yet; the script itself only retries
rem about 6s, so probe TCP 443 here and give up after ~120s instead of
rem wasting this run.
set "N=0"
:waitnet
powershell -NoProfile -Command "try { $c = New-Object System.Net.Sockets.TcpClient; $c.Connect('www.workbuddy.cn', 443); $c.Close(); exit 0 } catch { exit 1 }" >nul 2>&1
if %ERRORLEVEL%==0 goto netok
set /a N+=1
if %N% LSS 24 (
    timeout /t 5 /nobreak >nul
    goto waitnet
)
call :now
call :say WARN: network not reachable after ~120s, running anyway.
:netok

call :now
if not exist "%PYEXE%" (
    call :say ERROR: python not found: %PYEXE%
    rem Blank line so this block stays separated from the previous run in the log.
    echo.>>"%TMPLOG%"
    call :publish_block
    if defined REG_FAILED call :open_log
    exit /b 127
)

"%PYEXE%" checkin.py>>"%TMPLOG%" 2>&1
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
exit /b %RC%

rem ================================================================ subroutines

:forward
rem A --option meant for checkin.py: run it in this console. Views such as
rem --tasks and --today are read-only, so this path never touches the log.
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
rem Open the log in notepad, at most once per run.
if defined LOG_OPENED exit /b 0
set "LOG_OPENED=1"
if not exist "%LOGFILE%" exit /b 0
rem Open through Start-Process rather than "start notepad": "start" hands the
rem new process this script's stdout/stderr handles, so a caller that pipes or
rem redirects the run would block until notepad is closed. Start-Process spawns
rem a detached process and leaves the caller's handles alone.
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
rem Write one ASCII-only line into this run's temp log.
rem The timestamp prefix is dropped when :now could not produce an ASCII
rem one, so a broken helper never injects localized text into the log.
if defined NOW echo [%NOW%] %* >>"%TMPLOG%"
if not defined NOW echo %* >>"%TMPLOG%"
exit /b 0

:now
rem Locale-independent timestamp for the lines this .bat writes itself.
rem "-Format s" is the sortable ISO form 2026-09-14T21:44:12: it needs no
rem inner quotes and no localized date names. Retry once, then give up
rem quietly (a localized %date% fallback would corrupt the UTF-8 log).
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
rem Register through PowerShell: it works without elevation and allows the
rem "start when available" setting, so a run missed while the PC was off is
rem caught up later. [char]34 is a double quote, which keeps this command
rem free of nested quotes. The task action goes through run-hidden.vbs
rem (wscript.exe) so the runs happen in a hidden window - wscript is a
rem GUI-subsystem host and the vbs starts cmd with window style 0.
set "PS_SCRIPT_DIR=%SCRIPT_DIR%"
set "PSREG=$d=$env:PS_SCRIPT_DIR;$x=[char]34;"
set "PSREG=%PSREG%$arg=$x+$d+'\run-hidden.vbs'+$x+' '+$x+$d+'\checkin.bat'+$x;"
set "PSREG=%PSREG%$a=New-ScheduledTaskAction -Execute 'wscript.exe' -Argument $arg;"
set "PSREG=%PSREG%$s=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10);"
set "PSREG=%PSREG%$p=New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited;"
set "PSREG=%PSREG%$t=@();foreach($h in '08:45','11:45','14:45','17:45','20:45','23:45'){$t+=New-ScheduledTaskTrigger -Daily -At $h};"
set "PSREG=%PSREG%Register-ScheduledTask -TaskName '%TASK_DAILY%' -Action $a -Settings $s -Principal $p -Trigger $t -Force -ErrorAction Stop|Out-Null;"
set "PSREG=%PSREG%Register-ScheduledTask -TaskName '%TASK_STARTUP%' -Action $a -Settings $s -Principal $p -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) -Force -ErrorAction Stop|Out-Null;"
rem The resume task needs an EVENT trigger, which New-ScheduledTaskTrigger cannot
rem express, so it comes from an XML template. [IO.File]::ReadAllText reads it
rem without BOM surprises; $d is the project folder reused from above.
set "PS_RESUME_XML=%SCRIPT_DIR%\scheduled-task.resume.xml"
set "PSREG=%PSREG%$rx=[IO.File]::ReadAllText($env:PS_RESUME_XML);"
set "PSREG=%PSREG%$rx=$rx.Replace('__CHECKIN_VBS__',$d+'\run-hidden.vbs').Replace('__CHECKIN_BAT__',$d+'\checkin.bat').Replace('__USER__',$env:USERNAME);"
set "PSREG=%PSREG%Register-ScheduledTask -TaskName '%TASK_RESUME%' -Xml $rx -Force -ErrorAction Stop|Out-Null"
powershell -NoProfile -ExecutionPolicy Bypass -Command "%PSREG%" >nul 2>&1
call :now
call :tasks_ok
if errorlevel 1 (
    set "REG_FAILED=1"
    call :say WARN: task registration failed - opening the log in notepad.
    call :say WARN: run this once from an account that may manage tasks; see README section 3.
) else (
    call :say Scheduled tasks registered OK: %TASK_DAILY% six times a day, %TASK_STARTUP% at logon, %TASK_RESUME% on wake.
)
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
rem Keep the window open only when the user double-clicked this file.
echo %CMDCMDLINE% | findstr /i /l /c:"%~f0" >nul && pause
exit /b 0
