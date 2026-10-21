@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title 智慧树学习助手
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"
set "CHECK_ONLY="
set "MENU_MODE="
set "RESULT=1"
if /i "%~1"=="--check" set "CHECK_ONLY=1"
if "%~1"=="" set "MENU_MODE=1"
for %%A in (--setup-feishu --test-feishu --setup-chapter-ai) do if /i "%~1"=="%%A" set "MENU_MODE=1"
if /i "%~1"=="--chapter-tests" if /i "%~2"=="menu" set "MENU_MODE=1"
pushd "%~dp0"
if errorlevel 1 goto directory_error

for %%F in (main.py requirements.txt notification_settings.py account_login.py launcher_menu.py terminal_ui.py study_session.py chapter_tests.py chapter_ai_settings.py) do (
    if not exist "%%F" (
        echo [错误] 缺少 %%F，请保持项目文件完整。
        goto finish
    )
)

rem Prefer an existing project environment; --check never installs anything.
set "PYEXE=%CD%\venv\Scripts\python.exe"
set "PYARGS="
call :probe_python
if not errorlevel 1 goto dependencies
set "PYEXE=%CD%\.venv\Scripts\python.exe"
call :probe_python
if not errorlevel 1 goto dependencies
if defined CHECK_ONLY (
    echo [错误] 没有可用的项目环境。请双击本文件完成环境初始化。
    goto finish
)

set "PYEXE=py"
set "PYARGS=-3"
call :probe_python
if not errorlevel 1 goto create_environment
set "PYEXE=python"
set "PYARGS="
call :probe_python
if not errorlevel 1 goto create_environment
echo [错误] 未找到 Python 3.10 或更新版本。
echo 请从 https://www.python.org/downloads/windows/ 安装 Python，并勾选 Add python.exe to PATH。
goto finish

:create_environment
echo [提示] 正在创建 .venv 环境……
"%PYEXE%" %PYARGS% -m venv ".venv"
if errorlevel 1 (
    echo [错误] 无法创建虚拟环境，请检查 Python 安装和目录权限。
    goto finish
)
set "PYEXE=%CD%\.venv\Scripts\python.exe"
set "PYARGS="

:dependencies
echo [提示] Python: "%PYEXE%"
"%PYEXE%" -c "import requests, tiktoken; from PIL import Image, ImageOps; from Crypto.Cipher import AES; from openai import OpenAI; from requests.adapters import HTTPAdapter, Retry; from playwright.sync_api import sync_playwright" >nul 2>&1
if not errorlevel 1 goto source_check
if defined CHECK_ONLY (
    echo [错误] 依赖缺失或损坏。请双击本文件安装依赖。
    goto finish
)
echo [提示] 正在安装依赖，需要联网……
"%PYEXE%" -m pip --version >nul 2>&1
if not errorlevel 1 goto install_dependencies
"%PYEXE%" -m ensurepip --upgrade
if errorlevel 1 goto install_error
:install_dependencies
"%PYEXE%" -m pip install --disable-pip-version-check -r "requirements.txt"
if errorlevel 1 goto install_error
"%PYEXE%" -c "import requests, tiktoken; from PIL import Image, ImageOps; from Crypto.Cipher import AES; from openai import OpenAI; from requests.adapters import HTTPAdapter, Retry; from playwright.sync_api import sync_playwright"
if errorlevel 1 goto install_error

:source_check
rem Compile in memory without loading accounts or contacting the service.
"%PYEXE%" -c "from pathlib import Path; [compile(p.read_text(encoding='utf-8-sig'), str(p), 'exec') for p in Path('.').glob('*.py')]; print('[OK] Dependencies and Python syntax checks passed.')"
if errorlevel 1 goto finish
if defined CHECK_ONLY (
    set "RESULT=0"
    goto finish
)
if /i "%~1"=="--setup-feishu" goto feishu_setup
if /i "%~1"=="--test-feishu" goto feishu_test
if /i "%~1"=="--setup-chapter-ai" goto chapter_ai_setup
if /i "%~1"=="--chapter-tests" if /i "%~2"=="menu" goto study_login
if defined MENU_MODE goto menu
"%PYEXE%" -u "main.py" %*
set "RESULT=%ERRORLEVEL%"
goto after_action

:menu
"%PYEXE%" -u "launcher_menu.py"
set "MENU_CHOICE=%ERRORLEVEL%"
if "%MENU_CHOICE%"=="0" goto password_login
if "%MENU_CHOICE%"=="1" goto qr_login
if "%MENU_CHOICE%"=="2" goto chapter_ai_setup
if "%MENU_CHOICE%"=="3" goto feishu_setup
if "%MENU_CHOICE%"=="4" goto feishu_test
if "%MENU_CHOICE%"=="5" goto menu_exit
goto menu_error

:password_login
"%PYEXE%" -u "main.py" --password-login --interactive-menu
set "RESULT=%ERRORLEVEL%"
goto after_action

:qr_login
"%PYEXE%" -u "main.py" -q --interactive-menu
set "RESULT=%ERRORLEVEL%"
goto after_action

:feishu_setup
"%PYEXE%" -u "notification_settings.py" --setup-feishu
set "RESULT=%ERRORLEVEL%"
goto after_action

:feishu_test
"%PYEXE%" -u "notification_settings.py" --test-feishu
set "RESULT=%ERRORLEVEL%"
goto after_action

:chapter_ai_setup
"%PYEXE%" -u "chapter_ai_settings.py"
set "RESULT=%ERRORLEVEL%"
goto after_action

:study_login
"%PYEXE%" -u "main.py" --interactive-menu
set "RESULT=%ERRORLEVEL%"
goto after_action

:after_action
if "%RESULT%"=="20" if defined MENU_MODE goto menu
if "%RESULT%"=="20" set "RESULT=0"
echo.
echo [提示] 本次操作已结束，退出代码：%RESULT%
if not defined MENU_MODE goto finish
"%PYEXE%" -u "launcher_menu.py" --result "%RESULT%"
if errorlevel 1 goto menu_exit
goto menu

:menu_exit
set "RESULT=0"
goto finish

:menu_error
echo [错误] 无法读取菜单选择，请在终端中直接运行本文件。
set "RESULT=1"
goto finish

:install_error
echo [错误] 依赖安装失败，请查看上方错误后重试。
goto finish

:probe_python
"%PYEXE%" %PYARGS% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
exit /b %ERRORLEVEL%

:finish
popd
if defined CHECK_ONLY exit /b %RESULT%
if defined MENU_MODE if "%RESULT%"=="0" exit /b 0
pause
exit /b %RESULT%

:directory_error
echo [错误] 无法进入项目目录。
if not defined CHECK_ONLY pause
exit /b 1
