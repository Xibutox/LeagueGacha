@echo off
rem Installe le Raspberry Pi depuis ce PC (voir README, Heberger sur un Raspberry Pi).
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer-depuis-windows.ps1"
pause
