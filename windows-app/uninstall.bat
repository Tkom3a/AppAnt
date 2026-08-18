@echo off
chcp 65001 >nul
title Удаление УВЫ control
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"
pause
