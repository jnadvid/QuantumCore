"""
Omega Core Quantum Server
FastAPI + WebSocket server on port 3333
Provides real-time quantum state streaming and REST API for operations.
Authentication: JWT Bearer tokens (user/password login)
"""

import asyncio
import json
import logging
import math
import os
import secrets
import warnings
from datetime import datetime, timedelta, timezone
from typing import Set, Optional, List, Dict, Any

import numpy as np
import psutil

# Suppress WinError 10054 (Windows connection reset — harmless browser behavior)
logging.getLogger('asyncio').setLevel(logging.CRITICAL)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

# JWT — uses PyJWT (installed via requirements.txt)
import jwt as pyjwt

from quantum_engine import QuantumState

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("quantum-server")

# ─── AUTH CONFIG ──────────────────────────────────────────────────────────────
# Cambia usuario, contraseña y JWT_SECRET antes de desplegar.
# También puedes sobreescribirlos con variables de entorno:
#   export QC_USER=miusuario
#   export QC_PASSWORD=micontraseña
#   export QC_JWT_SECRET=clave_secreta_muy_larga

AUTH_USERNAME  = os.environ.get("QC_USER",       "admin")
AUTH_PASSWORD  = os.environ.get("QC_PASSWORD",   "admin1234")
JWT_SECRET     = os.environ.get("QC_JWT_SECRET",  secrets.token_hex(32))
JWT_ALGORITHM  = "HS256"
JWT_EXPIRE_HOURS = 12   # El token expira en 12 horas

logger.info(f"Auth user: '{AUTH_USERNAME}' | JWT expire: {JWT_EXPIRE_HOURS}h")

# ─── JWT helpers ──────────────────────────────────────────────────────────────

def create_token(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {"sub": username, "exp": expire}
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verify_token(token: str) -> Optional[str]:
    """Returns username if valid, None if invalid/expired."""
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("sub")
    except pyjwt.ExpiredSignatureError:
        return None
    except pyjwt.InvalidTokenError:
        return None

# ─── FastAPI Auth dependency ──────────────────────────────────────────────────

security = HTTPBearer()

def require_auth(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    username = verify_token(credentials.credentials)
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado. Inicia sesión de nuevo.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return username

# ─── App Setup ────────────────────────────────────────────────────────────────

app = FastAPI(title="Omega Core Quantum", version="1.0.0")

# CORS: aceptar cualquier origen incluyendo file:// (origin=null)
# El middleware estándar no cubre origin:null (archivo local), lo hacemos manualmente.
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse

class PermissiveCORSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        # Preflight OPTIONS — responder directamente sin pasar al handler
        if request.method == "OPTIONS":
            response = StarletteResponse(status_code=204)
        else:
            response = await call_next(request)
        # Añadir cabeceras CORS a TODAS las respuestas
        origin = request.headers.get("origin", "*")
        response.headers["Access-Control-Allow-Origin"]  = origin if origin else "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
        response.headers["Access-Control-Allow-Headers"] = "*"
        response.headers["Access-Control-Allow-Credentials"] = "false"
        response.headers["Access-Control-Max-Age"] = "86400"
        return response

app.add_middleware(PermissiveCORSMiddleware)

# ─── Auto-detect max qubits from available RAM ────────────────────────────────

def compute_max_qubits() -> int:
    total_ram_bytes = psutil.virtual_memory().total
    usable_bytes = total_ram_bytes * 0.60
    max_q = int(math.log2(usable_bytes / 16))
    max_q = max(4, min(max_q, 40))
    return max_q

MAX_QUBITS = compute_max_qubits()
total_ram_gb = psutil.virtual_memory().total / (1024**3)
usable_ram_gb = total_ram_gb * 0.60
max_state_mb = (2**MAX_QUBITS * 16) / (1024**2)

logger.info(f"System RAM: {total_ram_gb:.1f} GB total, {usable_ram_gb:.1f} GB usable (60%)")
logger.info(f"Auto-detected MAX_QUBITS = {MAX_QUBITS} (state vector ~{max_state_mb:.0f} MB)")

# Global quantum state
qc = QuantumState(n_qubits=10)
active_websockets: Set[WebSocket] = set()

# ─── WebSocket broadcast ──────────────────────────────────────────────────────

async def broadcast_state():
    global active_websockets
    if not active_websockets:
        return
    try:
        state_data = qc.get_full_state()
        message = json.dumps({"type": "state_update", "data": state_data})
        dead = set()
        for client_ws in list(active_websockets):
            try:
                await client_ws.send_text(message)
            except Exception:
                dead.add(client_ws)
        active_websockets -= dead
    except Exception as e:
        logger.error(f"broadcast_state error: {e}")

async def broadcast_event(event_type: str, data: Dict):
    global active_websockets
    if not active_websockets:
        return
    try:
        message = json.dumps({"type": event_type, "data": data})
        dead = set()
        for client_ws in list(active_websockets):
            try:
                await client_ws.send_text(message)
            except Exception:
                dead.add(client_ws)
        active_websockets -= dead
    except Exception as e:
        logger.error(f"broadcast_event error: {e}")

# ─── REST API Models ──────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

class GateOp(BaseModel):
    gate: str
    qubits: List[int]
    params: Optional[List[float]] = []

class CircuitOp(BaseModel):
    circuit: List[GateOp]

class AlgorithmOp(BaseModel):
    name: str
    params: Optional[Dict[str, Any]] = {}

class QubitToggle(BaseModel):
    qubit: int
    enabled: bool

class EnterpriseOp(BaseModel):
    name: str
    params: Optional[Dict[str, Any]] = {}

class SampleRequest(BaseModel):
    shots: int = 1024

class NoiseRequest(BaseModel):
    level: float = 0.0

# ─── AUTH Endpoints (sin protección) ─────────────────────────────────────────

@app.post("/auth/login")
async def login(req: LoginRequest):
    """Login con usuario y contraseña. Devuelve JWT token."""
    if req.username != AUTH_USERNAME or req.password != AUTH_PASSWORD:
        # Delay pequeño para dificultar brute-force
        await asyncio.sleep(1.0)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o contraseña incorrectos.",
        )
    token = create_token(req.username)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in_hours": JWT_EXPIRE_HOURS,
        "username": req.username,
    }

