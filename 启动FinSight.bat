@echo off
rem FinSight 一键启动:双击本文件即可同时启动后端和前端
set "PATH=%LOCALAPPDATA%\Pandoc;%PATH%"
start "FinSight-Backend" cmd /k "cd /d %~dp0FinSight-main\demo\backend && %~dp0FinSight-main\.venv\Scripts\python.exe app.py"
timeout /t 3 >nul
start "FinSight-Frontend" cmd /k "cd /d %~dp0FinSight-main\demo\frontend && npm run dev"
echo Two windows opened. Wait ~30s, then visit http://localhost:3000
pause
