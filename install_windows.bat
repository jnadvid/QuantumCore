@echo off
title Omega Core Quantum - Instalador
cd /d "%~dp0"

echo ==================================================
echo    OMEGA CORE QUANTUM  -  Instalador para Windows
echo ==================================================
echo.

REM ---- 1. Detectar Python (probando los comandos reales) ----------------
set "PY="
python --version >nul 2>&1 && set "PY=python"
if not defined PY ( py -3 --version >nul 2>&1 && set "PY=py -3" )
if not defined PY ( python3 --version >nul 2>&1 && set "PY=python3" )

if not defined PY (
  echo [X] No se ha detectado Python en el PATH.
  echo.
  echo     Si ya tienes Python instalado, abre una NUEVA ventana de cmd
  echo     o reinicia el equipo para que se actualice el PATH.
  echo.
  echo     Si no lo tienes, descargalo desde https://www.python.org/downloads/
  echo     y marca la casilla "Add Python to PATH" al instalar.
  echo.
  pause
  exit /b 1
)

echo [*] Python detectado:
%PY% --version
echo.

REM ---- 2. Crear entorno virtual ----------------------------------------
cd /d "%~dp0"
cd server
if not exist ".venv\Scripts\python.exe" (
  echo [*] Creando entorno virtual aislado...
  %PY% -m venv .venv
)

if not exist ".venv\Scripts\python.exe" (
  echo [X] No se pudo crear el entorno virtual con %PY%.
  echo     Comprueba que tu instalacion de Python incluye el modulo venv.
  pause
  exit /b 1
)

set "VENV_PY=%~dp0server\.venv\Scripts\python.exe"

REM ---- 3. Instalar dependencias ---------------------------------------
echo [*] Actualizando pip e instalando dependencias...
"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [X] Fallo al instalar las dependencias. Revisa tu conexion a internet.
  pause
  exit /b 1
)

echo.
echo ==================================================
echo   Instalacion COMPLETADA correctamente.
echo.
echo   Acceso a la aplicacion:
echo     Usuario     : admin
echo     Contrasena  : admin1234
echo ==================================================
echo.

REM ---- 4. Configurar credenciales (heredadas por el servidor) ----------
set "QC_USER=admin"
set "QC_PASSWORD=admin1234"
set "QC_JWT_SECRET=omega-core-quantum-local-secret"

REM ---- 5. Abrir la interfaz y arrancar el servidor ---------------------
echo [*] Abriendo la interfaz en el navegador...
start "" "%~dp0client\index.html"

echo.
echo [*] Servidor iniciando en http://127.0.0.1:3333
echo     Manten esta ventana abierta. Pulsa Ctrl+C para detener el servidor.
echo     Para volver a iniciar en el futuro usa: start_windows.bat
echo.
"%VENV_PY%" server.py

pause
