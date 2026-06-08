/**
 * Omega Core Quantum v2
 * Frontend Application — WebSocket + REST API
 *
 * CLOUD CONFIG: Edit QUANTUM_SERVER_HOST below with your server IP or domain.
 * Example: '1.2.3.4' or 'quantum.midominio.com'
 */

// ─── CONFIGURACIÓN DEL SERVIDOR ──────────────────────────────────────────────
// Cambia esta variable con la IP pública o dominio de tu servidor DigitalOcean
const QUANTUM_SERVER_HOST = window.QUANTUM_SERVER_HOST || '127.0.0.1';
const QUANTUM_SERVER_PORT = window.QUANTUM_SERVER_PORT || '3333';
const QUANTUM_USE_HTTPS   = window.QUANTUM_USE_HTTPS   || false;

const _proto_http = QUANTUM_USE_HTTPS ? 'https' : 'http';
const _proto_ws   = QUANTUM_USE_HTTPS ? 'wss'   : 'ws';
const API_BASE = `${_proto_http}://${QUANTUM_SERVER_HOST}:${QUANTUM_SERVER_PORT}`;
const WS_URL   = `${_proto_ws}://${QUANTUM_SERVER_HOST}:${QUANTUM_SERVER_PORT}/ws`;
// ─────────────────────────────────────────────────────────────────────────────

// ─── AUTH ────────────────────────────────────────────────────────────────────

let _authToken = null;  // JWT token en memoria (no localStorage para mayor seguridad)

/** Añade Authorization header a todas las peticiones fetch */
function authHeaders() {
  return _authToken
    ? { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + _authToken }
    : { 'Content-Type': 'application/json' };
}

/** Wrapper fetch que incluye auth automáticamente */
async function apiFetch(url, options = {}) {
  const res = await fetch(url, {
    ...options,
    headers: { ...authHeaders(), ...(options.headers || {}) },
  });
  if (res.status === 401 || res.status === 403) {
    // Token expirado — forzar logout
    showLoginOverlay('Sesión expirada. Inicia sesión de nuevo.');
    throw new Error('Unauthorized');
  }
  return res;
}

/** Muestra el overlay de login con mensaje opcional */
function showLoginOverlay(msg = '') {
  _authToken = null;
  const overlay = document.getElementById('login-overlay');
  if (overlay) overlay.style.display = 'flex';
  if (msg) showLoginError(msg);
  // Cerrar WebSocket si estaba abierto
  if (ws) { try { ws.close(); } catch(e) {} ws = null; }
}

/** Oculta el overlay de login */
function hideLoginOverlay() {
  const overlay = document.getElementById('login-overlay');
  if (overlay) {
    overlay.style.transition = 'opacity .4s';
    overlay.style.opacity = '0';
    setTimeout(() => { overlay.style.display = 'none'; overlay.style.opacity = '1'; }, 400);
  }
}

function showLoginError(msg) {
  const el = document.getElementById('login-error');
  if (el) { el.textContent = msg; el.style.display = 'block'; }
}

function hideLoginError() {
  const el = document.getElementById('login-error');
  if (el) el.style.display = 'none';
}

/** Función llamada por el botón de login del HTML */
async function doLogin() {
  const userEl = document.getElementById('login-user');
  const passEl = document.getElementById('login-pass');
  const btn    = document.getElementById('login-btn');
  const user   = (userEl?.value || '').trim();
  const pass   = passEl?.value || '';

  if (!user || !pass) { showLoginError('Introduce usuario y contraseña.'); return; }

  hideLoginError();
  btn.disabled = true;
  btn.textContent = 'CONECTANDO...';

  // Helper: parsear respuesta de forma segura aunque no sea JSON
  async function safeJson(res) {
    const text = await res.text();
    try { return JSON.parse(text); }
    catch(e) {
      // El servidor devolvió HTML u otra cosa — mostrar los primeros caracteres para diagnóstico
      const preview = text.slice(0, 120).replace(/</g, '&lt;');
      throw new Error('El servidor no devuelve JSON. Respuesta HTTP ' + res.status + ': ' + preview);
    }
  }

  try {
    // 1º — Test de conectividad rápido (OPTIONS/HEAD a /auth/login)
    btn.textContent = 'VERIFICANDO SERVIDOR...';
    let reachable = false;
    try {
      const probe = await fetch(API_BASE + '/auth/login', { method: 'OPTIONS', signal: AbortSignal.timeout(5000) });
      reachable = true;
    } catch(probeErr) {
      // fetch falló del todo — servidor inaccesible o CORS bloqueando antes de llegar
      const detail = probeErr.message || '';
      if (detail.includes('Failed to fetch') || detail.includes('NetworkError') || detail.includes('Load failed')) {
        showLoginError(
          '❌ No se puede alcanzar el servidor en ' + API_BASE + '.\n\n' +
          'Comprueba:\n' +
          '• El servidor está corriendo (systemctl status quantum)\n' +
          '• El puerto 3333 está abierto en el firewall de DigitalOcean\n' +
          '• La IP en index.html es correcta'
        );
      } else {
        showLoginError('❌ Error de red: ' + detail);
      }
      btn.disabled = false;
      btn.textContent = 'REINTENTAR';
      return;
    }

    // 2º — Login real
    btn.textContent = 'AUTENTICANDO...';
    const res = await fetch(API_BASE + '/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: user, password: pass }),
      signal: AbortSignal.timeout(10000),
    });

    const data = await safeJson(res);

    if (!res.ok) {
      showLoginError('❌ ' + (data.detail || 'Credenciales incorrectas.'));
      btn.disabled = false;
      btn.textContent = 'INICIAR SESIÓN';
      return;
    }

    // Login OK
    _authToken = data.access_token;
    hideLoginOverlay();
    await init();

  } catch(e) {
    // Mostrar el mensaje real del error para facilitar el diagnóstico
    const msg = e.message || String(e);
    showLoginError('❌ ' + msg);
    btn.disabled = false;
    btn.textContent = 'REINTENTAR';
  }
}

/** Arranque: mostrar el overlay de login y rellenar info del servidor */
function startLoginFlow() {
  const infoEl = document.getElementById('login-server-info');
  if (infoEl) infoEl.textContent = 'Servidor: ' + API_BASE;
  // Permitir login con Enter
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const overlay = document.getElementById('login-overlay');
      if (overlay && overlay.style.display !== 'none') doLogin();
    }
  });
}

// ─────────────────────────────────────────────────────────────────────────────

// ─── State ─────────────────────────────────────────────────────────────────

let ws = null;
let wsReconnectTimer = null;
let pollingMode = false;
let pollingInterval = null;

let state = {
  n_qubits: 0, qubit_states: [], statevector: [],
  entanglement: [], circuit: [], norm: 1
};

let probChart = null;
let selectedQubits = [];
let stateFilter = '';
let gates = { single_qubit: [], two_qubit: [], three_qubit: [] };
let algorithms = [];
let blochCompact = false;

// Builder state
let builderSteps = [];
let builderGateCount = 8;
let selectedBuilderGate = null;
let activeAlgoBtn = null;
let pendingAlgo = null;
let pendingGateDef = null;

// ─── Algorithm Info Database ────────────────────────────────────────────────

const ALGO_INFO = {
  bell_state: {
    icon: 'Φ⁺',
    category: 'Información Cuántica',
    what: 'Crea un <strong>par de Bell</strong> (estado EPR): dos qubits perfectamente entrelazados. Al medir uno, el estado del otro queda instantáneamente determinado, independientemente de la distancia.',
    how: 'Se aplica una puerta <strong>Hadamard (H)</strong> al qubit de control para crear superposición |+⟩, y luego una <strong>CNOT</strong> para enredar ambos qubits. El resultado es el estado (|00⟩ + |11⟩)/√2.',
    circuit: `q₀: ─[H]─●─────
q₁: ──────⊕─────`,
    apps: ['Criptografía cuántica (QKD)', 'Teleportación cuántica', 'Superdense coding', 'Tests de Bell / no-localidad'],
    complexity: [
      { label: 'Puertas', val: '2 (H + CNOT)' },
      { label: 'Qubits', val: '2' },
      { label: 'Ventaja cuántica', val: 'No-localidad' },
    ],
    note: 'Base de la comunicación cuántica segura'
  },
  ghz: {
    icon: 'GHZ',
    category: 'Entrelazamiento Multipartito',
    what: 'Crea el estado <strong>Greenberger–Horne–Zeilinger</strong>: N qubits entrelazados simultáneamente en la superposición (|000…0⟩ + |111…1⟩)/√2. El colapso de uno colapsa todos.',
    how: 'Una H en el primer qubit crea superposición. Cadena de CNOT propaga el entrelazamiento secuencialmente a todos los demás qubits. Para N qubits se requieren N−1 puertas CNOT.',
    circuit: `q₀: ─[H]─●──────●───
q₁: ──────⊕──●──│───
q₂: ─────────⊕──│───
qₙ: ────────────⊕───`,
    apps: ['Computación cuántica distribuida', 'Metrología cuántica', 'Corrección de errores cuánticos', 'Test de localidad multiqubit'],
    complexity: [
      { label: 'Puertas', val: 'N (1H + N-1 CNOT)' },
      { label: 'Qubits', val: 'N ≥ 3' },
      { label: 'Profundidad', val: 'O(N)' },
    ],
    note: 'Entrelazamiento genuinamente multipartito'
  },
  qft: {
    icon: 'QFT',
    category: 'Transformadas / Periodicidad',
    what: 'La <strong>Transformada de Fourier Cuántica</strong> es la versión cuántica de la DFT. Extrae información de periodicidad de un estado cuántico de forma exponencialmente más eficiente que la FFT clásica.',
    how: 'Para cada qubit: aplica H, luego puertas de fase controladas R_k con ángulos π/2^k desde los qubits siguientes. Al final, el espectro de frecuencias queda codificado en las amplitudes del estado.',
    circuit: `q₀: ─[H]─[R₂]─[R₃]─···
q₁: ──────●────[H]─[R₂]─
q₂: ──────────────●──[H]─`,
    apps: ['Algoritmo de Shor (factorización)', 'Estimación de fase cuántica', 'Resolución de ecuaciones de onda', 'Física cuántica computacional'],
    complexity: [
      { label: 'Puertas', val: 'O(n²)' },
      { label: 'vs FFT clásica', val: 'O(n·2ⁿ) → O(n²)' },
      { label: 'Ventaja', val: 'Exponencial' },
    ],
    note: 'Núcleo del algoritmo de factorización de Shor'
  },
  grover: {
    icon: '⊗G',
    category: 'Búsqueda / Optimización',
    what: 'El algoritmo de <strong>Grover</strong> busca un elemento marcado en una base de datos no estructurada de N elementos en O(√N) evaluaciones, comparado con O(N) clásico — una aceleración cuadrática garantizada.',
    how: 'Inicializa superposición uniforme. Itera el operador de Grover: (1) Oráculo de fase — invierte la fase del estado objetivo; (2) Difusión — amplifica la amplitud del objetivo, disminuye las demás. Tras O(π/4·√N) iteraciones, medir da el resultado con alta probabilidad.',
    circuit: `Init: [H⊗ⁿ]
Iter: ─[Oráculo]─[2|s⟩⟨s|−I]─
      × O(√N) veces`,
    apps: ['Búsqueda en bases de datos', 'Resolución de SAT / NP', 'Optimización combinatoria', 'Criptoanálisis (brute-force cuántico)'],
    complexity: [
      { label: 'Clásico', val: 'O(N)' },
      { label: 'Grover', val: 'O(√N)' },
      { label: 'Aceleración', val: 'Cuadrática' },
    ],
    note: 'Provablemente óptimo para búsqueda no estructurada'
  },
  quantum_teleportation: {
    icon: '⇌ψ',
    category: 'Información Cuántica',
    what: 'Transfiere el estado cuántico completo de un qubit a otro qubit distante, usando solo comunicación clásica (2 bits) y un par de Bell pre-compartido. No viola relatividad: sin canal clásico no hay teleportación.',
    how: '(1) Alice y Bob comparten par de Bell. (2) Alice aplica CNOT y H a su qubit + el par. (3) Alice mide 2 bits clásicos. (4) Bob aplica correcciones X y/o Z según los bits recibidos. El qubit de Bob colapsa al estado original.',
    circuit: `Alice─[ψ]─[H]─●────[M]──────────
              ─────────⊕────[M]──X─┐
Bob   ────────────────────────────Z─[ψ]`,
    apps: ['Redes cuánticas de larga distancia', 'Computación cuántica en la nube', 'Repetidores cuánticos', 'Internet cuántica'],
    complexity: [
      { label: 'Puertas', val: '5 (H, CNOT, 2M, X/Z)' },
      { label: 'Canal clásico', val: '2 bits' },
      { label: 'Par de Bell', val: '1 (epr)' },
    ],
    note: 'Estado del qubit origen se destruye (no-clonación)'
  },
  bernstein_vazirani: {
    icon: 'BV',
    category: 'Consulta / Algoritmos de Oráculo',
    what: 'El algoritmo de <strong>Bernstein-Vazirani</strong> revela una cadena binaria secreta <em>s</em> de n bits con <strong>una sola consulta</strong> al oráculo, comparado con O(n) consultas clásicas necesarias.',
    how: 'El oráculo implementa f(x) = s·x (mod 2). Prepara estado de entrada en superposición con H y ancilla en |−⟩. Una única llamada al oráculo codifica todos los bits de s en las fases. H inversa extrae s directamente al medir.',
    circuit: `qᵢ:  [H]──[Oráculo f(x)=s·x]──[H]──[M→sᵢ]
anc: [X][H]────────────────────────────────`,
    apps: ['Protocolos de consulta cuántica', 'Criptoanálisis teórico', 'Verificación de hardware cuántico', 'Benchmark de coherencia'],
    complexity: [
      { label: 'Clásico', val: 'O(n) consultas' },
      { label: 'Cuántico', val: '1 consulta' },
      { label: 'Aceleración', val: 'Lineal garantizada' },
    ],
    note: 'Primer algoritmo con ventaja cuántica demostrable'
  },
  deutsch: {
    icon: 'D-J',
    category: 'Algoritmos de Oráculo',
    what: 'El algoritmo de <strong>Deutsch</strong> determina si una función de 1 bit f: {0,1}→{0,1} es <em>constante</em> (f(0)=f(1)) o <em>balanceada</em> (f(0)≠f(1)) con <strong>una sola evaluación</strong>. Clásicamente necesitas 2.',
    how: 'El qubit ancilla en |−⟩ convierte la evaluación en un cambio de fase (kickback). La H en el qubit de entrada crea interferencia. Si f es constante, las amplitudes interfieren constructivamente en |0⟩; si es balanceada, en |1⟩.',
    circuit: `q₀: ─[H]──[Oráculo Uₓ]──[H]──[M]
q₁: ─[X][H]───────────────────────`,
    apps: ['Primer algoritmo cuántico con ventaja', 'Generalización: Deutsch-Jozsa para n bits', 'Análisis de funciones globales', 'Educación en computación cuántica'],
    complexity: [
      { label: 'Clásico', val: '2 evaluaciones' },
      { label: 'Cuántico', val: '1 evaluación' },
      { label: 'Puertas', val: '4 (H, X, Oráculo, H)' },
    ],
    note: 'El primer "Hello World" de la computación cuántica'
  },
  random: {
    icon: '∞',
    category: 'Aleatoriedad Cuántica',
    what: 'Genera <strong>números aleatorios verdaderamente aleatorios</strong> usando colapso cuántico. A diferencia de los generadores pseudoaleatorios clásicos (deterministas), el colapso de superposición cuántica es intrínsecamente no determinista.',
    how: 'Aplica H a todos los qubits → superposición uniforme sobre 2ⁿ estados. Al medir, cada qubit colapsa independientemente con P(0)=P(1)=0.5. El resultado es un número de n bits generado por azar fundamental de la naturaleza.',
    circuit: `q₀: ─[H]──[M→bit₀]
q₁: ─[H]──[M→bit₁]
qₙ: ─[H]──[M→bitₙ]`,
    apps: ['Criptografía (generación de claves)', 'Simulaciones Monte Carlo', 'Loterías verificablemente justas', 'Protocolos de seguridad (OTP)'],
    complexity: [
      { label: 'Puertas', val: 'N Hadamard' },
      { label: 'Bits generados', val: 'N por ejecución' },
      { label: 'Calidad', val: 'Verdaderamente aleatorio' },
    ],
    note: 'La única fuente de aleatoriedad verdadera conocida'
  },

  shor: {
    icon: '℘',
    category: 'Factorización / Criptoanálisis',
    what: 'El <strong>Algoritmo de Shor</strong> factoriza un entero N = p·q en tiempo polinómico O(n³), rompiendo la criptografía RSA. Un ordenador cuántico con ~4000 qubits lógicos podría factorizar claves RSA-2048 en horas, tarea que tomaría miles de años clásicamente con GNFS.',
    how: '(1) Elige base aleatoria <em>a</em>. (2) Usa la QFT para encontrar el <strong>orden r</strong> de <em>a</em> mód N: el mínimo r tal que aʳ ≡ 1 (mod N). (3) Si r es par: mcd(a^(r/2)±1, N) son factores no triviales. El paso (2) es exponencialmente más rápido en un computador cuántico.',
    circuit: `Init:  |0ⁿ⟩|1⟩
Reg1:  [H⊗n] → [Oracle: a^x mod N] → [QFT⁻¹] → [Medir]
Reg2:  |eigenstate⟩ (ancilla)
Clasic: Fracción continua → extraer r → mcd`,
    apps: ['Ruptura de RSA y criptografía de clave pública', 'Criptografía post-cuántica (motiva NIST PQC)', 'Factorización de números semiprimos', 'Seguridad nacional y defensa'],
    complexity: [
      { label: 'Clásico (GNFS)', val: 'O(e^(n^(1/3)))' },
      { label: 'Shor cuántico', val: 'O(n³) polinómico' },
      { label: 'Ventaja', val: 'Exponencial' },
    ],
    note: 'El algoritmo más temido por la NSA — amenaza toda la criptografía actual'
  },

  simon: {
    icon: 'Σs',
    category: 'Algoritmos de Oráculo / Periodicidad',
    what: 'El <strong>Algoritmo de Simon</strong> encuentra la cadena oculta <em>s</em> de una función f(x)=f(x⊕s) con ventaja <strong>exponencial</strong>: O(n) consultas cuánticas vs O(2ⁿ) clásicas. Fue el primer algoritmo que demostró separación exponencial en complejidad de consultas, inspirando directamente el Algoritmo de Shor.',
    how: 'Superposición uniforme en el registro de entrada. El oráculo codifica la función f en un registro auxiliar por entrelazamiento. Al medir el auxiliar, el registro de entrada colapsa a una superposición de {x, x⊕s}. Hadamard final produce vectores y ortogonales a s. Repetir n veces y resolver el sistema lineal en GF(2).',
    circuit: `Entrada: [H⊗n] → [Oráculo f] → [H⊗n] → [Medir y]
Auxiliar:          → [Medir] → descartado
Clásico: y⋅s=0 (mod 2) → sistema lineal → s`,
    apps: ['Teoría de complejidad cuántica', 'Base matemática de Shor', 'Análisis de funciones con simetría oculta', 'Benchmark de coherencia cuántica'],
    complexity: [
      { label: 'Clásico', val: 'O(2^(n/2)) esperado' },
      { label: 'Simon cuántico', val: 'O(n) consultas' },
      { label: 'Ventaja', val: 'Exponencial garantizada' },
    ],
    note: 'Primera prueba de separación exponencial — inspiró el algoritmo de Shor'
  },

  phase_estimation: {
    icon: 'Φe',
    category: 'Estimación / Subrrutina Central',
    what: 'La <strong>Estimación de Fase Cuántica (QPE)</strong> extrae el autovalor e^(2πiϕ) de un operador unitario U dado su autovector |u⟩. Es la subrrutina central del Algoritmo de Shor (para encontrar el orden), del algoritmo HHL (para sistemas lineales) y de la química cuántica (energías moleculares).',
    how: 'Prepara |u⟩ en el registro de eigenestado. Aplica H⊗n al registro de conteo. Aplica U^(2^k) controlada desde cada qubit k. La QFT inversa decodifica la fase ϕ en binario en el registro de conteo. Medir da ϕ con precisión 1/2ⁿ.',
    circuit: `Conteo: [H⊗n] → [ctrl-U¹, U², U⁴...] → [QFT⁻¹] → Medir
Eigen:  |u⟩ ─────────────────────────────
Fase:   ϕ ≈ medido / 2ⁿ`,
    apps: ['Núcleo de Shor (encontrar orden)', 'Química cuántica (energías moleculares)', 'Algoritmo HHL (sistemas de ecuaciones lineales)', 'Análisis espectral cuántico'],
    complexity: [
      { label: 'Puertas', val: 'O(n² + n·log n)' },
      { label: 'Precisión', val: '1/2ⁿ (bits n)' },
      { label: 'Ventaja', val: 'Exponencial en precisión' },
    ],
    note: 'Subrrutina más importante de la computación cuántica — aparece en casi todos los algoritmos'
  },

  swap_test: {
    icon: '|⟩⟨|',
    category: 'Aprendizaje Automático Cuántico',
    what: 'El <strong>SWAP Test</strong> mide el solapamiento |\u27e8A|B\u27e9|² entre dos estados cuánticos |A⟩ y |B⟩ con <strong>una sola medición</strong>, sin conocer su contenido. Es la operación fundamental del Aprendizaje Automático Cuántico (QML) para calcular distancias y kernels entre vectores de alta dimensión.',
    how: 'Prepara qubit ancilla en |0⟩ y aplica H. Aplica CSWAP (Fredkin) controlada por el ancilla sobre los dos registros |A⟩ y |B⟩. Aplica H al ancilla y mide. P(ancilla=0) = (1+|\u27e8A|B\u27e9|²)/2 — directamente proporcional al solapamiento al cuadrado.',
    circuit: `ancilla: [H] ────●───────[H]─[M]
|A⟩:           ───[CSWAP]─────
|B⟩:           ───[CSWAP]─────
P(0) = (1+|<A|B>|²)/2`,
    apps: ['Clasificación cuántica (QML kernels)', 'Similitud de documentos y vectores', 'Verificación de pureza de estados', 'Subrrutina de VQE y QAOA'],
    complexity: [
      { label: 'Puertas', val: '3 (H + CSWAP + H)' },
      { label: 'Mediciones', val: '1 ancilla' },
      { label: 'Ventaja', val: 'O(1) vs O(2ⁿ) clásico' },
    ],
    note: 'Construye kernels cuánticos para Support Vector Machines y redes neuronales cuánticas'
  },

  w_state: {
    icon: 'W',
    category: 'Entrelazamiento Multipartito',
    what: 'El <strong>Estado W</strong> es una superposición simétrica de todas las configuraciones con exactamente un qubit en |1⟩: (|10…0⟩+|01…0⟩+…+|0…01⟩)/√n. A diferencia del GHZ, su entrelazamiento es <strong>robusto</strong>: medir o perder un qubit deja a los demás aún entrelazados.',
    how: 'Se siembra una excitación en q₀ con una puerta X y se propaga por la cadena mediante rotaciones Y controladas CRY(θᵢ), con θᵢ = 2·arccos(√(1/(n−i))), seguidas de CNOT que reparten la amplitud equitativamente entre todos los qubits.',
    circuit: `q₀: [X]─●──────────────
q₁: ────CRY─⊕─●─────────
q₂: ──────────CRY─⊕─────
        (cadena × n−1)`,
    apps: ['Redes cuánticas tolerantes a pérdidas', 'Memoria cuántica distribuida', 'Protocolos de anonimato cuántico', 'Metrología robusta'],
    complexity: [
      { label: 'Puertas', val: '2(n−1)+1' },
      { label: 'Qubits', val: 'n ≥ 2' },
      { label: 'Robustez', val: 'Sobrevive a 1 pérdida' },
    ],
    note: 'Clase de entrelazamiento distinta al GHZ — no convertibles entre sí por LOCC'
  }
};

