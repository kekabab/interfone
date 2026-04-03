@echo off
echo ======================================================
echo    INTERFONE AI - INICIALIZANDO (TUNEL PINGGY)
echo ======================================================
echo.

echo [+] Iniciando o sistema do Interfone...
start "Servidor Interfone AI" cmd /k "python server.py"

echo [+] Abrindo conexao com a internet (Pinggy)...
echo [!] Procure a janela preta que vai abrir agora. 
echo [!] O link estara logo no topo dela.
start "Link do Interfone (Pinggy)" cmd /k "ssh -p 443 -R0:localhost:8765 a.pinggy.io -o StrictHostKeyChecking=no"

exit
