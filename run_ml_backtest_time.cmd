@echo off
setlocal

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if exist "%PYTHON_EXE%" goto run
set "PYTHON_EXE=python"

:run
"%PYTHON_EXE%" -m ml.backtest --split-mode time --validation-days 7 %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
