#!/bin/bash
# ─── Script de despliegue automático en DigitalOcean ───────────────────────
# Ejecutar DESDE TU MÁQUINA LOCAL:
#   chmod +x deploy_digitalocean.sh
#   ./deploy_digitalocean.sh TU_IP_DROPLET
#
# Requisito: tener acceso SSH al droplet (clave SSH configurada)

set -e

SERVER_IP="$1"
if [ -z "$SERVER_IP" ]; then
    echo "Uso: ./deploy_digitalocean.sh <IP_DEL_DROPLET>"
    echo "Ejemplo: ./deploy_digitalocean.sh 134.209.10.55"
    exit 1
fi

REMOTE_DIR="/opt/omega-core-quantum"
SSH_USER="root"

echo "========================================"
echo "  Desplegando Omega Core Quantum"
echo "  Servidor: $SERVER_IP"
echo "========================================"

echo ""
echo "[1/5] Subiendo archivos al servidor..."
ssh "$SSH_USER@$SERVER_IP" "mkdir -p $REMOTE_DIR"
scp quantum_engine.py "$SSH_USER@$SERVER_IP:$REMOTE_DIR/"
scp server.py         "$SSH_USER@$SERVER_IP:$REMOTE_DIR/"
scp requirements.txt  "$SSH_USER@$SERVER_IP:$REMOTE_DIR/"
scp quantum.service   "$SSH_USER@$SERVER_IP:/etc/systemd/system/"

echo ""
echo "[2/5] Instalando Python y dependencias..."
ssh "$SSH_USER@$SERVER_IP" "
    apt-get update -qq
    apt-get install -y python3 python3-pip -qq
    pip3 install -r $REMOTE_DIR/requirements.txt -q
"

echo ""
echo "[3/5] Abriendo puerto 3333 en el firewall..."
ssh "$SSH_USER@$SERVER_IP" "
    ufw allow 3333/tcp || true
    ufw allow OpenSSH   || true
"

echo ""
echo "[4/5] Configurando servicio systemd..."
ssh "$SSH_USER@$SERVER_IP" "
    systemctl daemon-reload
    systemctl enable quantum
    systemctl restart quantum
    sleep 2
    systemctl status quantum --no-pager
"

echo ""
echo "[5/5] ¡Despliegue completado!"
echo ""
echo "========================================"
echo "  Backend disponible en:"
echo "  http://$SERVER_IP:3333"
echo "  ws://$SERVER_IP:3333/ws"
echo "  Docs: http://$SERVER_IP:3333/docs"
echo "========================================"
echo ""
echo "Ahora edita client/index.html:"
echo "  window.QUANTUM_SERVER_HOST = '$SERVER_IP';"
