#!/bin/bash
# ─── Omega Core Quantum — Diagnóstico rápido ────────────────────────────────
# Ejecutar en el servidor con: bash check_server.sh

echo ""
echo "════════════════════════════════════════"
echo "  Omega Core Quantum — Diagnóstico"
echo "════════════════════════════════════════"
echo ""

# 1. Servicio systemd
echo "[1] Estado del servicio:"
systemctl is-active quantum 2>/dev/null && echo "    ✅ quantum.service ACTIVO" || echo "    ❌ quantum.service NO está corriendo"
echo ""

# 2. Puerto 3333 escuchando
echo "[2] Puerto 3333:"
if ss -tlnp 2>/dev/null | grep -q ':3333'; then
    echo "    ✅ Puerto 3333 está escuchando"
    ss -tlnp | grep ':3333'
else
    echo "    ❌ Nada escucha en el puerto 3333"
    echo "    → Ejecuta: systemctl start quantum"
fi
echo ""

# 3. Firewall ufw
echo "[3] Firewall ufw:"
if command -v ufw >/dev/null 2>&1; then
    ufw_status=$(ufw status 2>/dev/null)
    echo "$ufw_status" | grep -E "Status:|3333" || echo "    (ufw inactivo o sin regla para 3333)"
    if echo "$ufw_status" | grep -q "3333"; then
        echo "    ✅ Puerto 3333 permitido en ufw"
    else
        echo "    ⚠️  Puerto 3333 NO aparece en ufw — ejecuta:"
        echo "    ufw allow 3333/tcp && ufw reload"
    fi
else
    echo "    (ufw no instalado — firewall externo o iptables)"
fi
echo ""

# 4. Test HTTP local
echo "[4] Test HTTP local (curl al backend):"
response=$(curl -s -o /tmp/qc_probe.txt -w "%{http_code}" http://127.0.0.1:3333/auth/login \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"admin1234"}' \
    --max-time 5 2>/dev/null)
if [ "$response" = "200" ]; then
    echo "    ✅ Login funciona (HTTP 200)"
    cat /tmp/qc_probe.txt | python3 -m json.tool 2>/dev/null | head -5
elif [ "$response" = "401" ]; then
    echo "    ✅ Servidor responde (HTTP 401 — contraseña incorrecta, normal si la cambiaste)"
elif [ -z "$response" ]; then
    echo "    ❌ No hay respuesta — el servidor no está corriendo o no escucha en 3333"
else
    echo "    ⚠️  Respuesta HTTP $response:"
    cat /tmp/qc_probe.txt | head -3
fi
echo ""

# 5. IP pública
echo "[5] IP pública del servidor:"
pub_ip=$(curl -s --max-time 3 ifconfig.me || curl -s --max-time 3 icanhazip.com)
echo "    → $pub_ip"
echo "    Usa esta IP en client/index.html:"
echo "    window.QUANTUM_SERVER_HOST = '$pub_ip';"
echo ""

# 6. Logs recientes
echo "[6] Últimos logs del servicio:"
journalctl -u quantum -n 15 --no-pager 2>/dev/null || echo "    (sin logs disponibles)"
echo ""

echo "════════════════════════════════════════"
echo "  Si todo está ✅ y aún no funciona:"
echo "  → Abre el puerto 3333 en el Cloud"
echo "    Firewall de DigitalOcean (panel web)"
echo "    Networking → Firewalls → Inbound Rules"
echo "════════════════════════════════════════"
echo ""