@app.get("/auth/verify")
async def verify_auth(username: str = Depends(require_auth)):
    """Verifica si el token actual es válido."""
    return {"valid": True, "username": username}

# ─── REST Endpoints (protegidos con JWT) ─────────────────────────────────────

@app.get("/api/state")
async def get_state(username: str = Depends(require_auth)):
    return qc.get_full_state()

@app.get("/api/qubits")
async def get_qubits(username: str = Depends(require_auth)):
    return {"n_qubits": qc.n_qubits, "qubits": qc.get_qubit_probabilities()}

@app.post("/api/gate")
async def apply_gate(op: GateOp, username: str = Depends(require_auth)):
    try:
        result = qc.apply_gate(op.gate, op.qubits, op.params)
        await broadcast_state()
        await broadcast_event("gate_applied", {"gate": op.gate, "qubits": op.qubits})
        return {"success": True, "operation": result, "n_qubits": qc.n_qubits}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/circuit")
async def run_circuit(op: CircuitOp, username: str = Depends(require_auth)):
    try:
        ops = [{"gate": g.gate, "qubits": g.qubits, "params": g.params} for g in op.circuit]
        results = qc.run_circuit(ops)
        await broadcast_state()
        await broadcast_event("circuit_executed", {"ops": len(results)})
        return {"success": True, "results": results}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/measure/{qubit}")
async def measure_qubit(qubit: int, username: str = Depends(require_auth)):
    if qubit < 0 or qubit >= qc.n_qubits:
        raise HTTPException(status_code=400, detail="Invalid qubit index")
    result = qc.measure_qubit(qubit)
    await broadcast_state()
    await broadcast_event("measured", {"qubit": qubit, "result": result})
    return {"qubit": qubit, "result": result}

@app.post("/api/measure_all")
async def measure_all(username: str = Depends(require_auth)):
    results = qc.measure_all()
    await broadcast_state()
    await broadcast_event("measured_all", {"results": results})
    return {"results": results}

