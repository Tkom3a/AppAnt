@echo off
chcp 65001 >nul
title Установка УВЫ control
cd /d "%~dp0"
echo.
echo  Установщик УВЫ control
echo  Сейчас поставит зависимости и ярлык на рабочий стол.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 (
  echo.
  echo Установка не завершилась. Прочитайте сообщение выше.
  pause
  exit /b 1
)
echo.
pause