// ─── Particle Background ────────────────────────────────────────────────────

(function initBackground() {
  const canvas = document.getElementById('bg-canvas');
  const ctx = canvas.getContext('2d');
  let W = canvas.width = window.innerWidth;
  let H = canvas.height = window.innerHeight;

  const nodes = Array.from({ length: 20 }, () => ({
    x: Math.random() * W, y: Math.random() * H,
    vx: (Math.random() - 0.5) * 0.18, vy: (Math.random() - 0.5) * 0.18,
    pulse: Math.random() * Math.PI * 2,
  }));
  const particles = Array.from({ length: 60 }, () => ({
    x: Math.random() * W, y: Math.random() * H,
    vx: (Math.random() - 0.5) * 0.25, vy: (Math.random() - 0.5) * 0.25,
    r: Math.random() * 1.2 + 0.3,
    hue: Math.random() < 0.7 ? 190 : 260,
    alpha: Math.random() * 0.35 + 0.08,
  }));

  function draw() {
    ctx.clearRect(0, 0, W, H);
    ctx.strokeStyle = 'rgba(0,200,255,0.025)'; ctx.lineWidth = 1;
    for (let x = 0; x < W; x += 65) { ctx.beginPath(); ctx.moveTo(x,0); ctx.lineTo(x,H); ctx.stroke(); }
    for (let y = 0; y < H; y += 65) { ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(W,y); ctx.stroke(); }

    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i]; n.pulse += 0.018;
      const glow = (Math.sin(n.pulse) + 1) * 0.5;
      for (let j = i+1; j < nodes.length; j++) {
        const m = nodes[j];
        const d = Math.hypot(m.x-n.x, m.y-n.y);
        if (d < 240) {
          ctx.strokeStyle = `rgba(0,200,255,${(1-d/240)*0.07})`; ctx.lineWidth = 0.5;
          ctx.beginPath(); ctx.moveTo(n.x,n.y); ctx.lineTo(m.x,m.y); ctx.stroke();
        }
      }
      const g = ctx.createRadialGradient(n.x,n.y,0,n.x,n.y,10);
      g.addColorStop(0, `rgba(0,200,255,${0.35*glow})`); g.addColorStop(1,'transparent');
      ctx.beginPath(); ctx.arc(n.x,n.y,10,0,Math.PI*2); ctx.fillStyle=g; ctx.fill();
      ctx.beginPath(); ctx.arc(n.x,n.y,1.5,0,Math.PI*2); ctx.fillStyle=`rgba(0,200,255,0.7)`; ctx.fill();
      n.x+=n.vx; n.y+=n.vy;
      if(n.x<0||n.x>W)n.vx*=-1; if(n.y<0||n.y>H)n.vy*=-1;
    }
    for (const p of particles) {
      ctx.beginPath(); ctx.arc(p.x,p.y,p.r,0,Math.PI*2);
      ctx.fillStyle=`hsla(${p.hue},100%,70%,${p.alpha})`; ctx.fill();
      p.x+=p.vx; p.y+=p.vy;
      if(p.x<0)p.x=W; if(p.x>W)p.x=0; if(p.y<0)p.y=H; if(p.y>H)p.y=0;
    }
    requestAnimationFrame(draw);
  }
  window.addEventListener('resize', () => { W=canvas.width=window.innerWidth; H=canvas.height=window.innerHeight; });
  draw();
})();

// ─── WebSocket + Polling ────────────────────────────────────────────────────

function connectWS() {
  if (ws) { try { ws.close(); } catch(e) {} ws = null; }
  setWsStatus('connecting');
  apiFetch(API_BASE + '/api/info')
    .then(r => r.text())
    .then(text => {
      try {
        const info = JSON.parse(text);
        log('Servidor detectado: ' + info.n_qubits + ' qubits', 'info');
      } catch(e) {
        log('Servidor responde (formato inesperado, continuando...)', 'info');
      }
      tryWebSocket();
    })
    .catch(() => {
      setWsStatus('error');
      log('Servidor no disponible en ' + API_BASE, 'err');
      scheduleReconnect();
    });
}

function tryWebSocket() {
  // Pasar JWT token como query param para autenticar el WebSocket
  const wsUrlWithToken = _authToken ? WS_URL + '?token=' + encodeURIComponent(_authToken) : WS_URL;
  try { ws = new WebSocket(wsUrlWithToken); } catch(e) {
    log('Usando polling HTTP', 'info'); startPolling(); return;
  }
  const t = setTimeout(() => {
    if (ws && ws.readyState !== WebSocket.OPEN) {
      ws.close(); ws = null; startPolling();
    }
  }, 3000);
  ws.onopen = () => {
    clearTimeout(t); pollingMode = false;
    if (pollingInterval) { clearInterval(pollingInterval); pollingInterval = null; }
    setWsStatus('connected');
    log('WebSocket conectado', 'info');
    if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
    ws.send(JSON.stringify({ cmd: 'get_state' }));
  };
  ws.onmessage = (e) => { try { handleWsMessage(JSON.parse(e.data)); } catch(err) {} };
  ws.onclose = () => { clearTimeout(t); if (!pollingMode) { setWsStatus('error'); scheduleReconnect(); } };
  ws.onerror = () => { clearTimeout(t); if (!pollingMode) { ws=null; startPolling(); } };
}

function startPolling() {
  pollingMode = true; setWsStatus('connected');
  log('Modo polling HTTP activo', 'info');
  if (pollingInterval) clearInterval(pollingInterval);
  pollingInterval = setInterval(async () => {
    try {
      state = await apiFetch(API_BASE + '/api/state').then(r => r.json());
      renderAll();
    } catch(e) {
      setWsStatus('error'); clearInterval(pollingInterval);
      pollingInterval = null; pollingMode = false; scheduleReconnect();
    }
  }, 900);
}

function scheduleReconnect() {
  if (!wsReconnectTimer) wsReconnectTimer = setTimeout(() => { wsReconnectTimer=null; connectWS(); }, 3000);
}

function setWsStatus(s) {
  const dot = document.getElementById('ws-dot');
  const label = document.getElementById('ws-label');
  dot.className = 'status-dot';
  if (s==='connected') { dot.classList.add('connected'); label.textContent='Conectado'; }
  else if (s==='connecting') { label.textContent='Conectando...'; }
  else { dot.classList.add('error'); label.textContent='Desconectado'; }
}