@app.post("/api/sample")
async def sample_counts(req: SampleRequest, username: str = Depends(require_auth)):
    """Sample the measurement distribution over many shots (real QPU behaviour).
    Does NOT collapse the live state — returns a histogram of outcomes."""
    result = qc.sample_counts(req.shots)
    await broadcast_event("sampled", {"shots": result["shots"], "distinct": result["distinct"]})
    return result

@app.post("/api/noise")
async def set_noise(req: NoiseRequest, username: str = Depends(require_auth)):
    """Set per-gate depolarizing noise (0 = ideal, >0 = noisy NISQ device)."""
    level = qc.set_noise(req.level)
    await broadcast_state()
    await broadcast_event("noise_set", {"level": level})
    return {"success": True, "noise": level}

@app.get("/api/metrics")
async def get_metrics(username: str = Depends(require_auth)):
    """Quantum figures of merit: entropy, entanglement, participation ratio."""
    return qc.get_metrics()

@app.get("/api/qasm")
async def export_qasm(username: str = Depends(require_auth)):
    """Export the executed circuit as OpenQASM 2.0."""
    return {"qasm": qc.to_qasm(), "n_qubits": qc.n_qubits, "depth": len(qc.circuit_ops)}

@app.post("/api/reset")
async def reset_state(username: str = Depends(require_auth)):
    qc.reset()
    await broadcast_state()
    await broadcast_event("reset", {})
    return {"success": True, "n_qubits": qc.n_qubits}

@app.post("/api/algorithm")
async def run_algorithm(op: AlgorithmOp, username: str = Depends(require_auth)):
    try:
        result = qc.run_algorithm(op.name, op.params or {})
        qc.last_algorithm_result = result
        state_snapshot = qc.get_full_state()
        await broadcast_state()
        await broadcast_event("algorithm_run", {"name": op.name, "result": result, "state": state_snapshot})
        return {"success": True, "result": result, "state": state_snapshot}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/enterprise")
async def run_enterprise(op: EnterpriseOp, username: str = Depends(require_auth)):
    """Run a real-world enterprise quantum solution (finance, logistics, security, chemistry)."""
    try:
        result = qc.run_enterprise(op.name, op.params or {})
        qc.last_algorithm_result = result
        state_snapshot = qc.get_full_state()
        await broadcast_state()
        await broadcast_event("enterprise_run", {"name": op.name, "result": result, "state": state_snapshot})
        return {"success": True, "result": result, "state": state_snapshot}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/enterprise/list")
async def list_enterprise(username: str = Depends(require_auth)):
    return [
        {"name": "portfolio",       "label": "Optimización de Cartera", "industry": "Finanzas",        "icon": "$",  "description": "Selecciona los mejores activos según tus rendimientos y riesgos"},
        {"name": "knapsack",        "label": "Selección con Presupuesto","industry": "Operaciones",     "icon": "▦",  "description": "Elige proyectos de máximo valor dentro de tu presupuesto"},
        {"name": "task_assignment", "label": "Asignación de Tareas",     "industry": "Operaciones",     "icon": "⊞",  "description": "Asigna equipos a tareas minimizando el coste total"},
        {"name": "maxcut",          "label": "Optimización de Red",      "industry": "Logística",       "icon": "⋈",  "description": "Particiona redes y rutas según tus conexiones"},
        {"name": "grover_search",   "label": "Búsqueda en Datos",        "industry": "Datos",           "icon": "⌕",  "description": "Encuentra un registro en datos sin índice (Grover)"},
        {"name": "swap_similarity", "label": "Similitud / Fraude",       "industry": "IA y Riesgo",     "icon": "≈",  "description": "Compara dos perfiles para fraude o recomendación"},
        {"name": "bb84",            "label": "Clave Cuántica (BB84)",    "industry": "Ciberseguridad",  "icon": "K",  "description": "Comunicación inviolable con detección de espías"},
        {"name": "qrng",            "label": "Claves Aleatorias (QRNG)", "industry": "Ciberseguridad",  "icon": "#",  "description": "Genera claves AES-256 verdaderamente aleatorias"},
        {"name": "vqe_h2",          "label": "Simulación Molecular",     "industry": "Química / Farma", "icon": "H₂", "description": "Energía molecular del H₂ a tu distancia de enlace (VQE)"},
    ]

