@echo off
echo ======================================================
echo    INTERFONE AI - INICIALIZANDO ACESSO REMOTO
echo ======================================================

if exist cloudflared.exe (
    echo [+] Cloudflared ja encontrado.
) else (
    echo [+] Baixando ferramenta de tunel...
    powershell -Command "Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile 'cloudflared.exe'"
)

echo [+] Iniciando servidor Python...
start "Servidor Interfone AI" cmd /k "python server.py"

echo [+] Criando tunel seguro... COPIE O LINK 'https://...'
cloudflared.exe tunnel --url http://localhost:8765
pause
