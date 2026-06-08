# Omega Core Quantum — Despliegue en Cloud (DigitalOcean)

Arquitectura cliente-servidor real: el backend cuántico corre en un Droplet de DigitalOcean y el frontend se conecta desde cualquier navegador.

```
[Tu PC / navegador]          [DigitalOcean Droplet]
  client/index.html   ──────►  server/server.py :3333
  client/app.js      ◄──────   FastAPI + WebSocket
  client/style.css             quantum_engine.py
```

---

## 1. Crear el Droplet en DigitalOcean

1. Entra en [cloud.digitalocean.com](https://cloud.digitalocean.com) → **Create → Droplets**
2. Configuración recomendada:
   - **Región**: Elige la más cercana a ti (Frankfurt, Amsterdam…)
   - **OS**: Ubuntu 22.04 LTS
   - **Plan**: Basic — **Regular** → $6/mes (1 vCPU, 1 GB RAM) para probar  
     → $12/mes (1 vCPU, 2 GB RAM) para uso serio (más qubits simulados)
   - **Autenticación**: SSH Key (sube tu clave pública)
3. Anota la **IP pública** del Droplet (ejemplo: `134.209.10.55`)

> **Nota sobre qubits**: Con 1 GB RAM puedes simular ~23 qubits. Con 2 GB ~24 qubits. El servidor los detecta automáticamente.

---

## 2. Opción A — Despliegue automático (un solo comando)

Desde tu máquina local, en la carpeta `server/`:

```bash
chmod +x deploy_digitalocean.sh
./deploy_digitalocean.sh 134.209.10.55
```

El script hace todo automáticamente:
- Sube los archivos por SCP
- Instala Python + dependencias
- Abre el puerto 3333 en el firewall
- Configura un servicio systemd que arranca solo con el servidor

---

## 3. Opción B — Despliegue manual paso a paso

### 3.1 Conectar al Droplet

```bash
ssh root@134.209.10.55
```

### 3.2 Instalar dependencias

```bash
apt-get update
apt-get install -y python3 python3-pip
```

### 3.3 Subir los archivos del servidor

Desde tu máquina local (otra terminal):

```bash
scp server/quantum_engine.py root@134.209.10.55:/opt/omega-core-quantum/
scp server/server.py         root@134.209.10.55:/opt/omega-core-quantum/
scp server/requirements.txt  root@134.209.10.55:/opt/omega-core-quantum/
```

### 3.4 Instalar dependencias Python

```bash
# En el servidor
mkdir -p /opt/omega-core-quantum
cd /opt/omega-core-quantum
pip3 install -r requirements.txt
```

### 3.5 Abrir el puerto 3333

```bash
ufw allow 3333/tcp
ufw allow OpenSSH
ufw enable
```

### 3.6 Iniciar el servidor

**Prueba rápida** (foreground):
```bash
cd /opt/omega-core-quantum
python3 -m uvicorn server:app --host 0.0.0.0 --port 3333
```

**Producción** (servicio que sobrevive reinicios):
```bash
# Copiar servicio systemd
scp server/quantum.service root@134.209.10.55:/etc/systemd/system/

# En el servidor:
systemctl daemon-reload
systemctl enable quantum
systemctl start quantum
systemctl status quantum
```

### 3.7 Verificar que funciona

```bash
curl http://134.209.10.55:3333/api/info
```

Debes ver un JSON con la info del servidor cuántico.

---

## 4. Configurar el cliente

Edita **`client/index.html`** — busca estas líneas al final del archivo:

```javascript
window.QUANTUM_SERVER_HOST = '';   // ← PON AQUÍ LA IP DEL SERVIDOR
window.QUANTUM_SERVER_PORT = '3333';
window.QUANTUM_USE_HTTPS   = false;
```

Cámbialo a:

```javascript
window.QUANTUM_SERVER_HOST = '134.209.10.55';  // tu IP real
window.QUANTUM_SERVER_PORT = '3333';
window.QUANTUM_USE_HTTPS   = false;
```

Luego abre `client/index.html` directamente en tu navegador. **No necesita servidor web** — es un archivo HTML estático.

---

## 5. Comandos útiles de gestión

```bash
# Ver logs en tiempo real
journalctl -u quantum -f

# Reiniciar el servidor
systemctl restart quantum

# Parar el servidor
systemctl stop quantum

# Ver estado
systemctl status quantum

# Actualizar el código (desde tu máquina local)
scp server/quantum_engine.py root@IP:/opt/omega-core-quantum/
scp server/server.py         root@IP:/opt/omega-core-quantum/
ssh root@IP "systemctl restart quantum"
```

---

## 6. Opcional — Dominio + HTTPS

Si quieres usar un dominio propio (`quantum.misite.com`) con HTTPS:

### 6.1 Instalar Nginx + Certbot

```bash
apt-get install -y nginx certbot python3-certbot-nginx
```

### 6.2 Configurar Nginx como proxy

Crea `/etc/nginx/sites-available/quantum`:

```nginx
server {
    listen 80;
    server_name quantum.misite.com;

    location / {
        proxy_pass http://127.0.0.1:3333;
        proxy_http_version 1.1;

        # WebSocket support (crítico)
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }
}
```

```bash
ln -s /etc/nginx/sites-available/quantum /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

### 6.3 Obtener certificado SSL gratis

```bash
certbot --nginx -d quantum.misite.com
```

### 6.4 Actualizar el cliente

```javascript
window.QUANTUM_SERVER_HOST = 'quantum.misite.com';
window.QUANTUM_SERVER_PORT = '443';   // puerto HTTPS estándar
window.QUANTUM_USE_HTTPS   = true;    // activa wss:// automáticamente
```

---

## 7. Resumen de puertos

| Puerto | Protocolo | Uso |
|--------|-----------|-----|
| 22     | TCP       | SSH (gestión del servidor) |
| 3333   | TCP       | API REST + WebSocket (sin Nginx) |
| 80     | TCP       | HTTP (con Nginx) |
| 443    | TCP       | HTTPS/WSS (con Nginx + SSL) |

---

## 8. Arquitectura final

```
Internet
    │
    ▼
[DigitalOcean Droplet — Ubuntu 22.04]
    │
    ├── systemd: quantum.service
    │     └── uvicorn server:app --host 0.0.0.0 --port 3333
    │           ├── FastAPI REST API  →  /api/*
    │           └── WebSocket        →  /ws
    │
    └── (opcional) nginx → proxy inverso + SSL
          └── quantum.misite.com:443  →  127.0.0.1:3333

[Cliente — cualquier PC]
    └── client/index.html (abierto en navegador)
          ├── HTTP  →  API_BASE  = http://IP:3333
          └── WS    →  WS_URL   = ws://IP:3333/ws
```

---

*Omega Core Quantum — Backend cuántico en la nube*

---

## 9. Autenticación — Usuario y Contraseña

El servidor usa **JWT (JSON Web Tokens)**. Todas las rutas REST y el WebSocket están protegidas.

### Cambiar usuario y contraseña

**Opción A — Variables de entorno** (recomendado):

```bash
# En el servidor, antes de iniciar:
export QC_USER="mi_usuario"
export QC_PASSWORD="MiContraseña_Segura_2024!"
export QC_JWT_SECRET="clave_aleatoria_muy_larga_aqui"
systemctl restart quantum
```

**Opción B — Editar server.py directamente** (líneas 34-36):

```python
AUTH_USERNAME  = "mi_usuario"
AUTH_PASSWORD  = "MiContraseña_Segura_2024!"
JWT_SECRET     = "clave_aleatoria_muy_larga_min_32_chars"
```

### Valores por defecto

| Campo      | Valor por defecto |
|------------|-------------------|
| Usuario    | `admin`           |
| Contraseña | `quantum2024!`    |
| Expiración | 12 horas          |

> **Cambia la contraseña antes de exponer el servidor a internet.**

### Cómo funciona

1. El cliente abre el **overlay de login** al arrancar
2. Introduce usuario + contraseña → llama a `POST /auth/login`
3. El servidor devuelve un **JWT token** (válido 12 horas)
4. El token se guarda en memoria (no en localStorage)
5. Cada petición REST lleva `Authorization: Bearer <token>`
6. El WebSocket conecta a `ws://IP:3333/ws?token=<JWT>`
7. Si el token expira → el servidor manda `auth_error` → vuelve el login automáticamente

### Generar JWT_SECRET seguro

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### Endpoint de login (para uso desde scripts)

```bash
curl -X POST http://IP:3333/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"quantum2024!"}'

# Respuesta:
# {"access_token":"eyJ...","token_type":"bearer","expires_in_hours":12,"username":"admin"}

# Usar el token en llamadas posteriores:
TOKEN="eyJ..."
curl http://IP:3333/api/info -H "Authorization: Bearer $TOKEN"
```
