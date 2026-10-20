@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
if /i "%~1"=="--check" goto check
call "%~dp0一键启动.bat" --test-captcha
exit /b %ERRORLEVEL%

:check
call "%~dp0一键启动.bat" --check
exit /b %ERRORLEVEL%
