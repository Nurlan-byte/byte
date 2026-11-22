@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.." || exit /b 1
if not exist ".venv\Scripts\python.exe" (
    echo Сначала установите зависимости BakePlan по инструкции в README.
    pause
    exit /b 1
)
echo Сверка товаров SmartShell за вчера. Вставьте токен в скрытое поле и нажмите Enter.
".venv\Scripts\python.exe" -m scripts.pos_branches smartshell --sales-on
set "check_status=%errorlevel%"
pause
exit /b %check_status%
