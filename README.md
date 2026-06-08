# Omega Core Quantum

Simulador de ordenador cuántico por **vector de estado** completo: aplica puertas reales,
ejecuta algoritmos cuánticos, mide (colapso real) y visualiza esferas de Bloch,
entrelazamiento y métricas en tiempo real.

- **Backend**: FastAPI + WebSocket (Python), motor de simulación en `server/quantum_engine.py`.
- **Frontend**: HTML/CSS/JS puro (`client/`), sin build.
- **Auth**: JWT. Acceso por defecto → usuario **`admin`** · contraseña **`admin1234`**.

---

## 🚀 Instalación en Windows (1 clic)

1. Descarga/clona el proyecto.
2. Haz doble clic en **`install_windows.bat`**.
   - Detecta (o instala vía `winget`) Python, crea un entorno virtual, instala las
     dependencias, arranca el servidor y abre la interfaz en el navegador.
3. Inicia sesión con **admin / admin1234**.

Para volver a arrancar más tarde sin reinstalar: doble clic en **`start_windows.bat`**.

> El servidor escucha en `http://127.0.0.1:3333`. La contraseña se fija mediante las
> variables de entorno `QC_USER` / `QC_PASSWORD`; cámbialas en los `.bat` para producción.

### Linux / macOS

```bash
cd server
pip install -r requirements.txt
QC_USER=admin QC_PASSWORD=admin1234 python3 server.py
# abre client/index.html en el navegador
```

---

## 🏢 Casos de uso (con tus datos reales)

El **centro de trabajo** (panel central) muestra todos los casos de uso. Al elegir uno,
introduces **tus propios datos** en un formulario y el ordenador cuántico simulado
ejecuta el cálculo **real** sobre esos datos, devolviendo KPIs, gráficas y la solución óptima.

| Caso de uso | Industria | Tecnología | Datos que introduces |
|----------|-----------|------------|----------------------|
| **Optimización de Cartera** | Finanzas | QAOA / QUBO (Markowitz) | Activos con rendimiento y riesgo, nº a elegir |
| **Selección con Presupuesto** | Operaciones | QUBO (mochila) | Opciones con valor/coste y presupuesto |
| **Asignación de Tareas** | Operaciones / RRHH | QUBO one-hot | Matriz de costes equipo×tarea |
| **Optimización de Red (Max-Cut)** | Logística | QAOA | Nodos y conexiones de tu red |
| **Búsqueda en Datos (Grover)** | Datos | Grover | Lista de registros y objetivo |
| **Similitud / Fraude (SWAP Test)** | IA y Riesgo | SWAP Test | Dos perfiles a comparar |
| **Clave Cuántica (BB84)** | Ciberseguridad | QKD | Longitud de clave, espía sí/no |
| **Claves Aleatorias (QRNG)** | Ciberseguridad | Colapso cuántico | — (genera AES-256/contraseña/token) |
| **Simulación Molecular (VQE)** | Química / Farma | VQE | Distancia de enlace H–H (Å) |

> Cálculos cuánticos reales: el solver QAOA aplica la capa de coste `e^{-iγC}` de forma
> exacta sobre el vector de estado para cualquier QUBO; el VQE alcanza precisión química
> (error < 1.6 mHa) y reproduce la energía del H₂ (−1.136 Ha a 0.74 Å); BB84 detecta
> intrusos cuando el QBER supera el 11 %; Grover amplifica el registro objetivo en √N pasos.

## ✨ Novedades de esta versión

### Comportamiento de ordenador cuántico más realista
- **Muestreo por *shots*** (`⇶ Muestreo`): ejecuta N mediciones y muestra el histograma
  de resultados **sin colapsar** el estado en vivo — igual que una QPU real devuelve *counts*.
- **Modelo de ruido / decoherencia**: deslizador de ruido (depolarizante por puerta,
  trayectorias cuánticas) para simular un dispositivo **NISQ** ruidoso. 0 % = ideal.
- **Entrelazamiento real**: el mapa de correlaciones usa ahora **información mutua cuántica**
  `I(i:j)=S(ρᵢ)+S(ρⱼ)−S(ρᵢⱼ)` (entropía de von Neumann), no una heurística.

### Funciones útiles nuevas
- **Métricas cuánticas en vivo**: entropía de Shannon, entrelazamiento medio (ebits),
  estados activos y *participation ratio*.
- **Exportación OpenQASM 2.0** (`⤓ QASM`): copia o descarga el circuito; compatible con Qiskit.
- **Puertas nuevas**: `iSWAP`, `CRX/CRY/CRZ` (rotaciones controladas), `RXX/RYY/RZZ`
  (acoplos de Ising), `CSWAP` (Fredkin) — disponibles también en el constructor de circuitos.
- **Algoritmo Estado W**: entrelazamiento multipartito robusto (distinto del GHZ).

### Estética
- Fondo *aurora* animado, logo en rotación, paneles con acento dinámico, botones con
  barrido de brillo, títulos en degradado y nuevos componentes (métricas, histograma, QASM).

---

## API REST (requiere `Authorization: Bearer <token>`)

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/auth/login` | Login → JWT |
| GET  | `/api/state` | Estado completo (incluye `metrics`) |
| POST | `/api/gate` | Aplica una puerta |
| POST | `/api/circuit` | Ejecuta un circuito |
| POST | `/api/measure/{q}` · `/api/measure_all` | Medición |
| POST | `/api/sample` | Muestreo por shots (histograma, no destructivo) |
| POST | `/api/noise` | Nivel de ruido del dispositivo |
| GET  | `/api/metrics` | Métricas cuánticas |
| GET  | `/api/qasm` | Exporta el circuito a OpenQASM 2.0 |
| POST | `/api/algorithm` | Ejecuta un algoritmo |
| GET  | `/api/gates` · `/api/algorithms` · `/api/info` | Catálogos e info |
