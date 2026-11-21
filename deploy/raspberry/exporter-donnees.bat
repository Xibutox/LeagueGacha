@echo off
rem Copie la base et les images du PC sur le Raspberry Pi.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0exporter-donnees.ps1"
pause