@app.post("/api/qubits/add")
async def add_qubit(username: str = Depends(require_auth)):
    if qc.n_qubits >= MAX_QUBITS:
        mem_needed_gb = (2**MAX_QUBITS * 16) / (1024**3)
        raise HTTPException(status_code=400, detail=f"Límite máximo: {MAX_QUBITS} qubits (~{mem_needed_gb:.1f} GB RAM). Tu sistema tiene {total_ram_gb:.1f} GB.")
    new_n = qc.add_qubit()
    await broadcast_state()
    await broadcast_event("qubit_added", {"n_qubits": new_n})
    return {"success": True, "n_qubits": new_n}

@app.post("/api/qubits/remove")
async def remove_qubit(username: str = Depends(require_auth)):
    if qc.n_qubits <= 1:
        raise HTTPException(status_code=400, detail="Cannot remove last qubit")
    qubit_to_remove = qc.n_qubits - 1
    new_n = qc.remove_qubit(qubit_to_remove)
    await broadcast_state()
    await broadcast_event("qubit_removed", {"n_qubits": new_n, "removed": qubit_to_remove})
    return {"success": True, "n_qubits": new_n}

@app.post("/api/qubits/remove/{qubit}")
async def remove_specific_qubit(qubit: int, username: str = Depends(require_auth)):
    if qubit < 0 or qubit >= qc.n_qubits:
        raise HTTPException(status_code=400, detail="Invalid qubit index")
    if qc.n_qubits <= 1:
        raise HTTPException(status_code=400, detail="Cannot remove last qubit")
    new_n = qc.remove_qubit(qubit)
    await broadcast_state()
    await broadcast_event("qubit_removed", {"n_qubits": new_n, "removed": qubit})
    return {"success": True, "n_qubits": new_n}

@app.post("/api/qubits/toggle")
async def toggle_qubit(op: QubitToggle, username: str = Depends(require_auth)):
    if op.qubit < 0 or op.qubit >= qc.n_qubits:
        raise HTTPException(status_code=400, detail="Invalid qubit index")
    qc.enable_qubit(op.qubit, op.enabled)
    await broadcast_state()
    return {"success": True, "qubit": op.qubit, "enabled": op.enabled}

@app.post("/api/qubits/reset/{qubit}")
async def reset_qubit(qubit: int, username: str = Depends(require_auth)):
    if qubit < 0 or qubit >= qc.n_qubits:
        raise HTTPException(status_code=400, detail="Invalid qubit index")
    qc.reset_qubit(qubit)
    await broadcast_state()
    return {"success": True, "qubit": qubit}

@app.get("/api/gates")
async def list_gates(username: str = Depends(require_auth)):
    return {
        "single_qubit": [
            {"name": "H",   "label": "Hadamard",   "params": 0, "description": "Superposition"},
            {"name": "X",   "label": "Pauli-X",    "params": 0, "description": "Bit flip (NOT)"},
            {"name": "Y",   "label": "Pauli-Y",    "params": 0, "description": "Bit+phase flip"},
            {"name": "Z",   "label": "Pauli-Z",    "params": 0, "description": "Phase flip"},
            {"name": "S",   "label": "S Gate",     "params": 0, "description": "π/2 phase"},
            {"name": "T",   "label": "T Gate",     "params": 0, "description": "π/4 phase"},
            {"name": "SX",  "label": "√X Gate",    "params": 0, "description": "Square root of X"},
            {"name": "RX",  "label": "Rx(θ)",      "params": 1, "description": "X-rotation"},
            {"name": "RY",  "label": "Ry(θ)",      "params": 1, "description": "Y-rotation"},
            {"name": "RZ",  "label": "Rz(θ)",      "params": 1, "description": "Z-rotation"},
            {"name": "P",   "label": "Phase(φ)",   "params": 1, "description": "Phase shift"},
        ],
        "two_qubit": [
            {"name": "CNOT","label": "CNOT",  "params": 0, "description": "Controlled-NOT"},
            {"name": "CZ",  "label": "CZ",    "params": 0, "description": "Controlled-Z"},
            {"name": "CY",  "label": "CY",    "params": 0, "description": "Controlled-Y"},
            {"name": "CH",  "label": "CH",    "params": 0, "description": "Controlled-H"},
            {"name": "SWAP","label": "SWAP",  "params": 0, "description": "Swap qubits"},
            {"name": "ISWAP","label":"iSWAP", "params": 0, "description": "Imaginary swap"},
            {"name": "CRX", "label": "CRx(θ)","params": 1, "description": "Controlled X-rotation"},
            {"name": "CRY", "label": "CRy(θ)","params": 1, "description": "Controlled Y-rotation"},
            {"name": "CRZ", "label": "CRz(θ)","params": 1, "description": "Controlled Z-rotation"},
            {"name": "RXX", "label": "Rxx(θ)","params": 1, "description": "Ising XX coupling"},
            {"name": "RYY", "label": "Ryy(θ)","params": 1, "description": "Ising YY coupling"},
            {"name": "RZZ", "label": "Rzz(θ)","params": 1, "description": "Ising ZZ coupling"},
        ],
        "three_qubit": [
            {"name": "CCX",  "label": "Toffoli", "params": 0, "description": "Double-controlled NOT"},
            {"name": "CSWAP","label": "Fredkin", "params": 0, "description": "Controlled SWAP"},
        ],
    }

