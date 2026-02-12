@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=%~dp0..\.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
	echo ERROR: Python virtual environment not found at "%PYTHON_EXE%"
	echo Create it first, then install requirements.
	pause
	exit /b 1
)

echo Starting WhatsApp Health Bot Services...

:: Start Flask App
echo Starting Flask App...
start "Flask App" cmd /k ""%PYTHON_EXE%" "%~dp0app.py""

:: Start Node.js WhatsApp Service
echo Starting WhatsApp Service...
cd wa-service
start "WhatsApp Service" cmd /k "npm start"

echo All services attempt to start. Check the new windows for logs.
pause
