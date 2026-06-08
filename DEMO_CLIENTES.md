# Demostración para clientes — La ventaja de la computación cuántica

> Documento comercial y técnico para enseñar a un cliente, en **5 minutos** y de forma
> verificable, por qué un algoritmo cuántico resuelve ciertos problemas con muchísimas
> menos operaciones que el método clásico — usando **QuantumCore** (emulador de estado
> vectorial con cálculos cuánticos reales).

---

## 1. Resumen ejecutivo

La computación cuántica no es "un ordenador más rápido": es una **forma distinta de calcular**
que, en ciertos problemas (búsqueda, optimización, simulación molecular, criptografía),
necesita **muchísimas menos operaciones** que cualquier método clásico.

QuantumCore **emula** un ordenador cuántico en un servidor normal y ejecuta **los mismos
algoritmos** que correrían en hardware cuántico real. Esto permite **demostrar, enseñar,
prototipar y vender** la ventaja cuántica **sin pagar tiempo de QPU real**.

**La prueba que verá el cliente (ejemplo de esta guía):** encontrar un registro en una base
de datos sin índice de **1.024 elementos**:

| Método | Operaciones necesarias |
|---|---|
| Clásico (uno a uno) | ~512 de media · 1.024 en el peor caso |
| **Cuántico (Grover)** | **25** |

➡️ **~20× menos operaciones**, y la ventaja **crece** cuanto más grande es el problema.

---

## 2. El experimento: búsqueda en una base de datos sin índice (algoritmo de Grover)

### El problema
Tienes una lista **sin ordenar ni indexar** de N elementos y quieres encontrar uno concreto.
Clásicamente no hay atajo: tienes que mirarlos de uno en uno (de media, la mitad).

### Por qué el cuántico gana
El algoritmo de **Grover** prepara los N estados **en superposición a la vez** y, mediante
"amplificación de amplitud", concentra la probabilidad sobre el elemento buscado en solo
**≈ (π/4)·√N** pasos. Es un resultado **matemáticamente demostrado** (óptimo para búsqueda
no estructurada).

### El escalado (la clave del valor)

| Tamaño de la base | Clásico (media) | Grover (≈√N) | Reducción |
|---|---|---|---|
| 256 | 128 | 13 | ~10× |
| 1.024 | 512 | 25 | ~20× |
| 1.000.000 | 500.000 | ~785 | ~640× |
| 1.000 millones | 500.000.000 | ~25.000 | ~20.000× |

> Cuanto mayor es el problema, **mayor es la ventaja**. Eso es lo que hace valiosa a la
> computación cuántica.

---

## 3. Cómo hacerlo paso a paso con QuantumCore

> Tiempo estimado: 3–5 minutos.

**Paso 0 — Acceder.** Abre la aplicación e inicia sesión (usuario `admin`, contraseña `admin1234`).

**Paso 1 — Elegir el caso.** En la columna izquierda, panel **«Casos de uso»**, pulsa
**«Búsqueda en Datos»** (industria *Datos*).

**Paso 2 — Introducir tus datos reales.** En el formulario:
- Pega tu lista de registros, **uno por línea** (clientes, productos, IDs… lo que quieras
  demostrar). Para la demo, 1.024 líneas, o usa el ejemplo precargado.
- Indica la **posición a buscar** (por ejemplo, `300`).

**Paso 3 — Ejecutar.** Pulsa **«Resolver con computación cuántica»**.

**Paso 4 — Leer el resultado.** Aparece el panel de resultados con:
- **Registro encontrado** (el elemento buscado).
- **Iteraciones de Grover** (≈25 para 1.024 registros).
- **Probabilidad de éxito** (≈100 %).
- **Ventaja**: √N frente a N.

**Paso 5 — Demostrar que es cuántica de verdad (la parte que convence).**
- En el centro, observa las **Esferas de Bloch** (arrástralas con el ratón para verlas
  **en 3D**): muestran el estado real de cada qubit.
- Mira el **Circuito Cuántico** dibujado paso a paso (puertas H, oráculo, difusor).
- Pulsa **«Ver en PennyLane»**: el servidor reconstruye el circuito con la **librería
  PennyLane de Python** (estándar de la industria) y **verifica** que produce el mismo
  estado (fidelidad ~100 %). Esto prueba que no es un truco: es un circuito cuántico real.
- Pulsa **«⤓ QASM»** para exportar el circuito en **OpenQASM 2.0** y ejecutarlo, si quieres,
  en Qiskit o en hardware cuántico real.

**Paso 6 — Enseñar el escalado.** Repite con listas de 256 y de 1.024 elementos y compara
las **iteraciones**: el cliente ve con sus ojos que el coste cuántico crece como √N mientras
el clásico crece como N.

---

## 4. Segundo ejemplo (lenguaje de negocio): optimización de cartera

Para un cliente financiero/operativo, el caso **«Optimización de Cartera»** es muy visual:

1. **Casos de uso → Optimización de Cartera.**
2. Introduce **tus activos** con su rendimiento (%) y su riesgo (%).
3. Pulsa resolver: el algoritmo **QAOA** evalúa **todas las 2ⁿ combinaciones posibles**
   (con 10 activos son **1.024 carteras**) y devuelve la **óptima** (mejor rentabilidad
   ajustada al riesgo), con su ratio de Sharpe.

Mensaje para el cliente: *"explora exponencialmente muchas combinaciones para encontrar la
mejor decisión"*. Mismo patrón en **logística** (Max-Cut), **operaciones** (mochila,
asignación de tareas), **planificación** (coloreado de grafos) o **ciberseguridad** (BB84, QRNG).

---

## 5. Honestidad técnica: emulado vs. hardware real

Para no sobrevender (y ganar credibilidad ante un cliente técnico):

