@echo off
setlocal
cd /d "%~dp0"
call "%~dp0start_paparazzi.bat"
exit /b %errorlevel%
