@echo off
setlocal
set "PROJECT_ROOT=%~dp0"
set "IDF_PATH=D:\esp\.espressif\frameworks\esp-idf-v4.2.5"
set "ADF_PATH=C:\esp\esp-adf-v2"
set "PORT=%~1"
if "%PORT%"=="" set "PORT=COM4"

if not exist "%IDF_PATH%\export.bat" (
    echo [ERRO] ESP-IDF nao encontrado em %IDF_PATH%
    exit /b 1
)

call "%IDF_PATH%\export.bat"
cd /d "%PROJECT_ROOT%"
set "BUILD_DIR=C:\Users\kekab\.gemini\antigravity\scratch\interfone-build"
idf.py -B "%BUILD_DIR%" -p "%PORT%" flash monitor
