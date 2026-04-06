@echo off
setlocal

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if exist "%PYTHON_EXE%" goto run
set "PYTHON_EXE=python"

:run
"%PYTHON_EXE%" -m ml.batch_pipeline --smoke-test %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
