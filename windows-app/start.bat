@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python не найден. Установите Python 3.11+ с python.org и повторите.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Создаю виртуальное окружение...
  python -m venv .venv
)

call ".venv\Scripts\activate.bat"
python -m pip install -q -r requirements.txt
python asic_control.py
