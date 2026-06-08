@echo off
title Omega Core Quantum - Servidor
chcp 65001 >/dev/null
cd /d "%~dp0server"

if not exist ".venv\Scripts\python.exe" (
  echo [X] El entorno no esta instalado todavia.
  echo     Ejecuta primero: install_windows.bat
  echo.
  pause
  exit /b 1
)

set "QC_USER=admin"
set "QC_PASSWORD=admin1234"
set "QC_JWT_SECRET=omega-core-quantum-local-secret"

echo [*] Abriendo la interfaz en el navegador...
start "" "%~dp0client\index.html"

echo [*] Iniciando servidor en http://127.0.0.1:3333  (Ctrl+C para detener)
echo     Usuario: admin   Contrasena: admin1234
echo.
.venv\Scripts\python.exe server.py

pause
