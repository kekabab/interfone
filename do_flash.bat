@echo off
set ADF_PATH=C:\esp\esp-adf-v2
call D:\esp\.espressif\frameworks\esp-idf-v4.2.5\export.bat
cd C:\Users\kekab\.gemini\antigravity\scratch\esp32-ai-intercom
idf.py -p COM4 flash monitor