@app.get("/api/algorithms")
async def list_algorithms(username: str = Depends(require_auth)):
    return [
        {"name": "bell_state",        "label": "Bell State",             "description": "Crea par entrelazado (EPR)",                       "qubits_needed": 2},
        {"name": "ghz",               "label": "GHZ State",              "description": "Entrelazamiento multipartito",                      "qubits_needed": 3},
        {"name": "qft",               "label": "QFT",                    "description": "Transformada de Fourier Cuántica",                   "qubits_needed": 4},
        {"name": "grover",            "label": "Grover's Search",        "description": "Búsqueda cuadráticamente más rápida",               "qubits_needed": 3},
        {"name": "quantum_teleportation","label": "Teleportación",        "description": "Teleportación cuántica de qubit",                   "qubits_needed": 3},
        {"name": "bernstein_vazirani","label": "Bernstein-Vazirani",     "description": "Revela cadena secreta en 1 consulta",               "qubits_needed": 5},
        {"name": "deutsch",           "label": "Deutsch",                "description": "Constante vs. balanceada en 1 eval.",               "qubits_needed": 2},
        {"name": "random",            "label": "Aleatoriedad Cuántica",  "description": "Superposición completa de todos los qubits",        "qubits_needed": 1},
        {"name": "shor",              "label": "Algoritmo de Shor",      "description": "Factorización polinómica — rompe RSA",              "qubits_needed": 4},
        {"name": "simon",             "label": "Algoritmo de Simon",     "description": "Periodicidad oculta con ventaja exponencial",       "qubits_needed": 4},
        {"name": "phase_estimation",  "label": "Estimación de Fase (QPE)","description": "Núcleo de Shor y química cuántica",               "qubits_needed": 3},
        {"name": "swap_test",         "label": "SWAP Test",              "description": "Similitud cuántica — kernel para QML",             "qubits_needed": 3},
        {"name": "w_state",           "label": "Estado W",               "description": "Entrelazamiento robusto multipartito",             "qubits_needed": 3},
    ]

@app.get("/api/info")
async def get_info(username: str = Depends(require_auth)):
    return {
        "name": "Omega Core Quantum",
        "version": "1.0.0",
        "n_qubits": qc.n_qubits,
        "max_qubits": MAX_QUBITS,
        "total_ram_gb": round(total_ram_gb, 1),
        "usable_ram_gb": round(usable_ram_gb, 1),
        "max_state_vector_mb": round(max_state_mb, 0),
        "hilbert_dim": 2 ** qc.n_qubits,
        "state_norm": float(np.sum(np.abs(qc.state)**2)),
        "circuit_depth": len(qc.circuit_ops),
        "connected_clients": len(active_websockets),
        "noise": qc.noise,
        "metrics": qc.get_metrics(),
    }

