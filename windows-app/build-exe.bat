@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
)
call ".venv\Scripts\activate.bat"
python -m pip install -q -r requirements.txt pyinstaller
pyinstaller --noconfirm --windowed --onefile --name "УВЫ control" --icon uvy-logo.ico --add-data "uvy-logo.png;." --add-data "uvy-logo.ico;." asic_control.py
echo.
echo Готово: dist\УВЫ control.exe
echo Можно скопировать ярлык на рабочий стол.
pause
