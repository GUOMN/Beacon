@echo off
setlocal
set "INSTALL_DIR=%LOCALAPPDATA%\Programs\CodexStatusBridge"
set "START_MENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs"

if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
copy /Y "%~dp0CodexStatusBridge.exe" "%INSTALL_DIR%\CodexStatusBridge.exe" >nul

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$shell = New-Object -ComObject WScript.Shell; $shortcut = $shell.CreateShortcut('%START_MENU%\Codex 六灯蓝牙桥接.lnk'); $shortcut.TargetPath = '%INSTALL_DIR%\CodexStatusBridge.exe'; $shortcut.WorkingDirectory = '%INSTALL_DIR%'; $shortcut.Save()"

start "" "%INSTALL_DIR%\CodexStatusBridge.exe"
endlocal
exit /b 0
