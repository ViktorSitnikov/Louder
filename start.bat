@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 -c "import pycaw,webview,pystray,PIL" 2>nul
if errorlevel 1 (
  py -3 -m pip install -r "%~dp0requirements.txt"
  if errorlevel 1 (
    echo Не удалось установить зависимости.
    pause
    exit /b 1
  )
)
start "" pyw -3 "%~dp0backend\volume.py"