function handleWsMessage(msg) {
  // Auth error desde el servidor — sesion expirada
  if (msg.type==='auth_error') {
    showLoginOverlay('Sesión expirada o token inválido. Inicia sesión de nuevo.');
    return;
  }
  if (msg.type==='state_update') { state=msg.data; renderAll(); }
  else if (msg.type==='measured') {
    log(`Q${msg.data.qubit} medido → |${msg.data.result}⟩`, 'ok');
  }
  else if (msg.type==='measured_all') {
    showMeasurementResult(msg.data.results);
    const r=Object.entries(msg.data.results).map(([q,v])=>`q${q}:|${v}⟩`).join('  ');
    log('⊗ Medición completa: '+r,'ok');
  }
  else if (msg.type==='gate_applied') log(`Puerta ${msg.data.gate} → [${msg.data.qubits.join(',')}]`,'info');
  else if (msg.type==='algorithm_run') {
    showAlgorithmResult(msg.data.result, msg.data.name, msg.data.state);
    const r=msg.data.result;
    log(`▶ ${r.algorithm||msg.data.name}`,'algo');
    if(r.description) log('  '+r.description,'algo');
  }
  else if (msg.type==='enterprise_run') {
    showEnterpriseResult(msg.data.result);
    const r=msg.data.result||{};
    log(`${r.solution||msg.data.name} [${r.industry||''}]`,'algo');
  }
  else if (msg.type==='sample_result') { renderSampleHistogram(msg.data); }
  else if (msg.type==='noise_set') {
    const pct=(msg.data.level*100).toFixed(1);
    log(`Ruido del dispositivo → ${pct}%`, msg.data.level>0?'algo':'info');
  }
  else if (msg.type==='qubit_added') log(`Qubit añadido → total: ${msg.data.n_qubits}`,'ok');
  else if (msg.type==='qubit_removed') log(`Qubit eliminado → total: ${msg.data.n_qubits}`,'ok');
  else if (msg.type==='reset') { log('Reset → |0...0⟩','info'); selectedQubits=[]; }
  else if (msg.type==='error') log('Error: '+msg.data.message,'err');
}

// ─── Measurement Result Modal ──────────────────────────────────────────────

function showMeasurementResult(results) {
  const body = document.getElementById('measure-result-body');
  const entries = Object.entries(results).sort((a,b)=>parseInt(a[0])-parseInt(b[0]));
  const bitstring = entries.map(([,v])=>v).join('');
  const ones = entries.filter(([,v])=>v===1).length;
  const zeros = entries.length - ones;
  const decVal = parseInt(bitstring, 2);

  let html = `<div class="measure-bitstring-wrap">
    <div class="measure-bitstring">`;
  entries.forEach(([q, v], i) => {
    html += `<div class="measure-bit bit-${v}" style="animation-delay:${i*40}ms">
      <span class="measure-bit-label">q${q}</span>
      <span class="measure-bit-value">|${v}⟩</span>
    </div>`;
  });
  html += `</div>
    <div style="font-family:var(--font-mono);font-size:12px;color:var(--text-dim)">
      |${bitstring}⟩  →  decimal <span style="color:var(--accent)">${decVal}</span>
    </div>
  </div>
  <div class="measure-summary">
    <div class="measure-stat"><div class="measure-stat-label">Qubits medidos</div><div class="measure-stat-value">${entries.length}</div></div>
    <div class="measure-stat"><div class="measure-stat-label">Estados |0⟩</div><div class="measure-stat-value">${zeros}</div></div>
    <div class="measure-stat"><div class="measure-stat-label">Estados |1⟩</div><div class="measure-stat-value bit-1-color">${ones}</div></div>
    <div class="measure-stat"><div class="measure-stat-label">Valor decimal</div><div class="measure-stat-value">${decVal}</div></div>
    <div class="measure-stat"><div class="measure-stat-label">Valor hex</div><div class="measure-stat-value" style="color:var(--accent4)">0x${decVal.toString(16).toUpperCase()}</div></div>
  </div>`;

  body.innerHTML = html;
  document.getElementById('measure-result-overlay').classList.remove('hidden');
}

document.getElementById('measure-result-close').addEventListener('click', () => {
  document.getElementById('measure-result-overlay').classList.add('hidden');
});
document.getElementById('measure-result-overlay').addEventListener('click', e => {
  if(e.target===e.currentTarget) e.currentTarget.classList.add('hidden');
});

// ─── Algorithm Result Modal ────────────────────────────────────────────────

function showAlgorithmResult(r, algoName, stateData) {
  if(!r || r.error) { log('Error algoritmo: '+(r&&r.error||'desconocido'),'err'); return; }

  const ALGO_ICONS = {
    bell_state:'Φ⁺', ghz:'GHZ', qft:'QFT', grover:'⊗G',
    quantum_teleportation:'⟳ψ', bernstein_vazirani:'BV', deutsch:'D-J', random:'∞',
    shor:'℘', simon:'Σs', phase_estimation:'Φe', swap_test:'|⟩⟨|', w_state:'W'
  };
  const icon = ALGO_ICONS[algoName] || 'Ω';

  document.getElementById('algo-result-icon').textContent = icon;
  document.getElementById('algo-result-heading').textContent = r.algorithm || algoName;
  document.getElementById('algo-result-subheading').textContent = r.description || '';

  const body = document.getElementById('algo-result-body');
  let html = '';

  // ── Circuit ──
  if(r.circuit) {
    html += `<div class="algo-result-section">
      <div class="algo-result-label">Circuito ejecutado</div>
      <div class="algo-result-circuit">${escHtml(r.circuit)}</div>
    </div>`;
  }

  // ── Key stats grid ──
  const stats = [];
  if(r.iterations !== undefined)  stats.push({label:'Iteraciones', val:r.iterations, cls:'gold'});
  if(r.speedup)                   stats.push({label:'Ventaja cuántica', val:r.speedup, cls:'green'});
  if(r.classical_queries !== undefined) stats.push({label:'Consultas clásicas', val:r.classical_queries});
  if(r.quantum_queries !== undefined)   stats.push({label:'Consultas cuánticas', val:r.quantum_queries, cls:'purple'});
  if(r.complexity)                stats.push({label:'Complejidad', val:r.complexity, cls:''});
  if(r.entanglement)              stats.push({label:'Entrelazamiento', val:r.entanglement, cls:'green'});
  if(r.entropy)                   stats.push({label:'Entropía', val:r.entropy, cls:'purple'});
  if(r.states !== undefined)      stats.push({label:'Estados en superposición', val:r.states.toLocaleString(), cls:'gold'});
  if(stats.length > 0) {
    html += `<div class="algo-result-section"><div class="algo-result-stats">`;
    stats.forEach(s => {
      html += `<div class="algo-stat">
        <div class="algo-stat-label">${s.label}</div>
        <div class="algo-stat-value ${s.cls||''}">${escHtml(String(s.val))}</div>
      </div>`;
    });
    html += `</div></div>`;
  }

  // ── Algorithm-specific results ──
  if(r.found !== undefined || r.secret !== undefined) {
    // Bernstein-Vazirani
    const match = r.match;
    html += `<div class="algo-result-section">
      <div class="algo-result-label">Cadena secreta oculta</div>
      <div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">
        <div><div class="algo-result-label" style="margin-bottom:2px">Secreto</div>
          <div class="algo-result-value highlight">${escHtml(r.secret||'?')}</div></div>
        <div style="font-size:20px;color:var(--text-muted)">→</div>
        <div><div class="algo-result-label" style="margin-bottom:2px">Encontrado en 1 consulta</div>
          <div class="algo-result-value highlight" style="color:${match?'var(--accent3)':'var(--danger)'}">${escHtml(r.found||'?')}</div></div>
        <div style="font-family:var(--font-mono);font-size:18px;color:${match?'var(--accent3)':'var(--danger)'}">${match?'✓ CORRECTO':'✗ ERROR'}</div>
      </div>
    </div>`;
  }

  if(r.result !== undefined && algoName === 'deutsch') {
    // Deutsch
    const isBal = r.result === 'balanceada';
    html += `<div class="algo-result-section">
      <div class="algo-result-label">Veredicto</div>
      <div style="font-size:28px;font-weight:700;color:${isBal?'var(--accent2)':'var(--accent)'};font-family:var(--font-mono)">
        f(x) es ${r.result.toUpperCase()}
      </div>
      <div style="color:var(--text-dim);font-size:12px;margin-top:4px">${r.interpretation||''}</div>
    </div>`;
  }

  if(r.target !== undefined) {
    // Grover
    html += `<div class="algo-result-section">
      <div class="algo-result-label">Estado objetivo encontrado</div>
      <div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">
        <div><div class="algo-result-label" style="margin-bottom:2px">Binario</div>
          <div class="algo-result-value highlight">|${escHtml(r.target)}⟩</div></div>
        <div><div class="algo-result-label" style="margin-bottom:2px">Decimal</div>
          <div class="algo-result-value highlight">${r.target_decimal}</div></div>
        <div><div class="algo-result-label" style="margin-bottom:2px">Prob. tras búsqueda</div>
          <div class="algo-result-value highlight" style="color:var(--accent3)">${Math.round((r.target_probability||0)*100)}%</div></div>
      </div>
    </div>`;
  }

  if(r.m0 !== undefined) {
    // Teleportation
    const corr = r.correction || 'Ninguna';
    html += `<div class="algo-result-section">
      <div class="algo-result-label">Medición del canal Bell</div>
      <div style="display:flex;gap:20px;flex-wrap:wrap;margin-bottom:8px">
        <div><div class="algo-result-label" style="margin-bottom:2px">m0 (Alice)</div>
          <div class="algo-result-value highlight">|${r.m0}⟩</div></div>
        <div><div class="algo-result-label" style="margin-bottom:2px">m1 (Alice)</div>
          <div class="algo-result-value highlight">|${r.m1}⟩</div></div>
        <div><div class="algo-result-label" style="margin-bottom:2px">Corrección aplicada a q2</div>
          <div class="algo-result-value" style="color:var(--accent4)">${escHtml(corr)}</div></div>
      </div>`;
    if(r.bloch_original && r.bloch_teleported) {
      html += `<div class="algo-result-label" style="margin-top:8px">Esfera de Bloch — comparación</div>
      <table class="bloch-table">
        <tr><th>Eje</th><th>Estado original (q0)</th><th>Estado teleportado (q2)</th></tr>
        <tr><td>X</td><td>${r.bloch_original.x.toFixed(4)}</td><td>${r.bloch_teleported.x.toFixed(4)}</td></tr>
        <tr><td>Y</td><td>${r.bloch_original.y.toFixed(4)}</td><td>${r.bloch_teleported.y.toFixed(4)}</td></tr>
        <tr><td>Z</td><td>${r.bloch_original.z.toFixed(4)}</td><td>${r.bloch_teleported.z.toFixed(4)}</td></tr>
      </table>`;
    }
    html += `</div>`;
  }

  if(r.random_sample !== undefined) {
    // Random
    html += `<div class="algo-result-section">
      <div class="algo-result-label">Muestra aleatoria cuántica</div>
      <div class="algo-result-circuit" style="font-size:18px;text-align:center;padding:14px">${escHtml(r.random_sample)}</div>
      <div style="color:var(--text-dim);font-size:11px;margin-top:6px">${r.note||''}</div>
    </div>`;
  }

  // ── Shor result ──
  if(r.N !== undefined) {
    const success = r.success;
    const factors = r.factors_found;
    html += `<div class="algo-result-section">
      <div class="algo-result-label">Factorización de N=${r.N}</div>
      <div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin-bottom:8px">
        <div><div class="algo-result-label" style="margin-bottom:2px">N</div>
          <div class="algo-result-value highlight">${r.N}</div></div>
        <div style="font-size:22px;color:var(--text-muted)">=</div>
        ${success ? `<div><div class="algo-result-label" style="margin-bottom:2px">Factor p</div>
          <div class="algo-result-value highlight" style="color:var(--accent3)">${factors[0]}</div></div>
        <div style="font-size:22px;color:var(--text-muted)">×</div>
        <div><div class="algo-result-label" style="margin-bottom:2px">Factor q</div>
          <div class="algo-result-value highlight" style="color:var(--accent3)">${factors[1]}</div></div>` : '<div style="color:var(--danger)">No encontrado</div>'}
      </div>
      <div style="font-size:11px;color:var(--text-dim);font-family:var(--font-mono)">
        Base a=${r.a} | Orden r=${r.order_r} | ${r.n_count_qubits} qubits cuánticos
      </div>
    </div>`;
  }

  // ── Simon result ──
  if(r.hidden_s !== undefined) {
    const orth = r.orthogonal;
    html += `<div class="algo-result-section">
      <div class="algo-result-label">Cadena oculta s encontrada</div>
      <div style="display:flex;gap:20px;align-items:center;flex-wrap:wrap">
        <div><div class="algo-result-label" style="margin-bottom:2px">Cadena s (oculta)</div>
          <div class="algo-result-value highlight">${escHtml(r.hidden_s)}</div></div>
        <div><div class="algo-result-label" style="margin-bottom:2px">Vector y medido</div>
          <div class="algo-result-value highlight" style="color:var(--accent2)">${escHtml(r.y_measured)}</div></div>
        <div style="font-size:14px;font-family:var(--font-mono);color:${orth?'var(--accent3)':'var(--danger)'};">
          y⋅s = ${orth?'0 ✓':'1 ✗'}
        </div>
      </div>
    </div>`;
  }

  // ── Phase Estimation result ──
  if(r.true_phase !== undefined) {
    const err = r.error;
    const errColor = err < 0.02 ? 'var(--accent3)' : err < 0.1 ? 'var(--accent4)' : 'var(--danger)';
    html += `<div class="algo-result-section">
      <div class="algo-result-label">Estimación de fase</div>
      <div style="display:flex;gap:20px;align-items:center;flex-wrap:wrap">
        <div><div class="algo-result-label" style="margin-bottom:2px">Fase real ϕ</div>
          <div class="algo-result-value highlight">${r.true_phase.toFixed(6)}</div></div>
        <div><div class="algo-result-label" style="margin-bottom:2px">Fase estimada</div>
          <div class="algo-result-value highlight" style="color:var(--accent3)">${r.estimated_phase.toFixed(6)}</div></div>
        <div><div class="algo-result-label" style="margin-bottom:2px">Error</div>
          <div class="algo-result-value" style="color:${errColor}">${r.error.toFixed(6)}</div></div>
        <div><div class="algo-result-label" style="margin-bottom:2px">Binario</div>
          <div class="algo-result-value" style="color:var(--accent2)">${escHtml(r.measured_binary)}</div></div>
      </div>
    </div>`;
  }

  // ── SWAP Test result ──
  if(r.overlap_squared !== undefined) {
    const ol = r.overlap_squared;
    const identical = r.states_identical;
    html += `<div class="algo-result-section">
      <div class="algo-result-label">Similitud entre estados</div>
      <div style="display:flex;gap:20px;align-items:center;flex-wrap:wrap">
        <div><div class="algo-result-label" style="margin-bottom:2px">θ_A</div>
          <div class="algo-result-value">${r.theta_A.toFixed(4)} rad</div></div>
        <div><div class="algo-result-label" style="margin-bottom:2px">θ_B</div>
          <div class="algo-result-value">${r.theta_B.toFixed(4)} rad</div></div>
        <div><div class="algo-result-label" style="margin-bottom:2px">|&lt;A|B&gt;|²</div>
          <div class="algo-result-value highlight" style="color:${ol>0.8?'var(--accent3)':ol>0.3?'var(--accent4)':'var(--accent2)'}">${(ol*100).toFixed(1)}%</div></div>
        <div style="font-size:13px;font-family:var(--font-mono);color:${identical?'var(--accent3)':'var(--text-dim)'}">
          ${identical?'ESTADOS IDÉNTICOS':'Similitud parcial'}
        </div>
      </div>
    </div>`;
  }

  // ── Top states (state vector) ──
  const topStates = r.top_states || (stateData && stateData.statevector && stateData.statevector.slice(0,8));
  if(topStates && topStates.length > 0) {
    html += `<div class="algo-result-section">
      <div class="algo-result-label">Estados con mayor probabilidad</div>
      <div class="top-states-list">`;
    const maxProb = Math.max(...topStates.map(s=>s.probability||s.prob||0));
    topStates.slice(0,8).forEach(s => {
      const prob = s.probability ?? s.prob ?? 0;
      const pct = maxProb > 0 ? (prob / maxProb * 100) : 0;
      const dispPct = Math.round(prob * 100);
      const color = prob > 0.4 ? 'linear-gradient(90deg,var(--accent2),var(--accent))' :
                    prob > 0.1 ? 'linear-gradient(90deg,var(--accent),var(--accent3))' :
                                 'linear-gradient(90deg,var(--border2),var(--border2))';
      html += `<div class="top-state-row">
        <div class="top-state-label">|${escHtml(s.state)}⟩</div>
        <div class="top-state-bar-wrap"><div class="top-state-bar" style="width:${pct}%;background:${color}"></div></div>
        <div class="top-state-prob">${(prob*100).toFixed(1)}%</div>
      </div>`;
    });
    html += `</div></div>`;
  }

  // ── Fidelity ──
  if(r.fidelity !== undefined) {
    const f = r.fidelity;
    const fPct = Math.round(f * 100);
    const fColor = f >= 0.95 ? 'linear-gradient(90deg,var(--accent3),var(--accent))' :
                   f >= 0.5  ? 'linear-gradient(90deg,var(--accent4),var(--accent))' :
                                'linear-gradient(90deg,var(--danger),var(--accent4))';
    const fText = f >= 0.99 ? 'Perfecto' : f >= 0.95 ? 'Excelente' : f >= 0.5 ? 'Parcial' : 'Bajo';
    html += `<div class="algo-result-section">
      <div class="algo-result-label">Fidelidad del resultado</div>
      <div class="fidelity-gauge">
        <div class="fidelity-bar-wrap"><div class="fidelity-bar" style="width:${fPct}%;background:${fColor}"></div></div>
        <div class="fidelity-label" style="color:${f>=0.95?'var(--accent3)':f>=0.5?'var(--accent4)':'var(--danger)'}">${fPct}%</div>
        <div style="font-size:11px;color:var(--text-dim)">${fText}</div>
      </div>
    </div>`;
  }

  body.innerHTML = html;
  document.getElementById('algo-result-overlay').classList.remove('hidden');
}

function escHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

document.getElementById('algo-result-close').addEventListener('click', () => {
  document.getElementById('algo-result-overlay').classList.add('hidden');
});
document.getElementById('algo-result-overlay').addEventListener('click', e => {
  if(e.target===e.currentTarget) e.currentTarget.classList.add('hidden');
});

function sendWS(data) {
  if (ws && ws.readyState===WebSocket.OPEN) { ws.send(JSON.stringify(data)); return true; }
  return false;
}

// ─── REST API ───────────────────────────────────────────────────────────────

async function api(method, path, body) {
  try {
    const opts = { method, headers: {'Content-Type':'application/json'} };
    if (body!==undefined) opts.body=JSON.stringify(body);
    const res = await apiFetch(API_BASE+path, opts);
    // Parseo seguro: si el servidor devuelve HTML/error, no explotar
    const text = await res.text();
    let data;
    try { data = JSON.parse(text); }
    catch(e) { throw new Error('Respuesta no-JSON del servidor (HTTP '+res.status+'): '+text.slice(0,80)); }
    if (!res.ok) throw new Error(data.detail||'Error HTTP '+res.status);
    return data;
  } catch(e) { log('API: '+e.message,'err'); throw e; }
}

// ─── Render ─────────────────────────────────────────────────────────────────

function renderAll() {
  renderHeader();
  renderQubitGrid();
  renderBlochGrid();
  renderProbChart();
  renderStatevector();
  renderEntanglement();
  renderCircuitDiagram();
  renderMetrics();
}

// ─── Quantum Metrics ──────────────────────────────────────────────────────────

function renderMetrics() {
  const grid = document.getElementById('metrics-grid');
  if (!grid) return;
  const m = state.metrics;
  if (!m) { grid.innerHTML = '<div style="font-size:9px;color:var(--text-muted)">—</div>'; return; }
  const entPct  = Math.round((m.avg_entanglement || 0) * 100);
  const shaMax  = m.max_shannon || (state.n_qubits || 1);
  const shaPct  = Math.round(((m.shannon_entropy || 0) / Math.max(shaMax, 1)) * 100);
  grid.innerHTML = `
    <div class="metric-card">
      <span class="metric-label">Entropía Shannon</span>
      <span class="metric-value">${(m.shannon_entropy ?? 0).toFixed(2)}<span style="font-size:9px;color:var(--text-muted)"> /${shaMax} bits</span></span>
      <div class="metric-bar"><div style="width:${shaPct}%;background:linear-gradient(90deg,var(--accent),var(--accent2))"></div></div>
    </div>
    <div class="metric-card">
      <span class="metric-label">Entrelazamiento</span>
      <span class="metric-value purple">${(m.avg_entanglement ?? 0).toFixed(3)}<span style="font-size:9px;color:var(--text-muted)"> ebit</span></span>
      <div class="metric-bar"><div style="width:${entPct}%;background:linear-gradient(90deg,var(--accent2),var(--accent))"></div></div>
    </div>
    <div class="metric-card">
      <span class="metric-label">Estados activos</span>
      <span class="metric-value green">${(m.superposition_states ?? 0).toLocaleString()}</span>
    </div>
    <div class="metric-card">
      <span class="metric-label">Part. ratio</span>
      <span class="metric-value gold">${(m.participation_ratio ?? 0).toFixed(1)}</span>
    </div>`;
  // Keep slider/value in sync with server-side noise
  const nv = document.getElementById('noise-value');
  const ns = document.getElementById('noise-slider');
  if (nv && ns && document.activeElement !== ns) {
    const pct = (m.noise ?? 0) * 100;
    nv.textContent = pct.toFixed(1) + '%';
    ns.value = pct;
  }
}

function renderHeader() {
  document.getElementById('qubit-count-header').textContent = state.n_qubits+' qubits';
  document.getElementById('qubit-count-badge').textContent = state.n_qubits;
  document.getElementById('norm-display').textContent = '‖ψ‖ = '+(state.norm||1).toFixed(5);
  document.getElementById('circuit-depth-display').textContent = 'depth: '+(state.circuit||[]).length;
  document.getElementById('bloch-count').textContent = state.n_qubits;
}

// ─── Qubit Grid ─────────────────────────────────────────────────────────────

function renderQubitGrid() {
  const grid = document.getElementById('qubit-grid');
  const qubits = state.qubit_states || [];
  const n = qubits.length;

  // Responsive column count: 2 cols for ≤4, 3 for ≤9, 4 for ≤16, 5 for ≤25, 6 for ≤36+
  const cols = n <= 4 ? 2 : n <= 9 ? 3 : n <= 16 ? 4 : n <= 25 ? 5 : 6;
  grid.style.setProperty('--qgrid-cols', cols);

  if (grid.children.length !== n) {
    grid.innerHTML = '';
    qubits.forEach((q,i) => {
      const cell = document.createElement('div');
      cell.className = 'qubit-cell'; cell.id = `qcell-${i}`;
      cell.innerHTML = `
        <span class="q-label">q${i}</span>
        <span class="q-state-label" id="qstate-${i}">|0⟩</span>
        <div class="q-prob-bar"><div class="q-prob-fill" id="qfill-${i}"></div></div>
        <span class="q-prob-txt" id="qprob-${i}"></span>`;
      cell.addEventListener('click', () => onQubitClick(i));
      cell.addEventListener('dblclick', () => measureSingle(i));
      cell.addEventListener('contextmenu', e => { e.preventDefault(); removeQubit(i); });
      grid.appendChild(cell);
    });
  } else {
    // Ensure enhanced inner elements exist (upgrade from older template)
    qubits.forEach((q,i) => {
      const cell = document.getElementById(`qcell-${i}`);
      if (cell && !document.getElementById(`qstate-${i}`)) {
        cell.innerHTML = `
          <span class="q-label">q${i}</span>
          <span class="q-state-label" id="qstate-${i}">|0⟩</span>
          <div class="q-prob-bar"><div class="q-prob-fill" id="qfill-${i}"></div></div>
          <span class="q-prob-txt" id="qprob-${i}"></span>`;
        cell.addEventListener('click', () => onQubitClick(i));
        cell.addEventListener('dblclick', () => measureSingle(i));
        cell.addEventListener('contextmenu', e => { e.preventDefault(); removeQubit(i); });
      }
    });
  }
  qubits.forEach((q,i) => {
    const cell = document.getElementById(`qcell-${i}`);
    if (!cell) return;
    const p1 = q.p1||0;
    const p0 = q.p0||0;
    cell.style.setProperty('--q-p1', p1*0.75);
    cell.style.setProperty('--q-color', `hsl(${190+p1*70},80%,60%)`);
    cell.classList.toggle('disabled', !q.enabled);
    cell.classList.toggle('measured', q.measured!==null && q.measured!==undefined);
    cell.classList.toggle('active', p1>0.01 && p1<0.99);
    cell.classList.toggle('selected-q', selectedQubits.includes(i));

    // Update state label and probability display
    const stateEl = document.getElementById(`qstate-${i}`);
    const fillEl  = document.getElementById(`qfill-${i}`);
    const probEl  = document.getElementById(`qprob-${i}`);
    if (stateEl) {
      if (q.measured !== null && q.measured !== undefined) {
        stateEl.textContent = `|${q.measured}⟩`;
        stateEl.style.color = q.measured === 1 ? 'var(--accent2)' : 'var(--accent)';
      } else if (p1 > 0.99) {
        stateEl.textContent = '|1⟩'; stateEl.style.color = 'var(--accent2)';
      } else if (p0 > 0.99) {
        stateEl.textContent = '|0⟩'; stateEl.style.color = 'var(--accent)';
      } else {
        stateEl.textContent = '|ψ⟩'; stateEl.style.color = 'var(--accent4)';
      }
    }
    if (fillEl) {
      fillEl.style.width = (p1 * 100) + '%';
      fillEl.style.background = p1 > 0.5
        ? `linear-gradient(90deg, var(--accent), var(--accent2))`
        : `linear-gradient(90deg, var(--accent3), var(--accent))`;
    }
    if (probEl) {
      probEl.textContent = p1 > 0.005 ? Math.round(p1*100)+'%' : '';
    }
  });
}

function onQubitClick(i) {
  const idx = selectedQubits.indexOf(i);
  if (idx>=0) selectedQubits.splice(idx,1); else selectedQubits.push(i);
  renderQubitGrid();
}

async function measureSingle(qubit) {
  log(`Midiendo Q${qubit}...`,'info');
  await api('POST',`/api/measure/${qubit}`);
}

async function removeQubit(qubit) {
  if (state.n_qubits<=1) { log('No se puede eliminar el único qubit','err'); return; }
  await api('POST',`/api/qubits/remove/${qubit}`);
}

// ─── Bloch Grid — ALL QUBITS ────────────────────────────────────────────────

function renderBlochGrid() {
  const grid = document.getElementById('bloch-grid');
  const qubits = state.qubit_states || [];

  if (grid.children.length !== qubits.length) {
    grid.innerHTML = '';
    qubits.forEach((q,i) => {
      const wrap = document.createElement('div');
      wrap.className = 'bloch-wrap'; wrap.id = `bloch-${i}`;
      wrap.innerHTML = `
        <span class="bloch-label">Q${i}</span>
        <svg class="bloch-sphere-svg" id="bsvg-${i}" viewBox="-1.34 -1.34 2.68 2.68" xmlns="http://www.w3.org/2000/svg"></svg>
        <span class="bloch-prob" id="bprob-${i}"></span>`;
      grid.appendChild(wrap);
    });
  } else if (grid.children.length < qubits.length) {
    for (let i = grid.children.length; i < qubits.length; i++) {
      const wrap = document.createElement('div');
      wrap.className = 'bloch-wrap'; wrap.id = `bloch-${i}`;
      wrap.innerHTML = `
        <span class="bloch-label">Q${i}</span>
        <svg class="bloch-sphere-svg" id="bsvg-${i}" viewBox="-1.34 -1.34 2.68 2.68" xmlns="http://www.w3.org/2000/svg"></svg>
        <span class="bloch-prob" id="bprob-${i}"></span>`;
      grid.appendChild(wrap);
    }
  } else {
    while (grid.children.length > qubits.length) grid.removeChild(grid.lastChild);
  }

  qubits.forEach((q,i) => drawBlochSphere(i, q));
  applyBlochSize();
}

/* ─── 3D Bloch Sphere Renderer ─────────────────────────────────────────────
 * Projects the 3D Bloch sphere using a simple oblique perspective:
 *   screen_x = bx * cos30° - by * cos30°   (X and Y go diagonally)
 *   screen_y = bz * -1 + (bx+by)*0.25       (Z maps to vertical, XY add foreshortening)
 * This gives a clear 3D feel without WebGL.
 */

function bloch3D(bx, by, bz) {
  // Isometric-style projection (X right-forward, Y left-forward, Z up)
  const sx = bx * 0.72 - by * 0.72 * 0.5;
  const sy = -bz * 0.82 + (bx + by) * 0.20;
  return { sx, sy };
}

