@echo off
setlocal enabledelayedexpansion
title Omega Core Quantum - Instalador
chcp 65001 >/dev/null
cd /d "%~dp0"

echo ==================================================
echo    OMEGA CORE QUANTUM  -  Instalador para Windows
echo ==================================================
echo.

REM ---- 1. Detectar Python ----------------------------------------------
set "PY="
where python >/dev/null 2>&1 && set "PY=python"
if not defined PY ( where py >/dev/null 2>&1 && set "PY=py -3" )

if not defined PY (
  echo [!] Python no esta instalado. Intentando instalarlo con winget...
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  if errorlevel 1 (
    echo.
    echo [X] No se pudo instalar Python automaticamente.
    echo     Descargalo manualmente desde: https://www.python.org/downloads/
    echo     IMPORTANTE: marca la casilla "Add Python to PATH" durante la instalacion.
    echo     Despues vuelve a ejecutar este instalador.
    echo.
    pause
    exit /b 1
  )
  set "PY=python"
)

echo [*] Python detectado:
%PY% --version
echo.

REM ---- 2. Crear entorno virtual ----------------------------------------
cd /d "%~dp0server"
if not exist ".venv\Scripts\python.exe" (
  echo [*] Creando entorno virtual aislado...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [X] No se pudo crear el entorno virtual.
    pause
    exit /b 1
  )
)

set "VENV_PY=.venv\Scripts\python.exe"

REM ---- 3. Instalar dependencias ---------------------------------------
echo [*] Actualizando pip e instalando dependencias (numpy, fastapi, uvicorn, ...)...
"%VENV_PY%" -m pip install --upgrade pip >/dev/null 2>&1
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

REM ---- 4. Arrancar el servidor cuantico --------------------------------
echo [*] Iniciando el servidor cuantico en http://127.0.0.1:3333 ...
start "Omega Core Quantum - Servidor" cmd /k "cd /d "%~dp0server" && set QC_USER=admin&& set QC_PASSWORD=admin1234&& set QC_JWT_SECRET=omega-core-quantum-local-secret&& .venv\Scripts\python.exe server.py"

REM ---- 5. Abrir el cliente en el navegador -----------------------------
echo [*] Abriendo la interfaz en el navegador...
timeout /t 5 /nobreak >/dev/null
start "" "%~dp0client\index.html"

echo.
echo  - Si el navegador no se abre solo, abre el archivo: client\index.html
echo  - Para volver a iniciar mas tarde, ejecuta: start_windows.bat
echo  - Para detener el servidor, cierra la ventana "Omega Core Quantum - Servidor".
echo.
pause
