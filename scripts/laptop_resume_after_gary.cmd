@echo off
setlocal
cd /d "%~dp0\.."
echo [DEPRECATED WRAPPER] Resume logic is owned by laptop_bakeoff_runner.py.
echo Continuing lanes: uci,fidas,sim
py -3.13 scripts\laptop_bakeoff_runner.py --device cuda --lanes uci,fidas,sim %*
if errorlevel 1 exit /b %errorlevel%
endlocal
