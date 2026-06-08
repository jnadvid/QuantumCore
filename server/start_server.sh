#!/bin/bash
# ─── Omega Core Quantum — Server Startup ───────────────────────────────────
# Uso: ./start_server.sh
# Ejecutar como root o con sudo en DigitalOcean Droplet

set -e

echo "========================================"
echo "  Omega Core Quantum — Backend Server"
echo "========================================"

# Ir al directorio del script
cd "$(dirname "$0")"

# Instalar dependencias si no existen
if ! python3 -c "import fastapi" 2>/dev/null; then
    echo "[*] Instalando dependencias..."
    pip3 install -r requirements.txt
fi

echo "[*] Iniciando servidor en 0.0.0.0:3333 ..."
echo "[*] Presiona Ctrl+C para detener"
echo ""

python3 -m uvicorn server:app \
    --host 0.0.0.0 \
    --port 3333 \
    --workers 1 \
    --log-level info