function drawBlochSphere(idx, q) {
  const svg = document.getElementById(`bsvg-${idx}`);
  const prob = document.getElementById(`bprob-${idx}`);
  if (!svg) return;
  const b = q.bloch || { x: 0, y: 0, z: 1 };
  const p1 = q.p1 || 0;
  const measured = q.measured !== null && q.measured !== undefined;
  const col = measured ? '#fbbf24'
            : (p1 > 0.985 ? '#818cf8' : p1 < 0.015 ? '#38bdf8' : `hsl(${205 - p1 * 45},85%,64%)`);

  const P = (x, y, z) => { const p = bloch3D(x, y, z); return [p.sx, p.sy]; };
  const fmt = ([x, y]) => `${x.toFixed(3)},${y.toFixed(3)}`;
  const ringPts = (zc) => {
    const r = Math.sqrt(Math.max(0, 1 - zc * zc)); const pts = [];
    for (let a = 0; a <= 30; a++) { const t = a / 30 * 2 * Math.PI; pts.push(fmt(P(r * Math.cos(t), r * Math.sin(t), zc))); }
    return pts.join(' ');
  };
  const meridianPts = (plane) => {
    const pts = [];
    for (let a = 0; a <= 30; a++) { const t = a / 30 * 2 * Math.PI, c = Math.cos(t), s = Math.sin(t); pts.push(fmt(plane === 'xz' ? P(c, 0, s) : P(0, c, s))); }
    return pts.join(' ');
  };

  const az = P(0, 0, 1), azb = P(0, 0, -1), ax = P(1, 0, 0), axn = P(-1, 0, 0), ay = P(0, 1, 0), ayn = P(0, -1, 0);
  const sc = 0.92;
  const [tx, ty] = P(b.x * sc, b.y * sc, b.z * sc);
  const [px, py] = P(b.x * sc, b.y * sc, 0);
  const ang = Math.atan2(ty, tx), al = 0.16;
  const a1x = (tx - al * Math.cos(ang - 0.42)).toFixed(3), a1y = (ty - al * Math.sin(ang - 0.42)).toFixed(3);
  const a2x = (tx - al * Math.cos(ang + 0.42)).toFixed(3), a2y = (ty - al * Math.sin(ang + 0.42)).toFixed(3);
  const gid = `bg${idx}`;

  svg.innerHTML = `
    <defs>
      <radialGradient id="${gid}" cx="38%" cy="30%" r="75%">
        <stop offset="0%" stop-color="rgba(255,255,255,0.12)"/>
        <stop offset="45%" stop-color="rgba(56,189,248,0.05)"/>
        <stop offset="100%" stop-color="rgba(10,16,28,0.6)"/>
      </radialGradient>
    </defs>
    <ellipse cx="0" cy="1.08" rx="0.72" ry="0.11" fill="rgba(0,0,0,0.35)"/>
    <circle cx="0" cy="0" r="1" fill="url(#${gid})" stroke="rgba(148,163,184,0.30)" stroke-width="0.02"/>
    ${[-0.6, -0.3, 0.3, 0.6].map(z => `<polyline points="${ringPts(z)}" fill="none" stroke="rgba(148,163,184,0.10)" stroke-width="0.011"/>`).join('')}
    <polyline points="${ringPts(0)}" fill="none" stroke="rgba(56,189,248,0.32)" stroke-width="0.02" stroke-dasharray="0.05,0.04"/>
    <polyline points="${meridianPts('xz')}" fill="none" stroke="rgba(148,163,184,0.10)" stroke-width="0.011"/>
    <polyline points="${meridianPts('yz')}" fill="none" stroke="rgba(148,163,184,0.10)" stroke-width="0.011"/>
    <line x1="${axn[0].toFixed(3)}" y1="${axn[1].toFixed(3)}" x2="${ax[0].toFixed(3)}" y2="${ax[1].toFixed(3)}" stroke="rgba(248,113,113,0.40)" stroke-width="0.015"/>
    <line x1="${ayn[0].toFixed(3)}" y1="${ayn[1].toFixed(3)}" x2="${ay[0].toFixed(3)}" y2="${ay[1].toFixed(3)}" stroke="rgba(52,211,153,0.40)" stroke-width="0.015"/>
    <line x1="${azb[0].toFixed(3)}" y1="${azb[1].toFixed(3)}" x2="${az[0].toFixed(3)}" y2="${az[1].toFixed(3)}" stroke="rgba(56,189,248,0.45)" stroke-width="0.017"/>
    <text x="${(az[0] + 0.05).toFixed(3)}" y="${(az[1] - 0.04).toFixed(3)}" font-size="0.15" fill="rgba(56,189,248,0.85)" font-family="monospace" font-weight="bold">|0⟩</text>
    <text x="${(azb[0] + 0.05).toFixed(3)}" y="${(azb[1] + 0.2).toFixed(3)}" font-size="0.15" fill="rgba(129,140,248,0.85)" font-family="monospace" font-weight="bold">|1⟩</text>
    <text x="${(ax[0] + 0.05).toFixed(3)}" y="${(ax[1] + 0.06).toFixed(3)}" font-size="0.12" fill="rgba(248,113,113,0.7)" font-family="monospace">x</text>
    <text x="${(ay[0] + 0.05).toFixed(3)}" y="${(ay[1] + 0.06).toFixed(3)}" font-size="0.12" fill="rgba(52,211,153,0.7)" font-family="monospace">y</text>
    <circle cx="0" cy="0" r="0.03" fill="rgba(255,255,255,0.5)"/>
    <line x1="${tx.toFixed(3)}" y1="${ty.toFixed(3)}" x2="${px.toFixed(3)}" y2="${py.toFixed(3)}" stroke="${col}" stroke-width="0.013" stroke-dasharray="0.04,0.04" opacity="0.5"/>
    <line x1="0" y1="0" x2="${px.toFixed(3)}" y2="${py.toFixed(3)}" stroke="${col}" stroke-width="0.011" stroke-dasharray="0.03,0.04" opacity="0.32"/>
    <line x1="0" y1="0" x2="${tx.toFixed(3)}" y2="${ty.toFixed(3)}" stroke="${col}" stroke-width="0.11" opacity="0.16" stroke-linecap="round"/>
    <line x1="0" y1="0" x2="${tx.toFixed(3)}" y2="${ty.toFixed(3)}" stroke="${col}" stroke-width="0.045" stroke-linecap="round"/>
    <polygon points="${tx.toFixed(3)},${ty.toFixed(3)} ${a1x},${a1y} ${a2x},${a2y}" fill="${col}"/>
    <circle cx="${tx.toFixed(3)}" cy="${ty.toFixed(3)}" r="0.07" fill="${col}"/>
    <circle cx="${tx.toFixed(3)}" cy="${ty.toFixed(3)}" r="0.03" fill="rgba(255,255,255,0.9)"/>
  `;

  if (prob) {
    prob.textContent = measured ? `medido |${q.measured}⟩`
      : (p1 > 0.985 ? '|1⟩' : p1 < 0.015 ? '|0⟩' : `${(p1 * 100).toFixed(0)}% |1⟩`);
  }
}

// (legacy helper — no longer used, kept for compat)
function drawBlochVector(b, measured, p1) {
  const tipX = b.y * 0.82, tipY = -b.z * 0.82;
  const ang = Math.atan2(tipY, tipX);
  const ax = tipX - 0.13 * Math.cos(ang), ay = tipY - 0.13 * Math.sin(ang);
  const color = measured ? '#f59e0b' : `hsl(${190 + p1 * 70},80%,60%)`;
  return `<line x1="0" y1="0" x2="${tipX.toFixed(3)}" y2="${tipY.toFixed(3)}" stroke="${color}" stroke-width="0.05" stroke-linecap="round"/>
    <circle cx="${tipX.toFixed(3)}" cy="${tipY.toFixed(3)}" r="0.07" fill="${color}" opacity="0.9"/>`;
}

let blochSize = 138;
function applyBlochSize() {
  const g = document.getElementById('bloch-grid');
  if (g) g.style.setProperty('--bloch-size', blochSize + 'px');
}
document.getElementById('bloch-size-up').addEventListener('click', () => { blochSize = Math.min(blochSize + 22, 240); applyBlochSize(); });
document.getElementById('bloch-size-down').addEventListener('click', () => { blochSize = Math.max(blochSize - 22, 84); applyBlochSize(); });

// ─── Probability Chart ───────────────────────────────────────────────────────

function renderProbChart() {
  const sv = (state.statevector||[]).slice(0,32);
  if (!sv.length) return;
  const labels = sv.map(s=>`|${s.state.slice(-8)}⟩`);
  const probs = sv.map(s=>s.probability*100);
  const colors = sv.map(s=>`hsla(${190+s.probability*70},80%,60%,0.8)`);
  if (!probChart) {
    const ctx = document.getElementById('prob-chart').getContext('2d');
    probChart = new Chart(ctx, {
      type:'bar', data:{labels,datasets:[{data:probs,backgroundColor:colors,borderColor:colors.map(c=>c.replace('0.8','1')),borderWidth:1,borderRadius:2}]},
      options:{responsive:true,maintainAspectRatio:false,animation:{duration:180},
        plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>`P = ${ctx.raw.toFixed(2)}%`},backgroundColor:'rgba(10,22,40,0.95)',borderColor:'rgba(0,200,255,0.3)',borderWidth:1,titleColor:'#00c8ff',bodyColor:'#94a3b8'}},
        scales:{
          x:{grid:{color:'rgba(0,200,255,0.04)'},ticks:{color:'#64748b',font:{family:'JetBrains Mono',size:8},maxRotation:45}},
          y:{grid:{color:'rgba(0,200,255,0.04)'},ticks:{color:'#64748b',font:{family:'JetBrains Mono',size:9},callback:v=>v.toFixed(1)+'%'},beginAtZero:true,max:100}
        }
      }
    });
  } else {
    probChart.data.labels=labels;
    probChart.data.datasets[0].data=probs;
    probChart.data.datasets[0].backgroundColor=colors;
    probChart.update('none');
  }
}

// ─── State Vector ────────────────────────────────────────────────────────────

function renderStatevector() {
  const list = document.getElementById('statevector-list');
  const sv = (state.statevector||[]).filter(s=>!stateFilter||s.state.includes(stateFilter)).slice(0,32);
  list.innerHTML = sv.map(s=>`
    <div class="sv-row">
      <span class="sv-state">${s.state.slice(-8)}</span>
      <div class="sv-bar-wrap"><div class="sv-bar" style="width:${Math.max(s.probability*130,1)}px"></div></div>
      <span class="sv-prob">${(s.probability*100).toFixed(2)}%</span>
    </div>`).join('');
}

// ─── Entanglement ────────────────────────────────────────────────────────────

function entColor(v) {
  // v in [0,1]: dark → cyan (#38bdf8) → indigo (#818cf8)
  const lerp = (a, b, t) => Math.round(a + (b - a) * t);
  if (v < 0.5) {
    const t = v / 0.5;
    return `rgb(${lerp(10,56,t)},${lerp(31,189,t)},${lerp(51,248,t)})`;
  }
  const t = (v - 0.5) / 0.5;
  return `rgb(${lerp(56,129,t)},${lerp(189,140,t)},${lerp(248,248,t)})`;
}

function renderEntanglement() {
  const canvas = document.getElementById('entanglement-canvas');
  const ctx = canvas.getContext('2d');
  const emap = state.entanglement || []; const n = emap.length;
  const note = document.getElementById('ent-legend-note');
  const W = canvas.width, H = canvas.height;
  ctx.fillStyle = '#0a0f1c'; ctx.fillRect(0, 0, W, H);
  if (!n) { if (note) note.textContent = '—'; return; }
  const pad = 16;                         // espacio para etiquetas
  const grid = W - pad, cell = grid / n;
  let maxV = 0, maxPair = null;
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      const x = pad + j * cell, y = i * cell;
      if (i === j) {
        ctx.fillStyle = 'rgba(148,163,184,0.10)';
      } else {
        const v = emap[i][j] || 0;
        ctx.fillStyle = entColor(v);
        if (i < j && v > maxV) { maxV = v; maxPair = [i, j]; }
      }
      ctx.fillRect(x + 1, y + 1, cell - 2, cell - 2);
    }
  }
  // etiquetas de ejes
  ctx.fillStyle = 'rgba(230,237,246,0.45)';
  ctx.font = `${Math.max(7, Math.min(cell * 0.42, 10))}px JetBrains Mono`;
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  for (let i = 0; i < n; i++) {
    ctx.fillText(i, pad + i * cell + cell / 2, H - 7);   // eje X (abajo)
    ctx.fillText(i, 7, i * cell + cell / 2);             // eje Y (izquierda)
  }
  if (note) {
    note.textContent = maxV > 0.02 && maxPair
      ? `Máximo: q${maxPair[0]}–q${maxPair[1]} (${maxV.toFixed(2)})`
      : 'Sin correlaciones (estado producto)';
  }
}

// ─── Circuit ─────────────────────────────────────────────────────────────────

function renderCircuitDiagram() {
  const host = document.getElementById('circuit-diagram');
  if (!host) return;
  const n = state.n_qubits || 0;
  const allOps = (state.circuit || []);
  const ops = allOps.slice(-30);
  if (!n) { host.innerHTML = ''; return; }
  if (!ops.length) {
    host.innerHTML = `<div class="circ-empty">Sin operaciones todavía. Aplica una puerta o ejecuta un algoritmo para ver el circuito.</div>`;
    return;
  }
  const rowH = 30, colW = 38, padL = 36, padT = 12, padR = 14;
  const cols = ops.length;
  const W = padL + cols * colW + padR;
  const H = padT * 2 + n * rowH;
  const yOf = q => padT + q * rowH + rowH / 2;
  const NS = 'http://www.w3.org/2000/svg';
  let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" class="circ-svg" xmlns="${NS}">`;

  // wires + labels
  for (let q = 0; q < n; q++) {
    const y = yOf(q);
    s += `<text x="8" y="${y + 3.5}" class="circ-wlabel">q${q}</text>`;
    s += `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" class="circ-wire"/>`;
  }

  const dot = (x, y, c) => `<circle cx="${x}" cy="${y}" r="4.2" fill="${c}"/>`;
  const oplus = (x, y, c) => `<circle cx="${x}" cy="${y}" r="9" fill="none" stroke="${c}" stroke-width="1.6"/>
     <line x1="${x-9}" y1="${y}" x2="${x+9}" y2="${y}" stroke="${c}" stroke-width="1.6"/>
     <line x1="${x}" y1="${y-9}" x2="${x}" y2="${y+9}" stroke="${c}" stroke-width="1.6"/>`;
  const xmark = (x, y, c) => `<line x1="${x-6}" y1="${y-6}" x2="${x+6}" y2="${y+6}" stroke="${c}" stroke-width="1.8"/>
     <line x1="${x-6}" y1="${y+6}" x2="${x+6}" y2="${y-6}" stroke="${c}" stroke-width="1.8"/>`;
  const box = (x, y, label, c) => {
    const w = Math.max(22, 8 + label.length * 7.5);
    return `<rect x="${x - w/2}" y="${y - 12}" width="${w}" height="24" rx="5" fill="rgba(56,189,248,0.10)" stroke="${c}" stroke-width="1.3"/>
      <text x="${x}" y="${y + 4}" class="circ-glabel" fill="${c}">${escHtml(label)}</text>`;
  };
  const meter = (x, y, c) => `<rect x="${x-11}" y="${y-11}" width="22" height="22" rx="4" fill="rgba(251,191,36,0.10)" stroke="${c}" stroke-width="1.3"/>
     <path d="M ${x-6} ${y+4} A 6 6 0 0 1 ${x+6} ${y+4}" fill="none" stroke="${c}" stroke-width="1.3"/>
     <line x1="${x}" y1="${y+4}" x2="${x+5}" y2="${y-5}" stroke="${c}" stroke-width="1.3"/>`;

  const C_1Q = '#7dd3fc', C_CTRL = '#34d399', C_PARAM = '#fbbf24', C_MEAS = '#fbbf24', C_MULTI = '#c7d2fe';

  ops.forEach((op, ci) => {
    const x = padL + ci * colW + colW / 2;
    const g = (op.gate || '').toUpperCase();
    const qs = op.qubits || [];
    const ys = qs.map(yOf);
    const link = (c) => qs.length > 1
      ? `<line x1="${x}" y1="${Math.min(...ys)}" x2="${x}" y2="${Math.max(...ys)}" stroke="${c}" stroke-width="1.4" opacity="0.8"/>` : '';
    const plabel = (op.params && op.params.length) ? `(${op.params.map(p => (+p).toFixed(2)).join(',')})` : '';

    if (g === 'MEASURE') { s += meter(x, ys[0], C_MEAS); return; }
    if (g === 'CNOT' || g === 'CX') { s += link(C_CTRL) + dot(x, yOf(qs[0]), C_CTRL) + oplus(x, yOf(qs[1]), C_CTRL); return; }
    if (g === 'CZ') { s += link(C_CTRL) + dot(x, yOf(qs[0]), C_CTRL) + dot(x, yOf(qs[1]), C_CTRL); return; }
    if (g === 'SWAP' || g === 'ISWAP') { s += link(C_MULTI) + xmark(x, yOf(qs[0]), C_MULTI) + xmark(x, yOf(qs[1]), C_MULTI); return; }
    if (g === 'CCX' || g === 'TOFFOLI') { s += link(C_CTRL) + dot(x, yOf(qs[0]), C_CTRL) + dot(x, yOf(qs[1]), C_CTRL) + oplus(x, yOf(qs[2]), C_CTRL); return; }
    if (g === 'CSWAP') { s += link(C_MULTI) + dot(x, yOf(qs[0]), C_CTRL) + xmark(x, yOf(qs[1]), C_MULTI) + xmark(x, yOf(qs[2]), C_MULTI); return; }
    if (['CY','CH','CS','CT','CP','CRX','CRY','CRZ'].includes(g)) {
      s += link(C_CTRL) + dot(x, yOf(qs[0]), C_CTRL) + box(x, yOf(qs[1]), g === 'CP' ? 'P' : g.slice(1), C_PARAM); return;
    }
    if (['RX','RY','RZ','P','PHASE','U3','RXX','RYY','RZZ'].includes(g)) {
      s += link(C_PARAM);
      qs.forEach(q => { s += box(x, yOf(q), g, C_PARAM); });
      return;
    }
    // generic single-qubit gate(s)
    s += link(C_1Q);
    qs.forEach(q => { s += box(x, yOf(q), g, C_1Q); });
  });

  s += `</svg>`;
  host.innerHTML = s;
  host.scrollLeft = host.scrollWidth;
}

