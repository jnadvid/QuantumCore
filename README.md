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
