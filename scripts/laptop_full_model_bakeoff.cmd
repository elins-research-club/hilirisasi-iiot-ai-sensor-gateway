@echo off
setlocal
cd /d "%~dp0\.."
py -3.13 scripts\laptop_bakeoff_runner.py %*
if errorlevel 1 exit /b %errorlevel%
endlocal