// ─── Gate Buttons ────────────────────────────────────────────────────────────

function renderGateButtons() {
  renderGateGroup('gate-grid-1q', gates.single_qubit||[]);
  renderGateGroup('gate-grid-2q', gates.two_qubit||[]);
  renderGateGroup('gate-grid-3q', gates.three_qubit||[]);
}

function renderGateGroup(containerId, list) {
  const c = document.getElementById(containerId); c.innerHTML='';
  list.forEach(g=>{
    const btn=document.createElement('button');
    btn.className=`gate-btn gate-${g.name}`;
    btn.textContent=g.name;
    btn.dataset.tooltip=`${g.label}: ${g.description}`;
    btn.addEventListener('click',()=>onGateClick(g));
    c.appendChild(btn);
  });
}

function getGateQubitCount(gateName) {
  if ((gates.single_qubit||[]).find(g=>g.name===gateName)) return 1;
  if ((gates.two_qubit||[]).find(g=>g.name===gateName)) return 2;
  if ((gates.three_qubit||[]).find(g=>g.name===gateName)) return 3;
  return 1;
}

function onGateClick(gateDef) {
  pendingGateDef = gateDef;
  showGateModal(gateDef, getGateQubitCount(gateDef.name));
}

function showGateModal(gateDef, qCount) {
  document.getElementById('modal-title').textContent = `Puerta ${gateDef.label} — ${gateDef.description}`;
  const nq = state.n_qubits||1;
  let html='';
  for(let i=0;i<qCount;i++){
    const label=qCount===1?'Qubit objetivo':i===0?'Qubit de control':i===1&&qCount===3?'Control 2':'Qubit objetivo';
    const pre=selectedQubits[i]!==undefined?selectedQubits[i]:i;
    html+=`<div class="modal-field"><label class="modal-label">${label}</label>
      <select class="modal-input" id="mq-${i}">${Array.from({length:nq},(_,j)=>`<option value="${j}" ${j===pre?'selected':''}>${j}</option>`).join('')}</select></div>`;
  }
  for(let p=0;p<(gateDef.params||0);p++){
    const pNames=['θ (theta, rad)','φ (phi, rad)','λ (lambda, rad)'];
    html+=`<div class="modal-field"><label class="modal-label">${pNames[p]||'Parámetro '+(p+1)}</label>
      <input type="number" class="modal-input" id="mp-${p}" value="1.5707963" step="0.001"/></div>`;
  }
  document.getElementById('modal-body').innerHTML=html;
  document.getElementById('modal-overlay').classList.remove('hidden');
}

document.getElementById('modal-cancel').addEventListener('click',()=>{
  document.getElementById('modal-overlay').classList.add('hidden'); pendingGateDef=null;
});

document.getElementById('modal-confirm').addEventListener('click', async()=>{
  if(!pendingGateDef)return;
  const qCount=getGateQubitCount(pendingGateDef.name);
  const qubits=[]; for(let i=0;i<qCount;i++) qubits.push(parseInt(document.getElementById(`mq-${i}`).value));
  const params=[]; for(let p=0;p<(pendingGateDef.params||0);p++) params.push(parseFloat(document.getElementById(`mp-${p}`).value));
  if(new Set(qubits).size!==qubits.length){log('Qubits duplicados en la puerta','err');return;}
  document.getElementById('modal-overlay').classList.add('hidden');
  try { await api('POST','/api/gate',{gate:pendingGateDef.name,qubits,params}); } catch(e){}
  pendingGateDef=null;
});

// ─── Algorithm Buttons + Info Panel ─────────────────────────────────────────

function renderAlgorithms() {
  const container = document.getElementById('algo-list');
  container.innerHTML = '';
  const icons = {
    bell_state:'Φ⁺', ghz:'GHZ', qft:'QFT', grover:'⊗G',
    quantum_teleportation:'⇌ψ', bernstein_vazirani:'BV', deutsch:'D-J', random:'∞',
    shor:'℘', simon:'Σs', phase_estimation:'Φe', swap_test:'|⟩⟨|', w_state:'W'
  };
  algorithms.forEach(algo=>{
    const btn=document.createElement('button');
    btn.className='algo-btn'; btn.id=`algo-btn-${algo.name}`;
    btn.innerHTML=`<span class="algo-icon">${icons[algo.name]||'⟨ψ|'}</span>
      <span class="algo-text"><span class="algo-name">${algo.label}</span><span class="algo-desc">${algo.description}</span></span>`;
    btn.addEventListener('click',()=>showAlgoInfo(algo, btn));
    container.appendChild(btn);
  });
}

function showAlgoInfo(algo, btn) {
  // Toggle off if same
  if (activeAlgoBtn===btn && !document.getElementById('algo-info-panel').classList.contains('hidden')) {
    document.getElementById('algo-info-panel').classList.add('hidden');
    btn.classList.remove('active'); activeAlgoBtn=null; pendingAlgo=null; return;
  }
  if(activeAlgoBtn) activeAlgoBtn.classList.remove('active');
  btn.classList.add('active'); activeAlgoBtn=btn; pendingAlgo=algo;

  const info = ALGO_INFO[algo.name] || {};
  const panel = document.getElementById('algo-info-panel');

  document.getElementById('algo-info-icon').textContent = info.icon||'⟨ψ|';
  document.getElementById('algo-info-name').textContent = algo.label;
  document.getElementById('algo-info-category').textContent = info.category||'Algoritmo Cuántico';
  document.getElementById('algo-info-what').innerHTML = info.what||'—';
  document.getElementById('algo-info-how').innerHTML = info.how||'—';
  document.getElementById('algo-info-circuit').textContent = info.circuit||'—';
  document.getElementById('algo-info-apps').innerHTML = (info.apps||[]).map(a=>`<span class="algo-tag">${a}</span>`).join('');
  document.getElementById('algo-info-complexity').innerHTML = (info.complexity||[]).map(c=>
    `<div class="algo-complexity-row"><span class="algo-complexity-label">${c.label}</span><span class="algo-complexity-val">${c.val}</span></div>`).join('');
  document.getElementById('algo-info-note').textContent = info.note||'';

  panel.classList.remove('hidden');
  // Scroll main area to top to show panel
  document.querySelector('.main-area').scrollTo({top:0, behavior:'smooth'});
}

document.getElementById('algo-info-close').addEventListener('click',()=>{
  document.getElementById('algo-info-panel').classList.add('hidden');
  if(activeAlgoBtn){activeAlgoBtn.classList.remove('active');activeAlgoBtn=null;}
  pendingAlgo=null;
});

document.getElementById('algo-info-run').addEventListener('click', async()=>{
  if(!pendingAlgo) return;
  await runAlgorithm(pendingAlgo);
  document.getElementById('algo-info-panel').classList.add('hidden');
  if(activeAlgoBtn){activeAlgoBtn.classList.remove('active');activeAlgoBtn=null;}
});

async function runAlgorithm(algo) {
  log(`▶ Ejecutando: ${algo.label}...`,'algo');
  try {
    const n = state.n_qubits;
    const params = {};

    // Each algorithm uses as many qubits as possible from the available system
    if(algo.name === 'ghz') {
      params.n = n;  // use ALL qubits
    }
    if(algo.name === 'w_state') {
      params.n = Math.min(n, 10);  // W state up to 10 qubits
    }
    if(algo.name === 'qft') {
      params.n = Math.min(n, 8);  // QFT up to 8 qubits (2^8=256 states, fast)
    }
    if(algo.name === 'grover') {
      params.n = Math.min(n, 6);  // Grover up to 6 qubits (2^6=64 states)
      // Random target each execution
      params.target = Math.floor(Math.random() * Math.pow(2, params.n));
    }
    if(algo.name === 'bernstein_vazirani') {
      params.n = Math.min(n - 1, 8);  // use n-1 input qubits, 1 ancilla
      // Random secret each execution
      params.secret = Array.from({length: params.n}, () => Math.random() < 0.5 ? '1' : '0').join('');
    }
    if(algo.name === 'deutsch') {
      // Random oracle each time
      const oracles = ['constant_0','constant_1','balanced_id','balanced_not'];
      params.oracle = oracles[Math.floor(Math.random() * oracles.length)];
    }
    if(algo.name === 'shor') {
      // Pick a random semiprime and use available qubits for counting register
      const semiprimes = [15, 21, 35, 33, 77];
      params.N = semiprimes[Math.floor(Math.random() * semiprimes.length)];
      params.n_count = Math.min(n, 6);
    }
    if(algo.name === 'simon') {
      params.n = Math.min(Math.floor(n / 2), 4);  // n input + n ancilla qubits
    }
    if(algo.name === 'phase_estimation') {
      params.n = Math.min(n - 1, 6);   // counting register qubits
      // Random canonical phase each time
      const phases = [0.25, 0.125, 0.375, 0.333, 0.167, 0.2, 0.0625];
      params.phase = phases[Math.floor(Math.random() * phases.length)];
    }
    // swap_test, bell_state, quantum_teleportation, random: no extra params needed

    const resp = await api('POST', '/api/algorithm', {name: algo.name, params});
    if(resp && resp.result && !resp.result.error) {
      // WS event handles it; this is the REST fallback
      setTimeout(() => {
        if(document.getElementById('algo-result-overlay').classList.contains('hidden')) {
          showAlgorithmResult(resp.result, algo.name, resp.state);
        }
      }, 150);
    }
  } catch(e) { log('Error al ejecutar: ' + e.message, 'err'); }
}

// ─── Enterprise Solutions ─────────────────────────────────────────────────────

let enterpriseSolutions = [];
let entChart = null;

function industryColor(industry) {
  const s = (industry || '').toLowerCase();
  if (s.includes('financ') || s.includes('finanz') || s.includes('contab')) return { c: '#34d399', glow: 'rgba(52,211,153,0.14)' };
  if (s.includes('logíst') || s.includes('logist') || s.includes('red')) return { c: '#38bdf8', glow: 'rgba(56,189,248,0.14)' };
  if (s.includes('ciber') || s.includes('segur')) return { c: '#fbbf24', glow: 'rgba(251,191,36,0.14)' };
  if (s.includes('quím') || s.includes('quim') || s.includes('farma')) return { c: '#a78bfa', glow: 'rgba(167,139,250,0.16)' };
  if (s.includes('verific') || s.includes('ia') || s.includes('riesgo')) return { c: '#818cf8', glow: 'rgba(129,140,248,0.16)' };
  if (s.includes('planif')) return { c: '#fbbf24', glow: 'rgba(251,191,36,0.14)' };
  if (s.includes('operac')) return { c: '#2dd4bf', glow: 'rgba(45,212,191,0.14)' };
  return { c: '#38bdf8', glow: 'rgba(56,189,248,0.14)' };
}

// Compact card for the left sidebar quick-access list
function renderEnterprise() {
  const list = document.getElementById('enterprise-list');
  if (!list) return;
  list.innerHTML = '';
  enterpriseSolutions.forEach(sol => {
    const col = industryColor(sol.industry);
    const card = document.createElement('div');
    card.className = 'ent-card';
    card.style.setProperty('--ind-color', col.c);
    card.innerHTML = `
      <div class="ent-card-icon">${escHtml(sol.icon || '·')}</div>
      <div class="ent-card-text">
        <span class="ent-card-industry">${escHtml(sol.industry)}</span>
        <span class="ent-card-name">${escHtml(sol.label)}</span>
        <span class="ent-card-desc">${escHtml(sol.description || '')}</span>
      </div>
      <span class="ent-card-go">›</span>`;
    card.addEventListener('click', () => openUsecaseForm(sol));
    list.appendChild(card);
  });
}

// ─── Use-case input forms (real user data) ────────────────────────────────────

const FORM_SCHEMAS = {
  portfolio: {
    intro: 'Introduce tus activos con su rendimiento esperado (%) y su riesgo/volatilidad (%). El ordenador cuántico evaluará todas las combinaciones y elegirá la cartera óptima.',
    fields: [
      { type: 'rows', key: 'assets', addLabel: '+ Añadir activo',
        columns: [{ key: 'name', label: 'Activo', type: 'text', w: '1.4fr' },
                  { key: 'ret', label: 'Rendim. %', type: 'number', w: '1fr' },
                  { key: 'vol', label: 'Riesgo %', type: 'number', w: '1fr' }],
        def: [{ name: 'BBVA', ret: 12, vol: 18 }, { name: 'Iberdrola', ret: 8, vol: 10 },
              { name: 'Santander', ret: 15, vol: 28 }, { name: 'Inditex', ret: 10, vol: 14 }] },
      { type: 'number', key: 'budget', label: 'Nº de activos a elegir', def: 2, min: 1, max: 8 },
      { type: 'number', key: 'risk', label: 'Aversión al riesgo (0-10)', def: 3, min: 0, max: 10, step: 0.5 },
    ]
  },
  knapsack: {
    intro: 'Lista tus opciones (proyectos, inversiones, productos) con su valor y su coste. Fija el presupuesto disponible y el ordenador cuántico elegirá el subconjunto de mayor valor que cabe.',
    fields: [
      { type: 'rows', key: 'items', addLabel: '+ Añadir opción',
        columns: [{ key: 'name', label: 'Opción', type: 'text', w: '1.6fr' },
                  { key: 'value', label: 'Valor', type: 'number', w: '1fr' },
                  { key: 'weight', label: 'Coste', type: 'number', w: '1fr' }],
        def: [{ name: 'Web corporativa', value: 50, weight: 20 }, { name: 'CRM', value: 80, weight: 40 },
              { name: 'App móvil', value: 70, weight: 30 }, { name: 'BI / Analítica', value: 40, weight: 10 }] },
      { type: 'number', key: 'capacity', label: 'Presupuesto / capacidad total', def: 50, min: 1 },
    ]
  },
  task_assignment: {
    intro: 'Introduce el coste (horas, € o esfuerzo) de cada equipo al realizar cada tarea. El ordenador cuántico encuentra la asignación que minimiza el coste total (un equipo por tarea).',
    fields: [
      { type: 'matrix', key: 'cost_matrix', size: 3,
        rowLabels: ['Equipo A', 'Equipo B', 'Equipo C'], colLabels: ['Tarea 1', 'Tarea 2', 'Tarea 3'],
        def: [[9, 2, 7], [6, 4, 3], [5, 8, 1]] },
    ]
  },
  maxcut: {
    intro: 'Define el número de nodos (almacenes, servidores, antenas…) y sus conexiones. El ordenador cuántico los divide en dos grupos maximizando los enlaces entre grupos.',
    fields: [
      { type: 'number', key: 'n', label: 'Nº de nodos', def: 5, min: 3, max: 9 },
      { type: 'list', key: 'edges', outKey: 'edges', mode: 'edges', wide: true,
        label: 'Conexiones (una por línea, formato  0-1)', def: '0-1\n1-2\n2-3\n3-4\n4-0\n0-2' },
    ]
  },
  grover_search: {
    intro: 'Pega tu lista de registros (uno por línea) e indica la posición a localizar. Grover la encuentra en √N pasos en lugar de N.',
    fields: [
      { type: 'list', key: 'items', mode: 'lines', wide: true, label: 'Registros (uno por línea)',
        def: 'Cliente_001\nCliente_002\nCliente_003\nCliente_004\nCliente_005\nCliente_006\nCliente_007\nCliente_008' },
      { type: 'number', key: 'target', label: 'Posición a buscar (0 = primero)', def: 3, min: 0 },
    ]
  },
  swap_similarity: {
    intro: 'Codifica dos perfiles como un valor 0-100 (p. ej. puntuación de riesgo, vector de características). El SWAP Test mide su similitud en una sola medición cuántica.',
    fields: [
      { type: 'text', key: 'label_a', label: 'Nombre del perfil A', def: 'Transacción' },
      { type: 'number', key: 'a', label: 'Valor perfil A (0-100)', def: 30, min: 0, max: 100 },
      { type: 'text', key: 'label_b', label: 'Nombre del perfil B', def: 'Patrón normal' },
      { type: 'number', key: 'b', label: 'Valor perfil B (0-100)', def: 35, min: 0, max: 100 },
    ]
  },
  bb84: {
    intro: 'Genera una clave secreta compartida sobre qubits. Activa el espía para comprobar cómo la física cuántica detecta cualquier intento de interceptación.',
    fields: [
      { type: 'number', key: 'n', label: 'Nº de qubits (longitud)', def: 24, min: 8, max: 64 },
      { type: 'checkbox', key: 'eve', label: 'Simular espía (Eve) interceptando el canal', def: true },
    ]
  },
  qrng: {
    intro: 'Genera una clave criptográfica de 256 bits a partir del colapso cuántico — aleatoriedad verdadera, imposible de reproducir por un ordenador clásico. Pulsa para generar.',
    fields: []
  },
  vqe_h2: {
    intro: 'Introduce la distancia de enlace entre los dos átomos de hidrógeno (en Ångström). El VQE calcula la energía del estado fundamental de la molécula minimizando ⟨ψ|H|ψ⟩.',
    fields: [
      { type: 'number', key: 'bond_length', label: 'Distancia de enlace H–H (Å)', def: 0.7414, min: 0.3, max: 2.5, step: 0.01 },
    ]
  },
};

