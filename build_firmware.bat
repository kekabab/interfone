@echo off
echo ==============================================
echo [1] Injetando variaveis do ESP-IDF v4.2...
echo ==============================================
call C:\esp\esp-idf\export.bat

echo ==============================================
echo [2] Preparando biblioteca de audio ESP-ADF v2.2...
echo ==============================================
if not exist "C:\esp\esp-adf" (
    git clone --depth 1 -b v2.2 --recursive https://github.com/espressif/esp-adf.git C:\esp\esp-adf
) else (
    echo ESP-ADF ja existe!
)
set ADF_PATH=C:\esp\esp-adf

echo ==============================================
echo [3] Compilando o Firmware C do Interfone IA...
echo ==============================================
cd C:\Users\kekab\.gemini\antigravity\scratch\esp32-ai-intercom
idf.py build
