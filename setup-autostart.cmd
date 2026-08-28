@echo off
setlocal EnableExtensions
title LAN File Hub - 开机后台启动配置

net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo 请右键点击 setup-autostart.cmd，选择“以管理员身份运行”。
  pause
  endlocal
  exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-autostart.ps1"
set "LAN_FILE_HUB_EXIT_CODE=%ERRORLEVEL%"
if not "%LAN_FILE_HUB_EXIT_CODE%"=="0" (
  echo.
  echo 配置失败。请根据上方错误信息修正后重试。
  pause
)

endlocal & exit /b %LAN_FILE_HUB_EXIT_CODE%