function fieldHTML(f) {
  if (f.type === 'text' || f.type === 'number') {
    const attrs = `${f.min != null ? `min="${f.min}"` : ''} ${f.max != null ? `max="${f.max}"` : ''} ${f.step != null ? `step="${f.step}"` : ''}`;
    return `<div class="uf-field"><label>${escHtml(f.label)}</label>
      <input class="uf-input" data-key="${f.key}" type="${f.type}" value="${f.def}" ${attrs}></div>`;
  }
  if (f.type === 'checkbox') {
    return `<div class="uf-field uf-wide uf-check"><label><input type="checkbox" data-key="${f.key}" ${f.def ? 'checked' : ''}> ${escHtml(f.label)}</label></div>`;
  }
  if (f.type === 'list') {
    return `<div class="uf-field uf-wide"><label>${escHtml(f.label)}</label>
      <textarea class="uf-input uf-area" data-key="${f.key}" data-mode="${f.mode || 'lines'}" data-outkey="${f.outKey || f.key}" rows="6">${escHtml(f.def)}</textarea></div>`;
  }
  if (f.type === 'rows') {
    const cols = f.columns.map(c => c.w || '1fr').join(' ') + ' 28px';
    const head = `<div class="uf-rows-head" style="grid-template-columns:${cols}">` +
      f.columns.map(c => `<span>${escHtml(c.label)}</span>`).join('') + `<span></span></div>`;
    const rows = f.def.map(r => rowHTML(f, r)).join('');
    return `<div class="uf-field uf-wide"><label>Datos</label>
      <div class="uf-rows" data-key="${f.key}" data-cols="${escHtml(cols)}">${head}${rows}</div>
      <button type="button" class="uf-add" data-add="${f.key}">${escHtml(f.addLabel || '+ Añadir')}</button></div>`;
  }
  if (f.type === 'matrix') {
    const s = f.size;
    let html = `<div class="uf-field uf-wide"><label>Matriz de costes</label><div class="uf-matrix" data-key="${f.key}" data-size="${s}"><table><tr><th></th>`;
    for (let j = 0; j < s; j++) html += `<th><input class="uf-input uf-label-in" data-collabel="${j}" value="${escHtml(f.colLabels[j] || ('T' + j))}"></th>`;
    html += `</tr>`;
    for (let i = 0; i < s; i++) {
      html += `<tr><th><input class="uf-input uf-label-in" data-rowlabel="${i}" value="${escHtml(f.rowLabels[i] || ('E' + i))}"></th>`;
      for (let j = 0; j < s; j++) html += `<td><input class="uf-input" type="number" data-cell="${i}-${j}" value="${f.def[i][j]}"></td>`;
      html += `</tr>`;
    }
    return html + `</table></div></div>`;
  }
  return '';
}

function rowHTML(f, r) {
  const cols = f.columns.map(c => c.w || '1fr').join(' ') + ' 28px';
  return `<div class="uf-row" style="grid-template-columns:${cols}">` +
    f.columns.map(c => `<input class="uf-input" data-col="${c.key}" type="${c.type}" value="${escHtml(String(r[c.key] != null ? r[c.key] : ''))}">`).join('') +
    `<span class="uf-row-del" title="Eliminar">✕</span></div>`;
}

let selectedUsecase = null;

function openUsecaseForm(sol) {
  selectedUsecase = sol;
  const schema = FORM_SCHEMAS[sol.name] || { intro: sol.description, fields: [] };
  const col = industryColor(sol.industry);
  const form = document.getElementById('usecase-form');
  const modal = document.getElementById('usecase-form-modal');
  modal.style.setProperty('--ind-color', col.c);
  modal.style.setProperty('--ind-glow', col.glow);
  form.style.setProperty('--ind-color', col.c);
  form.innerHTML = `
    <div class="uf-head">
      <div class="uf-mono">${escHtml(sol.icon || '·')}</div>
      <div class="uf-head-titles">
        <div class="uf-head-industry">${escHtml(sol.industry)}</div>
        <div class="uf-head-name">${escHtml(sol.label)}</div>
      </div>
      <button class="btn btn-ghost btn-sm" id="uf-back">← Volver</button>
    </div>
    <div class="uf-intro">${escHtml(schema.intro || '')}</div>
    <div class="uf-grid">${schema.fields.map(fieldHTML).join('')}</div>
    <div class="uf-actions">
      <button class="uf-solve" id="uf-solve">Resolver con computación cuántica</button>
    </div>`;

  // wire repeatable-row add/remove
  form.querySelectorAll('[data-add]').forEach(btn => {
    btn.addEventListener('click', () => {
      const f = schema.fields.find(x => x.key === btn.dataset.add);
      const cont = form.querySelector(`.uf-rows[data-key="${f.key}"]`);
      const blank = {}; f.columns.forEach(c => blank[c.key] = c.type === 'number' ? 0 : '');
      cont.insertAdjacentHTML('beforeend', rowHTML(f, blank));
      wireRowDelete(cont);
    });
  });
  form.querySelectorAll('.uf-rows').forEach(wireRowDelete);

  document.getElementById('uf-back').addEventListener('click', closeUsecaseForm);
  document.getElementById('uf-solve').addEventListener('click', () => {
    const params = collectParams(schema, form);
    closeUsecaseForm();
    runEnterprise(sol, params);
  });

  document.getElementById('usecase-form-overlay').classList.remove('hidden');
}

function wireRowDelete(cont) {
  cont.querySelectorAll('.uf-row-del').forEach(el => {
    el.onclick = () => { if (cont.querySelectorAll('.uf-row').length > 1) el.parentElement.remove(); };
  });
}

function closeUsecaseForm() {
  document.getElementById('usecase-form-overlay').classList.add('hidden');
}
document.getElementById('usecase-form-overlay').addEventListener('click', e => { if (e.target === e.currentTarget) e.currentTarget.classList.add('hidden'); });

function collectParams(schema, root) {
  const p = {};
  schema.fields.forEach(f => {
    if (f.type === 'rows') {
      const cont = root.querySelector(`.uf-rows[data-key="${f.key}"]`);
      p[f.key] = [...cont.querySelectorAll('.uf-row')].map(r => {
        const o = {};
        f.columns.forEach(c => {
          const el = r.querySelector(`[data-col="${c.key}"]`);
          o[c.key] = c.type === 'number' ? (parseFloat(el.value) || 0) : el.value;
        });
        return o;
      });
    } else if (f.type === 'matrix') {
      const cont = root.querySelector(`.uf-matrix[data-key="${f.key}"]`);
      const s = parseInt(cont.dataset.size);
      const mat = [];
      for (let i = 0; i < s; i++) { const row = []; for (let j = 0; j < s; j++) row.push(parseFloat(cont.querySelector(`[data-cell="${i}-${j}"]`).value) || 0); mat.push(row); }
      p[f.key] = mat;
      p.workers = [...cont.querySelectorAll('[data-rowlabel]')].map(e => e.value);
      p.tasks = [...cont.querySelectorAll('[data-collabel]')].map(e => e.value);
    } else if (f.type === 'list') {
      const el = root.querySelector(`[data-key="${f.key}"]`);
      const lines = el.value.split('\n').map(s => s.trim()).filter(Boolean);
      if ((el.dataset.mode || 'lines') === 'edges') {
        p[el.dataset.outkey || 'edges'] = lines.map(l => l.split(/[-,\s]+/).map(x => parseInt(x)))
          .filter(a => a.length >= 2 && !isNaN(a[0]) && !isNaN(a[1])).map(a => [a[0], a[1]]);
      } else { p[f.key] = lines; }
    } else if (f.type === 'checkbox') {
      p[f.key] = root.querySelector(`[data-key="${f.key}"]`).checked;
    } else {
      const el = root.querySelector(`[data-key="${f.key}"]`);
      if (el) p[f.key] = f.type === 'number' ? parseFloat(el.value) : el.value;
    }
  });
  return p;
}

async function runEnterprise(sol, params = {}) {
  log(`Ejecutando caso de uso: ${sol.label} (${sol.industry})...`, 'algo');
  try {
    if (!sendWS({ cmd: 'enterprise', name: sol.name, params })) {
      const resp = await api('POST', '/api/enterprise', { name: sol.name, params });
      if (resp && resp.result) showEnterpriseResult(resp.result);
    } else {
      setTimeout(async () => {
        if (document.getElementById('enterprise-overlay').classList.contains('hidden')) {
          try { const resp = await api('POST', '/api/enterprise', { name: sol.name, params }); showEnterpriseResult(resp.result); } catch (e) {}
        }
      }, 500);
    }
  } catch (e) { log('Error en el caso de uso: ' + e.message, 'err'); }
}

