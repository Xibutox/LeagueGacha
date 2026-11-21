@echo off
rem Installe le Raspberry Pi depuis ce PC (voir README, Heberger sur un Raspberry Pi).
rem Arguments facultatifs : -Address 192.168.1.37 -RepoUrl https://github.com/... -Domain ... -Email ...
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer-depuis-windows.ps1" %*
pause