- QuantumCore **emula** el ordenador cuántico en un ordenador clásico. Por eso usa
  **RAM como qubits**: el estado de *n* qubits son 2ⁿ números complejos en memoria
  (cada qubit **duplica** la RAM). El panel **«Recursos · RAM → qubits»** lo muestra en
  vivo (p. ej., 16 GB ≈ **28–29 qubits**).
- En consecuencia, **en el emulador el tiempo de reloj no es menor** que el clásico para
  estos tamaños: lo que se demuestra es la **lógica y el escalado del algoritmo cuántico**
  (número de operaciones/consultas), que es **idéntico** al que se ejecutaría en una QPU real.
- El mismo circuito (exportable a **QASM** y verificado con **PennyLane**) es el que, en
  **hardware cuántico real**, sí entrega la ventaja en tiempo. QuantumCore es la herramienta
  perfecta para **diseñar, validar y enseñar** ese circuito **antes** de pagar QPU real.

> En una frase para el cliente: *"Te enseño el algoritmo cuántico funcionando y su ventaja
> de escalado, exactamente el que correría en una máquina cuántica real — sin el coste de
> alquilarla."*

---

## 6. Valoración como usuario (experiencia)

| Aspecto | Valoración |
|---|---|
| Instalación | **Muy fácil**: un solo `.bat` en Windows (autoinstala todo) o `python server.py` |
| Curva de aprendizaje | **Baja**: casos de uso con formularios; no hay que saber programar |
| Claridad de la demo | **Alta**: KPIs, gráficas, esferas de Bloch 3D interactivas y circuito visual |
| Credibilidad técnica | **Alta**: verificación con PennyLane + exportación a QASM/Qiskit |
| Para vender/educar | **Excelente**: resultados inmediatos y comprensibles por no-técnicos |
| Limitación | El tamaño está acotado por la RAM (≈28–30 qubits en 16 GB) |

**Veredicto de usuario:** herramienta ideal para **demostración comercial, formación y
prototipado**. Convierte un concepto abstracto en algo que el cliente ve, toca y verifica.

---

## 7. Valoración económica

### 7.1 Coste de QuantumCore (este software)
- **Software**: incluido (sin licencias por uso).
- **Infraestructura**: corre en un PC normal o en un VPS modesto.
  - PC/portátil existente: **0 € adicionales**.
  - VPS 8–16 GB RAM: **~20–40 €/mes** aprox.
- **Coste por ejecución / por demo: 0 €.** Ejecuciones ilimitadas.
- **Capacidad**: hasta ~**28–30 qubits** con 16 GB; suficiente para todos los casos de uso
  de demostración y prototipado.

### 7.2 Coste de acceder a hardware cuántico real (nube) — *cifras orientativas, verifica precios actuales*

| Plataforma | Modelo de precio (aprox.) | Coste típico de un experimento |
|---|---|---|
| IBM Quantum (pay-as-you-go) | ~1,6 $/segundo de QPU | Una sesión de minutos: **decenas–cientos de $** |
| AWS Braket (QPU IonQ/Rigetti) | ~0,30 $/tarea + ~0,01–0,03 $/disparo | 1.000 disparos: **~30–60 $** por circuito |
| AWS Braket (simulador gestionado) | ~0,075–4,50 $/min según motor | Pruebas largas: **varios $–decenas de $** |
| Azure Quantum | pago por uso (similar) | Comparable a las anteriores |

> Nota: los planes **gratuitos** de IBM/otros existen pero tienen **colas, límites y cupos**;
> no son fiables para una **demo en directo** ante un cliente.

### 7.3 Comparativa y recomendación

| | QuantumCore (emulado) | QPU real en nube |
|---|---|---|
| Coste por demo | **0 €** | decenas de € |
| Disponibilidad inmediata | **Sí** | colas / reservas |
| Repetible ilimitadamente | **Sí** | cada ejecución cuesta |
| Resultado idéntico al algoritmo real | **Sí** (verificable en PennyLane) | Sí (+ ruido físico) |
| Ventaja en tiempo de reloj | No (es emulación) | Sí (en problemas grandes) |

**Flujo recomendado (máximo ROI):**
1. **Emulador (QuantumCore)** → educar, demostrar a clientes, prototipar y **validar** los
   circuitos. Coste marginal **0 €**.
2. **QPU real** → solo para la **ejecución final** de circuitos ya validados, cuando el
   problema lo justifique. Pagas QPU **una vez** y sobre algo que ya sabes que funciona.

### 7.4 ROI ilustrativo
- Un equipo que prototipa/forma con **100 circuitos** en el emulador: **0 €** frente a
  **cientos–miles de €** si cada prueba se ejecutara en QPU real.
- Cada **demostración comercial** con QuantumCore: **0 €** y sin depender de colas.
- Valor añadido: **formación** del equipo, **captación** de clientes y **reducción del
  riesgo** (no pagas hardware real hasta haber validado el algoritmo).

---

## 8. Conclusión

QuantumCore permite **demostrar de forma creíble y verificable** la ventaja algorítmica de
la computación cuántica —con un ejemplo tan claro como "**25 operaciones frente a 512**"—,
**enseñarla** a clientes no técnicos y **prototipar** circuitos reales (exportables a QASM y
verificados con PennyLane) **sin coste por uso**. Es la pieza ideal para la fase de
**evangelización, formación y prototipado**, reservando el (caro) hardware cuántico real
únicamente para la ejecución final cuando el caso de negocio lo justifique.

---

*Credenciales de demo: usuario `admin` · contraseña `admin1234`. Las cifras de precios de
nube son orientativas (mercado 2024–2025); conviene verificar las tarifas vigentes de cada
proveedor.*