function showEnterpriseResult(r) {
  if (!r || r.error) { log('Error: ' + (r && r.error || 'desconocido'), 'err'); return; }
  const col = industryColor(r.industry);
  const modal = document.querySelector('.modal-enterprise');
  modal.style.setProperty('--ind-color', col.c);
  modal.style.setProperty('--ind-glow', col.glow);

  document.getElementById('ent-icon').textContent = r.icon || '·';
  document.getElementById('ent-industry').textContent = r.industry || '';
  document.getElementById('ent-name').textContent = r.solution || '';

  let html = '';
  if (r.summary) html += `<div class="ent-summary">${escHtml(r.summary)}</div>`;

  if (r.kpis && r.kpis.length) {
    html += `<div class="ent-kpis">` + r.kpis.map(k => `
      <div class="ent-kpi">
        <span class="ent-kpi-label">${escHtml(k.label)}</span>
        <span class="ent-kpi-value">${escHtml(String(k.value))}</span>
      </div>`).join('') + `</div>`;
  }

  if (r.highlight) {
    html += `<div class="ent-highlight">
      <span class="ent-highlight-label">${escHtml(r.highlight.label)}</span>
      <span class="ent-highlight-value">${escHtml(String(r.highlight.value))}</span>
    </div>`;
  }

  const hasRows = r.rows && r.rows.length;
  const hasChart = r.chart && r.chart.values && r.chart.values.length;
  if (hasRows || hasChart) {
    html += `<div class="ent-cols ${hasRows && hasChart ? '' : 'single'}">`;
    if (hasRows) {
      html += `<div><div class="ent-section-title">Detalle</div><div class="ent-rows">` +
        r.rows.map(row => `<div class="ent-row">
          <span class="ent-row-label">${escHtml(row.label)}</span>
          ${row.tag ? `<span class="ent-row-tag">${escHtml(row.tag)}</span>` : ''}
          <span class="ent-row-value">${escHtml(String(row.value))}</span>
        </div>`).join('') + `</div></div>`;
    }
    if (hasChart) {
      html += `<div><div class="ent-section-title">${escHtml(r.chart.label || 'Gráfica')}</div>
        <div class="ent-chart-wrap"><canvas id="ent-chart-canvas"></canvas></div></div>`;
    }
    html += `</div>`;
  }

  if (r.note) html += `<div class="ent-note">${escHtml(r.note)}</div>`;

  document.getElementById('ent-body').innerHTML = html;
  document.getElementById('enterprise-overlay').classList.remove('hidden');

  if (hasChart) {
    const ctx = document.getElementById('ent-chart-canvas').getContext('2d');
    if (entChart) { entChart.destroy(); entChart = null; }
    const isLine = r.chart.type === 'line';
    entChart = new Chart(ctx, {
      type: isLine ? 'line' : 'bar',
      data: {
        labels: r.chart.labels,
        datasets: [{
          data: r.chart.values,
          backgroundColor: isLine ? 'rgba(124,58,237,0.15)' : col.c + 'cc',
          borderColor: col.c,
          borderWidth: 2,
          borderRadius: isLine ? 0 : 4,
          pointRadius: isLine ? 0 : undefined,
          tension: 0.35,
          fill: isLine,
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: { duration: 500 },
        plugins: { legend: { display: false },
          tooltip: { backgroundColor: 'rgba(10,22,40,0.95)', borderColor: col.c, borderWidth: 1, titleColor: col.c, bodyColor: '#cbd5e1' } },
        scales: {
          x: { grid: { color: 'rgba(255,255,255,0.04)' }, ticks: { color: '#64748b', font: { family: 'JetBrains Mono', size: 8 }, maxRotation: 45 } },
          y: { grid: { color: 'rgba(255,255,255,0.04)' }, ticks: { color: '#64748b', font: { family: 'JetBrains Mono', size: 9 } } }
        }
      }
    });
  }
}

document.getElementById('ent-close').addEventListener('click', () => document.getElementById('enterprise-overlay').classList.add('hidden'));
document.getElementById('enterprise-overlay').addEventListener('click', e => { if (e.target === e.currentTarget) e.currentTarget.classList.add('hidden'); });

// ─── Algorithm Builder ───────────────────────────────────────────────────────

const BUILDER_GATES = ['H','X','Y','Z','S','T','RX','RY','RZ','CNOT','CZ','SWAP','ISWAP','CRY','RZZ','CCX','CSWAP'];
const BUILDER_COLS = 12;

function initBuilder() {
  // Gate palette
  const palette = document.getElementById('builder-gate-palette');
  palette.innerHTML='';
  BUILDER_GATES.forEach(g=>{
    const btn=document.createElement('button');
    btn.className='builder-gate-btn'; btn.textContent=g; btn.dataset.gate=g;
    btn.addEventListener('click',()=>{
      document.querySelectorAll('.builder-gate-btn').forEach(b=>b.classList.remove('selected-gate'));
      btn.classList.add('selected-gate'); selectedBuilderGate=g;
    });
    palette.appendChild(btn);
  });
}

function buildCircuitGrid() {
  const n = parseInt(document.getElementById('builder-qcount').value)||3;
  const grid = document.getElementById('builder-circuit-grid');
  grid.innerHTML='';
  for(let q=0;q<n;q++){
    const row=document.createElement('div'); row.className='builder-row';
    const label=document.createElement('div'); label.className='builder-qubit-label'; label.textContent=`q${q}`;
    row.appendChild(label);
    const slots=document.createElement('div'); slots.className='builder-slots';
    for(let col=0;col<BUILDER_COLS;col++){
      const slot=document.createElement('div'); slot.className='builder-slot';
      slot.dataset.qubit=q; slot.dataset.col=col;
      slot.addEventListener('click',()=>onBuilderSlotClick(q,col,slot));
      slots.appendChild(slot);
    }
    row.appendChild(slots); grid.appendChild(row);
  }
  renderBuilderSteps();
}

function onBuilderSlotClick(qubit, col, slotEl) {
  if(!selectedBuilderGate){ log('Selecciona una puerta primero','err'); return; }
  const gate=selectedBuilderGate;
  const isTwoQ=['CNOT','CZ','SWAP','ISWAP','CRX','CRY','CRZ','RXX','RYY','RZZ'].includes(gate);
  const isThreeQ=['CCX','CSWAP'].includes(gate);
  const n=parseInt(document.getElementById('builder-qcount').value)||3;
  const needsParams=['RX','RY','RZ','P','CRX','CRY','CRZ','RXX','RYY','RZZ'].includes(gate);
  let params=[];
  if(needsParams){ const v=prompt(`Parámetro θ para ${gate} (radianes):`,'1.5708'); if(!v)return; params=[parseFloat(v)||Math.PI/2]; }

  if(isTwoQ){
    if(qubit+1>=n){ log('No hay qubit destino para la puerta de 2 qubits','err'); return; }
    addBuilderStep({gate,qubits:[qubit,qubit+1],params,col});
  } else if(isThreeQ){
    if(qubit+2>=n){ log('No hay qubits suficientes para Toffoli','err'); return; }
    addBuilderStep({gate,qubits:[qubit,qubit+1,qubit+2],params,col});
  } else {
    addBuilderStep({gate,qubits:[qubit],params,col});
  }
}

function addBuilderStep(step) {
  builderSteps.push(step);
  renderBuilderSteps();
  placeBuilderGateVisual(step);
}

function placeBuilderGateVisual(step) {
  const n=parseInt(document.getElementById('builder-qcount').value)||3;
  step.qubits.forEach((q,qi)=>{
    const rows=document.querySelectorAll('.builder-row');
    const row=rows[q]; if(!row)return;
    const slots=row.querySelectorAll('.builder-slot');
    const slot=slots[step.col]; if(!slot)return;
    // Find first empty col
    let targetCol=step.col;
    for(let c=0;c<BUILDER_COLS;c++){
      const s=slots[c]; if(!s.querySelector('.builder-slot-gate')){ targetCol=c; break; }
    }
    const actualSlot=slots[targetCol]; if(!actualSlot)return;
    step.col=targetCol;
    const gateEl=document.createElement('div');
    gateEl.className='builder-slot-gate';
    gateEl.textContent = qi===0?step.gate:(qi===step.qubits.length-1?'⊕':'●');
    const idx=builderSteps.length-1;
    gateEl.title='Clic para eliminar';
    gateEl.addEventListener('click',e=>{e.stopPropagation();removeBuilderStep(idx);});
    actualSlot.appendChild(gateEl);
  });
}

function removeBuilderStep(idx) {
  builderSteps.splice(idx,1);
  buildCircuitGrid();
  builderSteps.forEach(s=>placeBuilderGateVisual(s));
  renderBuilderSteps();
}

function renderBuilderSteps() {
  const container=document.getElementById('builder-steps');
  container.innerHTML=builderSteps.length===0
    ? `<div style="color:var(--text-muted);font-size:10px;padding:4px 8px">Haz clic en una puerta y luego en la rejilla del qubit...</div>`
    : builderSteps.map((s,i)=>`
      <div class="builder-step">
        <span class="builder-step-num">${i+1}</span>
        <span class="builder-step-gate">${s.gate}</span>
        <span class="builder-step-qubits">qubits: [${s.qubits.join(',')}]${s.params&&s.params.length?' θ='+s.params.map(p=>p.toFixed(2)).join(','):''}</span>
        <span class="builder-step-del" data-idx="${i}" title="Eliminar paso">✕</span>
      </div>`).join('');
  container.querySelectorAll('.builder-step-del').forEach(el=>{
    el.addEventListener('click',()=>{removeBuilderStep(parseInt(el.dataset.idx));});
  });
}

document.getElementById('btn-open-builder').addEventListener('click',()=>{
  builderSteps=[]; selectedBuilderGate=null;
  document.querySelectorAll('.builder-gate-btn').forEach(b=>b.classList.remove('selected-gate'));
  buildCircuitGrid();
  document.getElementById('builder-overlay').classList.remove('hidden');
});
document.getElementById('builder-cancel').addEventListener('click',()=>{ document.getElementById('builder-overlay').classList.add('hidden'); });
document.getElementById('builder-clear').addEventListener('click',()=>{ builderSteps=[]; buildCircuitGrid(); });

document.getElementById('builder-qcount').addEventListener('change',()=>{ builderSteps=[]; buildCircuitGrid(); });

document.getElementById('builder-run').addEventListener('click', async()=>{
  if(!builderSteps.length){ log('El circuito está vacío','err'); return; }
  const name=document.getElementById('builder-name').value||'Mi Algoritmo';
  log(`Ejecutando circuito: "${name}" (${builderSteps.length} pasos)`,'algo');
  try {
    const circuit=builderSteps.map(s=>({gate:s.gate,qubits:s.qubits,params:s.params||[]}));
    await api('POST','/api/circuit',{circuit});
    log('Circuito ejecutado correctamente','ok');
  } catch(e){}
  document.getElementById('builder-overlay').classList.add('hidden');
});

// ─── Header Actions ──────────────────────────────────────────────────────────

document.getElementById('btn-reset').addEventListener('click', async()=>{ selectedQubits=[]; await api('POST','/api/reset'); });
document.getElementById('btn-measure-all').addEventListener('click', async()=>{
  log('⊗ Midiendo todos los qubits...','info');
  try {
    const resp = await api('POST','/api/measure_all');
    // WS will handle display, but as fallback:
    if(resp && resp.results) {
      setTimeout(()=>{ if(document.getElementById('measure-result-overlay').classList.contains('hidden')) {
        showMeasurementResult(resp.results);
      }}, 200);
    }
  } catch(e){}
});
document.getElementById('btn-add-qubit').addEventListener('click', async()=>{
  const n = state.n_qubits || 0;
  const nextN = n + 1;
  const memMB = Math.round((2 ** nextN) * 16 / 1024 / 1024);
  const memStr = memMB >= 1024 ? (memMB/1024).toFixed(1)+' GB' : memMB+' MB';
  // Fetch current max from server
  let maxQ = 28;
  try { const info = await apiFetch(API_BASE+'/api/info').then(r=>r.json()); maxQ = info.max_qubits; } catch(e){}
  if (nextN >= maxQ - 2) log(`AVISO: qubit ${nextN} — estado vectorial ~${memStr} de RAM`, 'err');
  else if (nextN >= 20) log(`Qubit ${nextN} — estado vectorial ~${memStr}`, 'info');
  await api('POST','/api/qubits/add');
});
document.getElementById('btn-remove-qubit').addEventListener('click', async()=>{ await api('POST','/api/qubits/remove'); });
document.getElementById('btn-clear-circuit').addEventListener('click', async()=>{ await api('POST','/api/reset'); });
document.getElementById('state-search').addEventListener('input',e=>{ stateFilter=e.target.value.trim(); renderStatevector(); });

// ─── Shot Sampling (histograma) ───────────────────────────────────────────────

function renderSampleHistogram(data) {
  const body = document.getElementById('sample-body');
  const sub  = document.getElementById('sample-subheading');
  if (!data || !data.counts) { body.innerHTML = '<div style="color:var(--text-dim)">Sin datos</div>'; return; }
  sub.textContent = `${data.shots.toLocaleString()} shots · ${data.distinct} resultados distintos`;
  const maxCount = Math.max(...data.counts.map(c=>c.count), 1);
  body.innerHTML = `<div class="histo-list">` + data.counts.map(c=>{
    const pct = (c.count / maxCount * 100);
    return `<div class="histo-row">
      <div class="histo-label">|${escHtml(c.state.slice(-12))}⟩</div>
      <div class="histo-bar-wrap"><div class="histo-bar" style="width:${pct}%"><span class="histo-count">${c.count}</span></div></div>
      <div class="histo-prob">${(c.prob*100).toFixed(1)}%</div>
    </div>`;
  }).join('') + `</div>`;
}

async function runSample() {
  const shots = parseInt(document.getElementById('sample-shots').value) || 1024;
  log(`⇶ Muestreando ${shots} shots...`, 'info');
  try {
    if (sendWS({cmd:'sample', shots})) return;       // WS path → renderSampleHistogram via message
    const data = await api('POST','/api/sample',{shots});
    renderSampleHistogram(data);
  } catch(e) { log('Error en muestreo: '+e.message,'err'); }
}

document.getElementById('btn-sample').addEventListener('click', ()=>{
  document.getElementById('sample-body').innerHTML =
    '<div style="color:var(--text-dim);padding:20px;text-align:center">Pulsa «Ejecutar muestreo» para obtener el histograma de mediciones.</div>';
  document.getElementById('sample-subheading').textContent = '';
  document.getElementById('sample-overlay').classList.remove('hidden');
});
document.getElementById('sample-run').addEventListener('click', runSample);
document.getElementById('sample-close').addEventListener('click', ()=>document.getElementById('sample-overlay').classList.add('hidden'));
document.getElementById('sample-overlay').addEventListener('click', e=>{ if(e.target===e.currentTarget) e.currentTarget.classList.add('hidden'); });

// ─── QASM Export ──────────────────────────────────────────────────────────────

let _lastQasm = '';
document.getElementById('btn-qasm').addEventListener('click', async()=>{
  try {
    const data = await api('GET','/api/qasm');
    _lastQasm = data.qasm || '';
    document.getElementById('qasm-code').textContent = _lastQasm || '// El circuito está vacío — aplica puertas o ejecuta un algoritmo primero.';
    document.getElementById('qasm-subheading').textContent = `${data.n_qubits} qubits · profundidad ${data.depth} · OpenQASM 2.0 (Qiskit-compatible)`;
    document.getElementById('qasm-overlay').classList.remove('hidden');
  } catch(e) { log('Error exportando QASM: '+e.message,'err'); }
});
document.getElementById('qasm-close').addEventListener('click', ()=>document.getElementById('qasm-overlay').classList.add('hidden'));
document.getElementById('qasm-overlay').addEventListener('click', e=>{ if(e.target===e.currentTarget) e.currentTarget.classList.add('hidden'); });
document.getElementById('qasm-copy').addEventListener('click', ()=>{
  navigator.clipboard?.writeText(_lastQasm).then(()=>log('QASM copiado al portapapeles','ok')).catch(()=>log('No se pudo copiar','err'));
});
document.getElementById('qasm-download').addEventListener('click', ()=>{
  const blob = new Blob([_lastQasm||''], {type:'text/plain'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'omega_circuit.qasm';
  a.click();
  URL.revokeObjectURL(a.href);
  log('Circuito descargado (omega_circuit.qasm)','ok');
});

// ─── PennyLane circuit ────────────────────────────────────────────────────────

document.getElementById('btn-pennylane').addEventListener('click', async () => {
  log('Reconstruyendo circuito con PennyLane...', 'info');
  try {
    const data = await api('GET', '/api/pennylane');
    const code = document.getElementById('pennylane-code');
    const sub = document.getElementById('pennylane-sub');
    if (!data.available) {
      code.textContent = data.note || 'PennyLane no disponible.';
      sub.textContent = 'Backend opcional no instalado';
    } else if (!data.drawing) {
      code.textContent = data.note || 'El circuito está vacío. Aplica puertas o ejecuta un algoritmo.';
      sub.textContent = `${data.ops || 0} operaciones`;
    } else {
      code.textContent = data.drawing;
      sub.textContent = `${data.n_qubits} qubits · ${data.ops} operaciones · fidelidad con el motor ${(data.fidelity * 100).toFixed(2)}%`;
    }
    document.getElementById('pennylane-overlay').classList.remove('hidden');
  } catch (e) { log('Error PennyLane: ' + e.message, 'err'); }
});
document.getElementById('pennylane-close').addEventListener('click', () => document.getElementById('pennylane-overlay').classList.add('hidden'));
document.getElementById('pennylane-overlay').addEventListener('click', e => { if (e.target === e.currentTarget) e.currentTarget.classList.add('hidden'); });

// ─── Noise Control ────────────────────────────────────────────────────────────

let _noiseTimer = null;
document.getElementById('noise-slider').addEventListener('input', e=>{
  const pct = parseFloat(e.target.value);
  document.getElementById('noise-value').textContent = pct.toFixed(1)+'%';
  clearTimeout(_noiseTimer);
  _noiseTimer = setTimeout(()=>{
    const level = pct/100;
    if (!sendWS({cmd:'noise', level})) { api('POST','/api/noise',{level}).catch(()=>{}); }
  }, 220);
});

// ─── Console ─────────────────────────────────────────────────────────────────

function log(msg, type='') {
  const c=document.getElementById('console-log');
  const e=document.createElement('div'); e.className='log-entry';
  const t=new Date().toLocaleTimeString('es',{hour12:false});
  e.innerHTML=`<span class="log-time">${t}</span><span class="log-msg ${type}">${msg}</span>`;
  c.prepend(e);
  while(c.children.length>60) c.removeChild(c.lastChild);
}

// ─── Init ─────────────────────────────────────────────────────────────────────

async function init() {
  log('Omega Core Quantum v2 iniciando...','info');
  initBuilder();
  // Fetch system info and display RAM + max qubits
  try {
    const info = await apiFetch(API_BASE+'/api/info').then(r=>r.json());
    const maxQ = info.max_qubits;
    const ramGb = info.total_ram_gb;
    const usableGb = info.usable_ram_gb;
    const stateMb = info.max_state_vector_mb;
    document.getElementById('ram-info-display').textContent =
      `RAM: ${ramGb}GB total | max: ${maxQ} qubits (~${stateMb>=1024?(stateMb/1024).toFixed(1)+'GB':(stateMb||0).toFixed(0)+'MB'} estado)`;
    log(`Sistema detectado: ${ramGb} GB RAM (${usableGb} GB usable)`, 'ok');
    log(`Máximo de qubits calculado: ${maxQ} qubits`, 'ok');
    // Update add button tooltip
    const addBtn = document.getElementById('btn-add-qubit');
    if (addBtn) addBtn.dataset.tooltip = `Añadir qubit (máx: ${maxQ})`;
  } catch(e) {
    document.getElementById('ram-info-display').textContent = 'RAM: servidor offline';
  }

  try {
    gates = await apiFetch(API_BASE+'/api/gates').then(r=>r.json());
    renderGateButtons();
    log('Puertas cuánticas cargadas','ok');
  } catch(e) { log('Servidor no disponible — ejecuta start_windows.bat','err'); }
  try {
    algorithms = await apiFetch(API_BASE+'/api/algorithms').then(r=>r.json());
    renderAlgorithms();
    log(algorithms.length+' algoritmos disponibles','ok');
  } catch(e) {}
  try {
    enterpriseSolutions = await apiFetch(API_BASE+'/api/enterprise/list').then(r=>r.json());
    renderEnterprise();
    log(enterpriseSolutions.length+' casos de uso cargados','ok');
  } catch(e) {}
  log('Conectando a '+WS_URL+'...','info');
  connectWS();
  setInterval(()=>{ if(ws&&ws.readyState===WebSocket.OPEN) ws.send(JSON.stringify({cmd:'ping'})); }, 10000);
}

// Arranque: mostrar pantalla de login (init() se llama después de login exitoso)
startLoginFlow();