# ─── WebSocket endpoint (protegido con token en query param) ──────────────────
# Conexión: ws://host:3333/ws?token=<JWT>

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # Leer token desde query param ?token=...
    token = websocket.query_params.get("token")
    username = verify_token(token) if token else None

    if not username:
        # Rechazar conexión sin cerrar con error duro — mandamos mensaje y cerramos limpiamente
        await websocket.accept()
        await websocket.send_text(json.dumps({
            "type": "auth_error",
            "data": {"message": "Token inválido o expirado. Inicia sesión de nuevo."}
        }))
        await websocket.close(code=4401)
        return

    await websocket.accept()
    active_websockets.add(websocket)
    logger.info(f"[WS] '{username}' conectado. Total: {len(active_websockets)}")

    # Send initial state
    try:
        state = qc.get_full_state()
        await websocket.send_text(json.dumps({"type": "state_update", "data": state}))
        await websocket.send_text(json.dumps({"type": "connected", "data": {"n_qubits": qc.n_qubits, "username": username}}))
    except Exception as e:
        logger.error(f"Error sending initial state: {e}")

    try:
        while True:
            msg = await websocket.receive_text()
            try:
                data = json.loads(msg)
                cmd = data.get("cmd")

                if cmd == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))

                elif cmd == "get_state":
                    state = qc.get_full_state()
                    await websocket.send_text(json.dumps({"type": "state_update", "data": state}))

                elif cmd == "gate":
                    gate   = data.get("gate")
                    qubits = data.get("qubits", [])
                    params = data.get("params", [])
                    qc.apply_gate(gate, qubits, params)
                    await broadcast_state()

                elif cmd == "measure":
                    qubit = data.get("qubit")
                    if qubit is not None:
                        result = qc.measure_qubit(qubit)
                        await broadcast_state()
                        await broadcast_event("measured", {"qubit": qubit, "result": result})

                elif cmd == "enterprise":
                    name   = data.get("name")
                    eparams = data.get("params", {})
                    result = qc.run_enterprise(name, eparams)
                    qc.last_algorithm_result = result
                    await broadcast_state()
                    await broadcast_event("enterprise_run", {"name": name, "result": result})

                elif cmd == "sample":
                    shots = int(data.get("shots", 1024))
                    result = qc.sample_counts(shots)
                    await websocket.send_text(json.dumps({"type": "sample_result", "data": result}))

                elif cmd == "noise":
                    level = qc.set_noise(float(data.get("level", 0.0)))
                    await broadcast_state()
                    await broadcast_event("noise_set", {"level": level})

                elif cmd == "reset":
                    qc.reset()
                    await broadcast_state()

                elif cmd == "algorithm":
                    name   = data.get("name")
                    params = data.get("params", {})
                    result = qc.run_algorithm(name, params)
                    await broadcast_state()
                    await broadcast_event("algorithm_run", {"name": name, "result": result})

                elif cmd == "add_qubit":
                    if qc.n_qubits < MAX_QUBITS:
                        new_n = qc.add_qubit()
                        await broadcast_state()
                        await broadcast_event("qubit_added", {"n_qubits": new_n})
                    else:
                        await websocket.send_text(json.dumps({"type": "error", "data": {"message": f"Límite: {MAX_QUBITS} qubits (RAM: {total_ram_gb:.1f} GB)"}}))

                elif cmd == "remove_qubit":
                    qubit = data.get("qubit", qc.n_qubits - 1)
                    if qc.n_qubits > 1:
                        new_n = qc.remove_qubit(qubit)
                        await broadcast_state()
                        await broadcast_event("qubit_removed", {"n_qubits": new_n})

            except Exception as e:
                logger.error(f"Error processing WS message: {e}")
                await websocket.send_text(json.dumps({"type": "error", "data": {"message": str(e)}}))

    except WebSocketDisconnect:
        active_websockets.discard(websocket)
        logger.info(f"[WS] '{username}' desconectado. Total: {len(active_websockets)}")

# ─── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    logger.info("Starting Omega Core Quantum on port 3333...")
    uvicorn.run(app, host="0.0.0.0", port=3333, log_level="info")
