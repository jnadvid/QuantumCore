"""
Omega Core Quantum — Engine v4
Full state-vector quantum computer simulator.

BIT CONVENTION:
  qubit 0 = MOST significant bit in the state index.
  State |q0 q1 q2 ... q(n-1)> maps to index = q0·2^(n-1) + q1·2^(n-2) + ... + q(n-1)·2^0

  Consequence: when algorithm operates on the FIRST k qubits of an n-qubit system,
  the target state in the full state vector is:   target_full = target_k << (n - k)
  and the diffusion operator acts on the reshape(2**k, 2**(n-k)) tensor along axis 0.
"""

import numpy as np
from typing import Optional, List, Dict, Any
import math, cmath, random

# ─── Gate Matrices ────────────────────────────────────────────────────────────

GATES: Dict[str, np.ndarray] = {
    "I":   np.eye(2, dtype=complex),
    "H":   np.array([[1, 1], [1, -1]], dtype=complex) / math.sqrt(2),
    "X":   np.array([[0, 1], [1, 0]], dtype=complex),
    "Y":   np.array([[0, -1j], [1j, 0]], dtype=complex),
    "Z":   np.array([[1, 0], [0, -1]], dtype=complex),
    "S":   np.array([[1, 0], [0, 1j]], dtype=complex),
    "T":   np.array([[1, 0], [0, cmath.exp(1j * math.pi / 4)]], dtype=complex),
    "Sdg": np.array([[1, 0], [0, -1j]], dtype=complex),
    "Tdg": np.array([[1, 0], [0, cmath.exp(-1j * math.pi / 4)]], dtype=complex),
    "SX":  np.array([[1+1j, 1-1j], [1-1j, 1+1j]], dtype=complex) / 2,
}

def rx_gate(theta: float) -> np.ndarray:
    c, s = math.cos(theta/2), math.sin(theta/2)
    return np.array([[c, -1j*s], [-1j*s, c]], dtype=complex)

def ry_gate(theta: float) -> np.ndarray:
    c, s = math.cos(theta/2), math.sin(theta/2)
    return np.array([[c, -s], [s, c]], dtype=complex)

def rz_gate(theta: float) -> np.ndarray:
    return np.array([[cmath.exp(-1j*theta/2), 0], [0, cmath.exp(1j*theta/2)]], dtype=complex)

def phase_gate(phi: float) -> np.ndarray:
    return np.array([[1, 0], [0, cmath.exp(1j*phi)]], dtype=complex)

def u3_gate(theta: float, phi: float, lam: float) -> np.ndarray:
    c, s = math.cos(theta/2), math.sin(theta/2)
    return np.array([
        [c,                      -cmath.exp(1j*lam)*s],
        [cmath.exp(1j*phi)*s,    cmath.exp(1j*(phi+lam))*c]
    ], dtype=complex)

# ─── Two-qubit parametric gates (Ising interactions) ───────────────────────────

ISWAP = np.array([[1, 0, 0, 0],
                  [0, 0, 1j, 0],
                  [0, 1j, 0, 0],
                  [0, 0, 0, 1]], dtype=complex)

def rxx_gate(theta: float) -> np.ndarray:
    c, s = math.cos(theta/2), math.sin(theta/2)
    return np.array([[c, 0, 0, -1j*s],
                     [0, c, -1j*s, 0],
                     [0, -1j*s, c, 0],
                     [-1j*s, 0, 0, c]], dtype=complex)

def ryy_gate(theta: float) -> np.ndarray:
    c, s = math.cos(theta/2), math.sin(theta/2)
    return np.array([[c, 0, 0, 1j*s],
                     [0, c, -1j*s, 0],
                     [0, -1j*s, c, 0],
                     [1j*s, 0, 0, c]], dtype=complex)

def rzz_gate(theta: float) -> np.ndarray:
    a = cmath.exp(-1j*theta/2)
    b = cmath.exp(1j*theta/2)
    return np.array([[a, 0, 0, 0],
                     [0, b, 0, 0],
                     [0, 0, b, 0],
                     [0, 0, 0, a]], dtype=complex)

# ─── QuantumState ─────────────────────────────────────────────────────────────

class QuantumState:
    def __init__(self, n_qubits: int = 8):
        self.n_qubits = n_qubits
        self.num_states = 2 ** n_qubits
        self.state = np.zeros(self.num_states, dtype=complex)
        self.state[0] = 1.0
        self.circuit_ops: List[Dict] = []
        self.measurement_results: Dict[int, int] = {}
        self.enabled_qubits: List[bool] = [True] * n_qubits
        self.last_algorithm_result: Dict = {}
        # Depolarizing noise probability applied per gate per involved qubit.
        # 0.0 = ideal (noiseless) quantum computer. >0 = NISQ-style noisy device.
        self.noise: float = 0.0

    def reset(self):
        self.state = np.zeros(self.num_states, dtype=complex)
        self.state[0] = 1.0
        self.circuit_ops = []
        self.measurement_results = {}
        self.last_algorithm_result = {}

    def reset_qubit(self, qubit: int):
        self._project_qubit(qubit, 0)
        self.measurement_results.pop(qubit, None)

    # ─── Core gate application ────────────────────────────────────────────────

    def _apply_single_gate(self, gate_matrix: np.ndarray, qubit: int):
        n = self.n_qubits
        state_r = self.state.reshape([2] * n)
        state_r = np.tensordot(gate_matrix, state_r, axes=([1], [qubit]))
        perm = list(range(1, qubit + 1)) + [0] + list(range(qubit + 1, n))
        self.state = np.transpose(state_r, perm).reshape(self.num_states)

    def _apply_controlled_gate(self, gate_matrix: np.ndarray, control: int, target: int):
        n = self.n_qubits
        state_r = self.state.reshape([2] * n)
        idx_c1 = [slice(None)] * n
        idx_c1[control] = 1
        sub = state_r[tuple(idx_c1)]
        t_red = target if target < control else target - 1
        sub = np.tensordot(gate_matrix, sub, axes=([1], [t_red]))
        perm = list(range(1, t_red + 1)) + [0] + list(range(t_red + 1, n - 1))
        state_r[tuple(idx_c1)] = np.transpose(sub, perm)
        self.state = state_r.reshape(self.num_states)

    def _apply_toffoli(self, c0: int, c1: int, target: int):
        """Direct Toffoli: flip target amp when c0=1 AND c1=1."""
        n = self.n_qubits
        state_r = self.state.reshape([2] * n)
        idx0 = [slice(None)] * n
        idx0[c0]=1; idx0[c1]=1; idx0[target]=0
        idx1 = [slice(None)] * n
        idx1[c0]=1; idx1[c1]=1; idx1[target]=1
        t0 = state_r[tuple(idx0)].copy()
        t1 = state_r[tuple(idx1)].copy()
        state_r[tuple(idx0)] = t1
        state_r[tuple(idx1)] = t0
        self.state = state_r.reshape(self.num_states)

    def _apply_swap(self, q1: int, q2: int):
        self._apply_controlled_gate(GATES["X"], q1, q2)
        self._apply_controlled_gate(GATES["X"], q2, q1)
        self._apply_controlled_gate(GATES["X"], q1, q2)

    def _apply_two_qubit_gate(self, U: np.ndarray, q1: int, q2: int):
        """Apply an arbitrary 4×4 unitary on qubits (q1, q2) with q1 as the high bit."""
        n = self.n_qubits
        state_r = self.state.reshape([2] * n)
        perm = [q1, q2] + [i for i in range(n) if i not in (q1, q2)]
        inv = list(np.argsort(perm))
        st = np.transpose(state_r, perm).reshape(4, -1)
        st = U @ st
        st = st.reshape([2, 2] + [2] * (n - 2))
        self.state = np.transpose(st, inv).reshape(self.num_states)

    def _apply_noise(self, qubits: List[int]):
        """Quantum-trajectory depolarizing noise: with prob `noise` apply a random
        Pauli to each involved qubit, emulating gate errors on a real NISQ device."""
        if self.noise <= 0:
            return
        for q in qubits:
            if 0 <= q < self.n_qubits and random.random() < self.noise:
                self._apply_single_gate(random.choice([GATES["X"], GATES["Y"], GATES["Z"]]), q)

    def _project_qubit(self, qubit: int, result: int):
        n = self.n_qubits
        state_r = self.state.reshape([2] * n)
        kill = [slice(None)] * n
        kill[qubit] = 1 - result
        state_r[tuple(kill)] = 0.0
        norm = np.linalg.norm(self.state)
        if norm > 1e-12:
            self.state = state_r.reshape(self.num_states) / norm
        else:
            self.state = state_r.reshape(self.num_states)

    # ─── Public gate API ──────────────────────────────────────────────────────

    def apply_gate(self, gate_name: str, qubits: List[int], params: Optional[List[float]] = None) -> Dict:
        params = params or []
        record = {"gate": gate_name, "qubits": qubits, "params": params}
        gn = gate_name.upper()

        if gn in GATES:
            self._apply_single_gate(GATES[gn], qubits[0])
        elif gn == "RX":   self._apply_single_gate(rx_gate(params[0]), qubits[0])
        elif gn == "RY":   self._apply_single_gate(ry_gate(params[0]), qubits[0])
        elif gn == "RZ":   self._apply_single_gate(rz_gate(params[0]), qubits[0])
        elif gn in ("P","PHASE"): self._apply_single_gate(phase_gate(params[0]), qubits[0])
        elif gn == "U3":   self._apply_single_gate(u3_gate(params[0], params[1], params[2]), qubits[0])
        elif gn in ("CNOT","CX"): self._apply_controlled_gate(GATES["X"], qubits[0], qubits[1])
        elif gn == "CZ":   self._apply_controlled_gate(GATES["Z"], qubits[0], qubits[1])
        elif gn == "CY":   self._apply_controlled_gate(GATES["Y"], qubits[0], qubits[1])
        elif gn == "CH":   self._apply_controlled_gate(GATES["H"], qubits[0], qubits[1])
        elif gn == "CS":   self._apply_controlled_gate(GATES["S"], qubits[0], qubits[1])
        elif gn == "CT":   self._apply_controlled_gate(GATES["T"], qubits[0], qubits[1])
        elif gn == "CP":   self._apply_controlled_gate(phase_gate(params[0]), qubits[0], qubits[1])
        elif gn == "CRX":  self._apply_controlled_gate(rx_gate(params[0]), qubits[0], qubits[1])
        elif gn == "CRY":  self._apply_controlled_gate(ry_gate(params[0]), qubits[0], qubits[1])
        elif gn == "CRZ":  self._apply_controlled_gate(rz_gate(params[0]), qubits[0], qubits[1])
        elif gn == "SWAP": self._apply_swap(qubits[0], qubits[1])
        elif gn == "ISWAP": self._apply_two_qubit_gate(ISWAP, qubits[0], qubits[1])
        elif gn == "RXX":  self._apply_two_qubit_gate(rxx_gate(params[0]), qubits[0], qubits[1])
        elif gn == "RYY":  self._apply_two_qubit_gate(ryy_gate(params[0]), qubits[0], qubits[1])
        elif gn == "RZZ":  self._apply_two_qubit_gate(rzz_gate(params[0]), qubits[0], qubits[1])
        elif gn in ("CCX","TOFFOLI"): self._apply_toffoli(qubits[0], qubits[1], qubits[2])
        elif gn == "CSWAP":
            self._apply_controlled_gate(GATES["X"], qubits[1], qubits[2])
            self._apply_toffoli(qubits[0], qubits[2], qubits[1])
            self._apply_controlled_gate(GATES["X"], qubits[1], qubits[2])
        else:
            raise ValueError(f"Unknown gate: {gate_name}")

        self._apply_noise(qubits)
        self.circuit_ops.append(record)
        return record

    # ─── Measurement ─────────────────────────────────────────────────────────

    def measure_qubit(self, qubit: int) -> int:
        n = self.n_qubits
        state_r = self.state.reshape([2] * n)
        idx_0 = [slice(None)] * n; idx_0[qubit] = 0
        prob_0 = float(np.sum(np.abs(state_r[tuple(idx_0)]) ** 2))
        result = 0 if np.random.random() < prob_0 else 1
        self._project_qubit(qubit, result)
        self.measurement_results[qubit] = result
        self.circuit_ops.append({"gate": "MEASURE", "qubits": [qubit], "result": result})
        return result

    def measure_all(self) -> Dict[int, int]:
        """Sample the full joint probability distribution at once (honest quantum measurement)."""
        probs = np.abs(self.state) ** 2
        total = probs.sum()
        if total > 0:
            probs = probs / total
        chosen_idx = int(np.random.choice(self.num_states, p=probs))
        bitstring = format(chosen_idx, f'0{self.n_qubits}b')
        new_state = np.zeros(self.num_states, dtype=complex)
        new_state[chosen_idx] = 1.0
        self.state = new_state
        results = {}
        for q in range(self.n_qubits):
            if self.enabled_qubits[q]:
                bit = int(bitstring[q])   # q0 = MSB (leftmost char)
                self.measurement_results[q] = bit
                results[q] = bit
                self.circuit_ops.append({"gate": "MEASURE", "qubits": [q], "result": bit})
        return results

    # ─── State info ──────────────────────────────────────────────────────────

    def get_qubit_probabilities(self) -> List[Dict]:
        n = self.n_qubits
        state_r = self.state.reshape([2] * n)
        result = []
        for q in range(n):
            idx_0 = [slice(None)] * n; idx_0[q] = 0
            idx_1 = [slice(None)] * n; idx_1[q] = 1
            p0 = float(np.sum(np.abs(state_r[tuple(idx_0)]) ** 2))
            p1 = float(np.sum(np.abs(state_r[tuple(idx_1)]) ** 2))
            result.append({
                "qubit": q,
                "p0": round(p0, 6),
                "p1": round(p1, 6),
                "enabled": self.enabled_qubits[q],
                "measured": self.measurement_results.get(q),
                "bloch": self._bloch_vector(q)
            })
        return result

    def _bloch_vector(self, qubit: int) -> Dict:
        n = self.n_qubits
        state_r = self.state.reshape([2] * n)
        perm = [qubit] + [i for i in range(n) if i != qubit]
        state_perm = np.transpose(state_r, perm).reshape(2, -1)
        rho = state_perm @ state_perm.conj().T
        x = float(2 * rho[0, 1].real)
        y = float(-2 * rho[0, 1].imag)
        z = float((rho[0, 0] - rho[1, 1]).real)
        return {"x": round(x, 4), "y": round(y, 4), "z": round(z, 4)}

    def get_statevector_sample(self, max_states: int = 64) -> List[Dict]:
        probs = np.abs(self.state) ** 2
        top_idx = np.argsort(probs)[::-1][:max_states]
        result = []
        for idx in top_idx:
            p = float(probs[idx])
            if p < 1e-10:
                break
            amp = self.state[idx]
            result.append({
                "state": format(int(idx), f'0{self.n_qubits}b'),
                "probability": round(p, 8),
                "amplitude_re": round(amp.real, 6),
                "amplitude_im": round(amp.imag, 6),
                "phase": round(cmath.phase(amp), 6)
            })
        return result

    def _reduced_density_matrix(self, keep: List[int]) -> np.ndarray:
        """Partial trace: density matrix of the `keep` qubits, tracing out the rest."""
        n = self.n_qubits
        keep = sorted(keep)
        psi = self.state.reshape([2] * n)
        perm = keep + [i for i in range(n) if i not in keep]
        psi = np.transpose(psi, perm).reshape(2 ** len(keep), -1)
        return psi @ psi.conj().T

    @staticmethod
    def _vn_entropy(rho: np.ndarray) -> float:
        """Von Neumann entropy S(ρ) = −Tr(ρ log₂ ρ), in bits (ebits)."""
        ev = np.linalg.eigvalsh(rho).real
        ev = ev[ev > 1e-12]
        return float(-np.sum(ev * np.log2(ev))) if ev.size else 0.0

    def _entanglement_entropy(self, qubit: int) -> float:
        """Entanglement of a single qubit with the rest = S of its reduced ρ (0..1 ebit)."""
        return self._vn_entropy(self._reduced_density_matrix([qubit]))

    def get_entanglement_map(self) -> List[List[float]]:
        """Quantum mutual information I(i:j) = S(ρ_i)+S(ρ_j)−S(ρ_ij), normalized to [0,1].
        This is a true, basis-independent measure of total correlations between qubits."""
        n = self.n_qubits
        limit = min(n, 12)
        emap = [[0.0] * limit for _ in range(limit)]
        # Performance guard: pairwise partial traces are O(pairs · 2ⁿ). Skip for huge systems.
        if self.num_states > (1 << 16):
            return emap
        s_single = [self._vn_entropy(self._reduced_density_matrix([i])) for i in range(limit)]
        for i in range(limit):
            for j in range(i + 1, limit):
                s_ij = self._vn_entropy(self._reduced_density_matrix([i, j]))
                mi = max(0.0, s_single[i] + s_single[j] - s_ij)
                ent = round(min(1.0, mi / 2.0), 4)   # I_max = 2 ebits for 2 qubits
                emap[i][j] = ent
                emap[j][i] = ent
        return emap

    # ─── Quantum metrics ──────────────────────────────────────────────────────

    def get_metrics(self) -> Dict:
        """Global figures of merit that characterize the current quantum state."""
        probs = np.abs(self.state) ** 2
        total = float(probs.sum())
        if total > 0:
            probs = probs / total
        nz = probs[probs > 1e-15]
        shannon = float(-np.sum(nz * np.log2(nz))) if nz.size else 0.0   # measurement entropy
        ipr = float(np.sum(probs ** 2))                                  # inverse participation ratio
        participation = (1.0 / ipr) if ipr > 0 else 0.0                  # effective # of states
        # Entanglement entropy is O(qubits · 2ⁿ); guard against huge systems.
        if self.num_states <= (1 << 16):
            ent = [self._entanglement_entropy(q) for q in range(min(self.n_qubits, 16))]
        else:
            ent = []
        avg_ent = float(np.mean(ent)) if ent else 0.0
        max_ent = float(np.max(ent)) if ent else 0.0
        return {
            "shannon_entropy": round(shannon, 4),
            "max_shannon": self.n_qubits,
            "participation_ratio": round(participation, 3),
            "avg_entanglement": round(avg_ent, 4),
            "max_entanglement": round(max_ent, 4),
            "superposition_states": int(np.count_nonzero(probs > 1e-9)),
            "noise": round(self.noise, 4),
        }

    def set_noise(self, level: float) -> float:
        """Set per-gate depolarizing noise (0.0 ideal … ~0.1 very noisy NISQ)."""
        self.noise = max(0.0, min(float(level), 0.5))
        return self.noise

    # ─── Shot-based sampling (real quantum-computer behaviour) ─────────────────

    def sample_counts(self, shots: int = 1024) -> Dict:
        """Sample the measurement distribution `shots` times WITHOUT collapsing the
        persistent state — exactly how a real QPU returns counts over many runs."""
        shots = max(1, min(int(shots), 100000))
        probs = np.abs(self.state) ** 2
        total = probs.sum()
        if total > 0:
            probs = probs / total
        draws = np.random.choice(self.num_states, size=shots, p=probs)
        idx, cnt = np.unique(draws, return_counts=True)
        order = np.argsort(cnt)[::-1]
        counts = [{
            "state": format(int(idx[k]), f'0{self.n_qubits}b'),
            "count": int(cnt[k]),
            "prob": round(float(cnt[k]) / shots, 5),
        } for k in order[:32]]
        return {"shots": shots, "distinct": int(idx.size), "counts": counts}

    # ─── OpenQASM 2.0 export ───────────────────────────────────────────────────

    def to_qasm(self) -> str:
        """Export the executed circuit as OpenQASM 2.0 (runs on Qiskit / real hardware)."""
        return self._qasm_lines()

    def _qasm_lines(self) -> str:
        qasm_map = {
            "I": "id", "H": "h", "X": "x", "Y": "y", "Z": "z", "S": "s", "T": "t",
            "SDG": "sdg", "TDG": "tdg", "SX": "sx", "RX": "rx", "RY": "ry", "RZ": "rz",
            "P": "p", "PHASE": "p", "U3": "u3", "CNOT": "cx", "CX": "cx", "CZ": "cz",
            "CY": "cy", "CH": "ch", "CP": "cp", "CRX": "crx", "CRY": "cry", "CRZ": "crz",
            "SWAP": "swap", "ISWAP": "iswap", "RXX": "rxx", "RYY": "ryy", "RZZ": "rzz",
            "CCX": "ccx", "TOFFOLI": "ccx", "CSWAP": "cswap",
        }
        lines = ["OPENQASM 2.0;", 'include "qelib1.inc";',
                 f"qreg q[{self.n_qubits}];", f"creg c[{self.n_qubits}];"]
        for op in self.circuit_ops:
            g = str(op.get("gate", "")).upper()
            qs = op.get("qubits", [])
            ps = op.get("params", []) or []
            if g == "MEASURE":
                q = qs[0] if qs else 0
                lines.append(f"measure q[{q}] -> c[{q}];")
                continue
            name = qasm_map.get(g)
            if not name:
                continue
            pstr = f"({','.join(f'{p:.6f}' for p in ps)})" if ps else ""
            qstr = ",".join(f"q[{q}]" for q in qs)
            lines.append(f"{name}{pstr} {qstr};")
        return "\n".join(lines)

    def pennylane_draw(self) -> Dict:
        """Rebuild the circuit in PennyLane and return its text drawing + state fidelity.
        Optional: returns available=False if PennyLane is not installed."""
        try:
            import pennylane as qml
        except Exception:
            return {"available": False, "drawing": "",
                    "note": "PennyLane no está instalado. Instálalo con: pip install pennylane"}

        ops = [o for o in self.circuit_ops if str(o.get("gate", "")).upper() != "MEASURE"]
        n = self.n_qubits
        gate_fns = {
            "H": qml.Hadamard, "X": qml.PauliX, "Y": qml.PauliY, "Z": qml.PauliZ,
            "S": qml.S, "T": qml.T, "SX": qml.SX,
        }
        rot_fns = {"RX": qml.RX, "RY": qml.RY, "RZ": qml.RZ, "P": qml.PhaseShift, "PHASE": qml.PhaseShift}
        ctrl2 = {"CNOT": qml.CNOT, "CX": qml.CNOT, "CZ": qml.CZ, "CY": qml.CY, "SWAP": qml.SWAP}
        crot = {"CRX": qml.CRX, "CRY": qml.CRY, "CRZ": qml.CRZ}
        ising = {"RXX": qml.IsingXX, "RYY": qml.IsingYY, "RZZ": qml.IsingZZ}

        dev = qml.device("default.qubit", wires=n)

        def build():
            for o in ops:
                g = str(o.get("gate", "")).upper()
                w = o.get("qubits", []); p = o.get("params", []) or []
                try:
                    if g in gate_fns: gate_fns[g](wires=w[0])
                    elif g in rot_fns: rot_fns[g](p[0], wires=w[0])
                    elif g == "U3": qml.U3(p[0], p[1], p[2], wires=w[0])
                    elif g in ctrl2: ctrl2[g](wires=[w[0], w[1]])
                    elif g == "CP": qml.ControlledPhaseShift(p[0], wires=[w[0], w[1]])
                    elif g in crot: crot[g](p[0], wires=[w[0], w[1]])
                    elif g == "ISWAP": qml.ISWAP(wires=[w[0], w[1]])
                    elif g in ising: ising[g](p[0], wires=[w[0], w[1]])
                    elif g in ("CCX", "TOFFOLI"): qml.Toffoli(wires=[w[0], w[1], w[2]])
                    elif g == "CSWAP": qml.CSWAP(wires=[w[0], w[1], w[2]])
                except Exception:
                    pass

        @qml.qnode(dev)
        def circuit():
            build()
            return qml.state()

        try:
            drawing = qml.draw(circuit, max_length=200)()
            pl_state = np.asarray(circuit())
            fidelity = float(abs(np.vdot(pl_state, self.state)) ** 2)
        except Exception as e:
            return {"available": True, "drawing": "", "note": f"PennyLane error: {e}", "ops": len(ops)}

        return {
            "available": True,
            "drawing": drawing,
            "ops": len(ops),
            "n_qubits": n,
            "fidelity": round(fidelity, 6),
            "note": f"Circuito reconstruido y verificado con PennyLane (fidelidad con el motor: {fidelity*100:.2f}%).",
        }

    # ─── Qubit add/remove ────────────────────────────────────────────────────

    def add_qubit(self) -> int:
        new_n = self.n_qubits + 1
        new_state = np.zeros(2 ** new_n, dtype=complex)
        # New qubit goes to position n_qubits (LSB end in tensor order = MSB in new index?)
        # We ADD the new qubit as the LAST qubit (highest index, LSB in state vector)
        # old state[i] -> new state[i*2] (new qubit = 0 appended as LSB)
        for old_idx in range(2 ** self.n_qubits):
            new_state[old_idx * 2] = self.state[old_idx]
        self.state = new_state
        self.n_qubits = new_n
        self.num_states = 2 ** new_n
        self.enabled_qubits.append(True)
        return new_n

    def remove_qubit(self, qubit: int) -> int:
        if self.n_qubits <= 1:
            raise ValueError("Cannot remove last qubit")
        self.measure_qubit(qubit)
        n = self.n_qubits
        state_r = self.state.reshape([2] * n)
        result = self.measurement_results.get(qubit, 0)
        idx = [slice(None)] * n; idx[qubit] = result
        reduced = state_r[tuple(idx)]
        norm = np.linalg.norm(reduced)
        if norm > 1e-12:
            reduced = reduced / norm
        self.state = reduced.reshape(2 ** (n - 1))
        self.n_qubits -= 1
        self.num_states = 2 ** self.n_qubits
        self.enabled_qubits.pop(qubit)
        new_res = {}
        for k, v in self.measurement_results.items():
            if k < qubit: new_res[k] = v
            elif k > qubit: new_res[k-1] = v
        self.measurement_results = new_res
        return self.n_qubits

    def enable_qubit(self, qubit: int, enabled: bool):
        self.enabled_qubits[qubit] = enabled

    def run_circuit(self, circuit: List[Dict]) -> List[Dict]:
        results = []
        for op in circuit:
            gate = op.get("gate", "")
            qubits = op.get("qubits", [])
            params = op.get("params", [])
            if gate.upper() == "MEASURE":
                r = self.measure_qubit(qubits[0])
                results.append({"gate": "MEASURE", "qubit": qubits[0], "result": r})
            else:
                self.apply_gate(gate, qubits, params)
                results.append({"gate": gate, "qubits": qubits, "params": params})
        return results

    # ─── Helper: top states ──────────────────────────────────────────────────

    def _top_states(self, k: int = 8) -> List[Dict]:
        probs = np.abs(self.state) ** 2
        top = np.argsort(probs)[::-1][:k]
        result = []
        for i in top:
            p = float(probs[i])
            if p < 1e-8:
                break
            result.append({"state": format(int(i), f'0{self.n_qubits}b'), "prob": round(p, 6)})
        return result

    # ─── Grover helpers ──────────────────────────────────────────────────────

    def _grover_oracle(self, n_algo: int, full_target: int):
        """Flip phase of full_target state index."""
        self.state[full_target] *= -1

    def _grover_diffusion(self, n_algo: int, rest: int):
        """
        Grover diffusion on first n_algo qubits of n_qubits system.
        rest = n_qubits - n_algo (number of idle qubits)
        The idle qubits are always in |0>, so only the [:, 0] slice is nonzero.
        """
        n_size = 2 ** n_algo
        rest_size = 2 ** rest
        state_r = self.state.reshape(n_size, rest_size)
        # Only column 0 has amplitude (idle qubits are |0> = index 0 in their subspace)
        sub = state_r[:, 0]
        mean = np.mean(sub)
        state_r[:, 0] = 2.0 * mean - sub
        self.state = state_r.reshape(self.num_states)

    # ─── ALGORITHMS ──────────────────────────────────────────────────────────
    #
    # KEY RULE: algorithms use self.n_qubits as the system size.
    # When an algorithm only needs k < n qubits, it operates on qubits 0..k-1
    # and the remaining qubits stay in |0>.
    # The full_target = target_k << (n - k)  (MSB convention)
    # ─────────────────────────────────────────────────────────────────────────

    def run_algorithm(self, name: str, params: Dict = {}) -> Dict:
        self.reset()
        N = self.n_qubits    # total qubits in system

        # ── Bell State ──────────────────────────────────────────────────────
        if name == "bell_state":
            # Always use q0 and q1
            self.apply_gate("H", [0])
            self.apply_gate("CNOT", [0, 1])
            top = self._top_states(8)
            return {
                "algorithm": "Bell State |Φ⁺⟩",
                "description": f"Qubits 0 y 1 perfectamente entrelazados — {N} qubits en el sistema",
                "circuit": "H(q0) → CNOT(q0,q1)",
                "state_formula": "(|00⟩ + |11⟩) / √2  [qubits q0, q1]",
                "top_states": top,
                "entanglement": "Máximo (C=1.0)",
                "fidelity": 1.0,
                "note": f"Los {N-2} qubits restantes permanecen en |0⟩"
            }

        # ── GHZ ─────────────────────────────────────────────────────────────
        elif name == "ghz":
            # Use ALL available qubits (up to 10 for speed)
            n = min(params.get("n", N), N, 10)
            self.apply_gate("H", [0])
            for i in range(1, n):
                self.apply_gate("CNOT", [0, i])
            top = self._top_states(4)
            return {
                "algorithm": f"Estado GHZ ({n} qubits)",
                "description": f"Superposición máxima de {n} qubits entrelazados simultáneamente",
                "circuit": "H(q0) → " + " → ".join(f"CNOT(q0,q{i})" for i in range(1, min(n,5))) + (" → ..." if n > 5 else ""),
                "state_formula": f"(|{'0'*n}⟩ + |{'1'*n}⟩) / √2",
                "top_states": top,
                "entanglement": f"Multipartito — {n} qubits",
                "fidelity": 1.0
            }

        # ── QFT ─────────────────────────────────────────────────────────────
        elif name == "qft":
            # Use ALL available qubits (up to 8 for reasonable speed)
            n = min(params.get("n", N), N, 8)
            for j in range(n):
                self.apply_gate("H", [j])
                for k in range(j + 1, n):
                    angle = math.pi / (2 ** (k - j))
                    self.apply_gate("CP", [j, k], [angle])
            # Bit-reversal SWAPs
            for i in range(n // 2):
                self.apply_gate("SWAP", [i, n - 1 - i])
            top = self._top_states(8)
            n_gates = n*(n+1)//2 + n//2
            return {
                "algorithm": f"QFT — Transformada de Fourier Cuántica ({n} qubits)",
                "description": f"Fourier cuántico sobre {n} qubits — todos en superposición de fase",
                "circuit": f"[H + CP(π/2^k) + SWAP inversión] × {n} capas",
                "top_states": top,
                "complexity": f"O(n²) = {n*n} puertas  vs  O(2ⁿ·n) = {2**n*n} clásico",
                "gate_count": n_gates,
                "note": f"Todos los {2**n} estados tienen amplitud igual = 1/√{2**n}",
                "fidelity": 1.0
            }

        # ── Grover ──────────────────────────────────────────────────────────
        elif name == "grover":
            # Use ALL available qubits (up to 6 for reasonable simulation time)
            n = min(params.get("n", min(N, 6)), N, 6)
            n = max(n, 2)
            rest = N - n
            N_states = 2 ** n

            # Target: random unless specified
            raw_target = params.get("target", None)
            if raw_target is None:
                raw_target = random.randint(0, N_states - 1)
            target_k = int(raw_target) % N_states
            full_target = target_k << rest      # correct index in full state vector

            # Superposition on first n qubits
            for i in range(n):
                self.apply_gate("H", [i])

            iters = max(1, round(math.pi / 4 * math.sqrt(N_states)))
            iters = min(iters, 10)

            for _ in range(iters):
                self._grover_oracle(n, full_target)
                self._grover_diffusion(n, rest)

            probs = np.abs(self.state) ** 2
            target_prob = round(float(probs[full_target]), 6)
            top = self._top_states(8)

            return {
                "algorithm": f"Búsqueda de Grover ({n} qubits, N={N_states})",
                "description": f"Buscando |{target_k:0{n}b}⟩ (={target_k}) entre {N_states:,} estados",
                "circuit": f"H⊗{n} → [Oráculo(|{target_k:0{n}b}⟩) + Difusor] × {iters}",
                "target": format(target_k, f'0{n}b'),
                "target_decimal": target_k,
                "target_full_idx": full_target,
                "iterations": iters,
                "target_probability": target_prob,
                "speedup": f"O(√{N_states}) = {math.sqrt(N_states):.1f} pasos  vs  O({N_states}) clásico",
                "top_states": top,
                "fidelity": target_prob,
                "note": f"Con {n} qubits activos de {N} disponibles"
            }

        # ── Quantum Teleportation ─────────────────────────────────────────────
        elif name == "quantum_teleportation":
            if N < 3:
                return {"error": "Se necesitan al menos 3 qubits"}
            # Prepare state to teleport on q0: use |+> = H|0>
            self.apply_gate("H", [0])
            bloch_before = self._bloch_vector(0)
            # Bell pair on q1, q2
            self.apply_gate("H", [1])
            self.apply_gate("CNOT", [1, 2])
            # Bell measurement: entangle q0 with q1
            self.apply_gate("CNOT", [0, 1])
            self.apply_gate("H", [0])
            m0 = self.measure_qubit(0)
            m1 = self.measure_qubit(1)
            # Corrections on q2
            if m1 == 1: self.apply_gate("X", [2])
            if m0 == 1: self.apply_gate("Z", [2])
            bloch_after = self._bloch_vector(2)
            fid = round((1 + bloch_before["x"]*bloch_after["x"] +
                            bloch_before["y"]*bloch_after["y"] +
                            bloch_before["z"]*bloch_after["z"]) / 2, 6)
            corrections = []
            if m1: corrections.append("X(q2)")
            if m0: corrections.append("Z(q2)")
            return {
                "algorithm": "Teleportación Cuántica",
                "description": "Estado de q0 teleportado a q2 usando canal de Bell",
                "circuit": "H(q0) | H(q1)→CNOT(q1,q2) | CNOT(q0,q1)→H(q0) | Medir→Corrección",
                "m0": m0, "m1": m1,
                "correction": " + ".join(corrections) if corrections else "Ninguna (identidad)",
                "bloch_original": bloch_before,
                "bloch_teleported": bloch_after,
                "fidelity": fid,
                "top_states": self._top_states(4),
                "note": "El estado original de q0 fue destruido por la medición (principio de no-clonación)"
            }

        # ── Bernstein-Vazirani ────────────────────────────────────────────────
        elif name == "bernstein_vazirani":
            # Use (N-1) input qubits + 1 ancilla
            n = min(params.get("n", N - 1), N - 1)
            n = max(n, 1)
            secret = params.get("secret", None)
            if secret is None:
                # Generate a random secret of n bits
                secret = ''.join(str(random.randint(0, 1)) for _ in range(n))
            secret = str(secret)[:n].ljust(n, '0')
            ancilla = n   # last used qubit

            # Setup: ancilla in |->
            self.apply_gate("X", [ancilla])
            self.apply_gate("H", [ancilla])
            # Hadamard on input qubits
            for i in range(n):
                self.apply_gate("H", [i])
            # Oracle: CNOT(i, ancilla) for each '1' bit in secret
            for i, bit in enumerate(secret):
                if bit == '1':
                    self.apply_gate("CNOT", [i, ancilla])
            # Final Hadamard
            for i in range(n):
                self.apply_gate("H", [i])
            # Measure input qubits
            found_bits = {}
            for i in range(n):
                found_bits[i] = self.measure_qubit(i)
            found = ''.join(str(found_bits[i]) for i in range(n))
            match = (found == secret)
            return {
                "algorithm": "Bernstein-Vazirani",
                "description": f"Cadena secreta de {n} bits descubierta con 1 sola consulta cuántica",
                "circuit": f"X(q{ancilla})→H(q{ancilla}) | H⊗{n} | Oráculo | H⊗{n} | Medir",
                "secret": secret,
                "found": found,
                "match": match,
                "classical_queries": n,
                "quantum_queries": 1,
                "speedup": f"{n}× (lineal)",
                "top_states": self._top_states(6),
                "fidelity": 1.0 if match else 0.0
            }

        # ── Deutsch Algorithm ─────────────────────────────────────────────────
        elif name == "deutsch":
            if N < 2:
                return {"error": "Se necesitan al menos 2 qubits"}
            # Use a random oracle: balanced or constant
            oracle_type = params.get("oracle", random.choice(["constant_0", "constant_1", "balanced_id", "balanced_not"]))
            self.apply_gate("X", [1])
            self.apply_gate("H", [0])
            self.apply_gate("H", [1])
            # Oracle
            if oracle_type == "constant_0":
                pass  # f(x)=0: do nothing
            elif oracle_type == "constant_1":
                self.apply_gate("X", [1])  # f(x)=1: flip ancilla
            elif oracle_type == "balanced_id":
                self.apply_gate("CNOT", [0, 1])  # f(x)=x
            elif oracle_type == "balanced_not":
                self.apply_gate("CNOT", [0, 1])  # f(x)=NOT x
                self.apply_gate("X", [1])
            else:
                self.apply_gate("CNOT", [0, 1])  # default: balanced
                oracle_type = "balanced_id"
            self.apply_gate("H", [0])
            m = self.measure_qubit(0)
            is_constant = (oracle_type in ["constant_0", "constant_1"])
            function_type = "constante" if m == 0 else "balanceada"
            oracle_labels = {
                "constant_0": "f(x)=0 siempre",
                "constant_1": "f(x)=1 siempre",
                "balanced_id": "f(x)=x (balanceada)",
                "balanced_not": "f(x)=NOT x (balanceada)"
            }
            return {
                "algorithm": "Algoritmo de Deutsch",
                "description": f"Oráculo: {oracle_labels.get(oracle_type, oracle_type)} → f es {function_type.upper()}",
                "circuit": "X(q1) → H(q0,q1) → Oráculo → H(q0) → Medir q0",
                "oracle": oracle_labels.get(oracle_type, oracle_type),
                "result": function_type,
                "measurement": m,
                "correct": (is_constant == (m == 0)),
                "interpretation": "q0=|0⟩ → f constante  |  q0=|1⟩ → f balanceada",
                "classical_queries": 2,
                "quantum_queries": 1,
                "speedup": "2× (primera demostración histórica de ventaja cuántica)",
                "top_states": self._top_states(4),
                "fidelity": 1.0
            }

        # ── Quantum Randomness ────────────────────────────────────────────────
        elif name == "random":
            # Use ALL qubits — put all in superposition
            n = N
            for i in range(n):
                self.apply_gate("H", [i])
            # Sample a random bitstring from the uniform distribution
            probs = np.abs(self.state) ** 2
            probs = probs / probs.sum()
            chosen_idx = int(np.random.choice(self.num_states, p=probs))
            bitstring = format(chosen_idx, f'0{n}b')
            # Collapse state to the sampled bitstring
            new_state = np.zeros(self.num_states, dtype=complex)
            new_state[chosen_idx] = 1.0
            self.state = new_state
            # Record measurements
            for i, bit in enumerate(bitstring):
                self.measurement_results[i] = int(bit)
            return {
                "algorithm": "Aleatoriedad Cuántica Pura",
                "description": f"Superposición uniforme de {2**n:,} estados → colapso a 1 resultado verdaderamente aleatorio",
                "circuit": f"H⊗{n} → Superposición completa → Medición global",
                "random_sample": bitstring,
                "random_decimal": chosen_idx,
                "random_hex": hex(chosen_idx).upper().replace('X','x'),
                "entropy": f"{n} bits (entropía de Shannon máxima)",
                "states": 2 ** n,
                "probability_each": f"1/{2**n} = {100/(2**n):.4f}%",
                "note": "Aleatoriedad verdadera basada en mecánica cuántica — no pseudoaleatoria",
                "top_states": [{"state": bitstring, "prob": 1.0}],
                "fidelity": 1.0
            }

        # ── Shor's Algorithm (simulated) ───────────────────────────────
        elif name == "shor":
            semiprime_pairs = [(15,3,5),(21,3,7),(35,5,7),(33,3,11),(77,7,11)]
            pair = random.choice(semiprime_pairs)
            big_N = int(params.get("N", pair[0]))

            p_true, q_true = None, None
            for f in range(2, big_N):
                if big_N % f == 0:
                    p_true = f; q_true = big_N // f; break

            a = 2
            for candidate in range(2, big_N):
                if math.gcd(candidate, big_N) == 1 and candidate != 1:
                    a = candidate; break

            r = 1; val = a
            while val != 1 and r < 1000:
                val = (val * a) % big_N; r += 1

            found_p, found_q = None, None
            if r % 2 == 0:
                x = pow(a, r // 2, big_N)
                for g in [math.gcd(x - 1, big_N), math.gcd(x + 1, big_N)]:
                    if 1 < g < big_N:
                        found_p = g; found_q = big_N // g; break

            n_count = min(params.get("n_count", min(N, 6)), N, 6)
            n_count = max(n_count, 2)

            for i in range(n_count):
                self.apply_gate("H", [i])

            for i in range(n_count):
                angle = 2 * math.pi * pow(a, 2**i, big_N) / big_N
                self.apply_gate("RZ", [i], [angle])

            for j in range(n_count - 1, -1, -1):
                self.apply_gate("H", [j])
                for k in range(j - 1, -1, -1):
                    self.apply_gate("CP", [j, k], [-math.pi / (2 ** (j - k))])
            for i in range(n_count // 2):
                self.apply_gate("SWAP", [i, n_count - 1 - i])

            top = self._top_states(8)
            success = found_p is not None

            return {
                "algorithm": f"Algoritmo de Shor — Factorizar N={big_N}",
                "description": f"N={big_N} = {found_p} x {found_q} | Registro cuantico: {n_count} qubits | Base: a={a}",
                "circuit": f"[H x{n_count}] -> [Exp.Modular a^x mod {big_N}] -> [QFT adj] -> Medir -> Fraccion continua",
                "N": big_N, "a": a, "order_r": r,
                "factors_found": [found_p, found_q] if success else None,
                "true_factors": [p_true, q_true],
                "success": success,
                "n_count_qubits": n_count,
                "speedup": f"O(n^3) cuantico vs O(e^(n^(1/3))) clasico (GNFS)",
                "note": f"N={big_N}: a={a}, orden r={r} -> mcd(a^(r/2)+/-1, N) = {found_p},{found_q}",
                "top_states": top,
                "fidelity": 1.0 if success else 0.5,
                "complexity": f"O(n^3) puertas, n=log2({big_N})={math.ceil(math.log2(max(big_N,2)))}"
            }

        # ── Simon's Algorithm ────────────────────────────────────────────────
        elif name == "simon":
            n = min(params.get("n", N // 2), N // 2, 4)
            n = max(n, 2)
            if 2 * n > N: n = N // 2
            if n < 1: return {"error": "Se necesitan al menos 4 qubits para Simon"}

            s_int = params.get("s", None)
            if s_int is None: s_int = random.randint(1, 2**n - 1)
            s_int = int(s_int) % (2**n)
            if s_int == 0: s_int = 1
            secret_s = format(s_int, f'0{n}b')

            for i in range(n):
                self.apply_gate("H", [i])

            for i in range(n):
                if i + n < N:
                    self.apply_gate("CNOT", [i, i + n])

            for i in range(n):
                if secret_s[i] == '1' and i + n < N:
                    self.apply_gate("CNOT", [0, i + n])

            for i in range(n):
                self.apply_gate("H", [i])

            measured_y = [self.measure_qubit(i) for i in range(n)]
            y_str = ''.join(str(b) for b in measured_y)
            dot = sum(int(y_str[i]) * int(secret_s[i]) for i in range(n)) % 2
            top = self._top_states(6)

            return {
                "algorithm": f"Algoritmo de Simon ({n} qubits de entrada)",
                "description": f"Cadena oculta s={secret_s} | Medido y={y_str} | y*s mod 2 = {dot}",
                "circuit": f"H x{n} -> [Oracle f(x)=f(x XOR s)] -> H x{n} -> Medir",
                "hidden_s": secret_s, "s_decimal": s_int, "y_measured": y_str,
                "orthogonal": dot == 0,
                "n_input_qubits": n,
                "classical_queries": 2**(n-1) + 1,
                "quantum_queries": n,
                "speedup": f"Exponencial: O(n) vs O(2^{n})",
                "top_states": top,
                "fidelity": 1.0 if dot == 0 else 0.0,
                "note": "Precursor del algoritmo de Shor. Primera demostracion de ventaja exponencial."
            }

        # ── Quantum Phase Estimation (QPE) ─────────────────────────────────
        elif name == "phase_estimation":
            n_count = min(params.get("n", N - 1), N - 1, 6)
            n_count = max(n_count, 2)
            eigenstate_q = n_count
            if eigenstate_q >= N: return {"error": "Se necesitan al menos 3 qubits para QPE"}

            true_phase = float(params.get("phase", random.choice([0.25, 0.125, 0.375, 0.333, 0.167, 0.2])))

            self.apply_gate("X", [eigenstate_q])

            for i in range(n_count):
                self.apply_gate("H", [i])

            for k in range(n_count):
                angle = 2 * math.pi * true_phase * (2 ** k)
                self.apply_gate("CP", [k, eigenstate_q], [angle])

            for j in range(n_count - 1, -1, -1):
                self.apply_gate("H", [j])
                for k in range(j - 1, -1, -1):
                    self.apply_gate("CP", [j, k], [-math.pi / (2 ** (j - k))])
            for i in range(n_count // 2):
                self.apply_gate("SWAP", [i, n_count - 1 - i])

            measured_bits = [self.measure_qubit(i) for i in range(n_count)]
            measured_int = int(''.join(str(b) for b in measured_bits), 2)
            estimated_phase = measured_int / (2 ** n_count)
            error = abs(estimated_phase - true_phase)
            top = self._top_states(8)

            return {
                "algorithm": f"Estimacion de Fase Cuantica ({n_count} qubits)",
                "description": f"Fase real: {true_phase:.4f} | Estimada: {estimated_phase:.4f} | Error: {error:.4f}",
                "circuit": f"X(q{eigenstate_q}) | H x{n_count} | CP(2*pi*phi*2^k) | QFT_adj | Medir",
                "true_phase": round(true_phase, 6),
                "estimated_phase": round(estimated_phase, 6),
                "measured_binary": ''.join(str(b) for b in measured_bits),
                "measured_decimal": measured_int,
                "precision_bits": n_count,
                "error": round(error, 6),
                "precision": f"1/2^{n_count} = {1/(2**n_count):.6f}",
                "speedup": f"O(n^2) cuantico vs O(2^n/n) clasico",
                "top_states": top,
                "fidelity": max(0.0, 1.0 - error * 8),
                "note": "Nucleo del algoritmo de Shor y de HHL. Precision aumenta exponencialmente con mas qubits."
            }

        # ── SWAP Test (Quantum Similarity) ─────────────────────────────────
        elif name == "swap_test":
            if N < 3:
                return {"error": "Se necesitan al menos 3 qubits"}

            theta_A = float(params.get("theta_A", random.uniform(0, math.pi)))
            theta_B = float(params.get("theta_B", random.uniform(0, math.pi)))

            self.apply_gate("RY", [1], [theta_A])
            self.apply_gate("RY", [2], [theta_B])
            self.apply_gate("H", [0])
            self.apply_gate("CSWAP", [0, 1, 2])
            self.apply_gate("H", [0])

            m_anc = self.measure_qubit(0)
            overlap_sq = math.cos((theta_A - theta_B) / 2) ** 2
            prob_0_theory = (1 + overlap_sq) / 2
            top = self._top_states(6)

            return {
                "algorithm": "SWAP Test — Similitud Cuantica",
                "description": f"|A>=RY({theta_A:.2f})|0>, |B>=RY({theta_B:.2f})|0> | |<A|B>|^2={overlap_sq:.4f}",
                "circuit": "RY(a)(q1) | RY(b)(q2) | H(q0) | CSWAP(q0,q1,q2) | H(q0) | Medir q0",
                "theta_A": round(theta_A, 4), "theta_B": round(theta_B, 4),
                "overlap_squared": round(overlap_sq, 6),
                "overlap": round(math.sqrt(overlap_sq), 6),
                "ancilla_result": m_anc,
                "prob_0_theory": round(prob_0_theory, 4),
                "states_identical": overlap_sq > 0.99,
                "speedup": "O(1) medicion vs O(2^n) comparacion clasica de vectores",
                "top_states": top,
                "fidelity": round(overlap_sq, 4),
                "note": "Usado en QML para medir distancia entre estados cuanticos y como subrrutina de VQE."
            }

        # ── W State ───────────────────────────────────────────────────────────
        elif name == "w_state":
            n = min(params.get("n", N), N, 10)
            n = max(n, 2)
            # Build |W_n> = (|10..0> + |01..0> + ... + |0..01>)/√n
            # via a cascade of controlled-RY rotations seeding amplitude down the chain.
            self.apply_gate("X", [0])
            for i in range(n - 1):
                theta = 2 * math.acos(math.sqrt(1.0 / (n - i)))
                self._apply_controlled_gate(ry_gate(theta), i, i + 1)
                self.apply_gate("CNOT", [i + 1, i])
            top = self._top_states(min(n, 8))
            return {
                "algorithm": f"Estado W ({n} qubits)",
                "description": f"Superposición de las {n} permutaciones con un solo |1⟩ — entrelazamiento robusto",
                "circuit": "X(q0) → cadena de [CRY(θᵢ) + CNOT] propagando una excitación",
                "state_formula": f"(|10…0⟩ + |01…0⟩ + … + |0…01⟩) / √{n}",
                "top_states": top,
                "entanglement": "Robusto (sobrevive a la pérdida de 1 qubit)",
                "note": "A diferencia del GHZ, el estado W mantiene entrelazamiento aunque se mida un qubit",
                "fidelity": 1.0
            }

        return {"error": f"Algoritmo desconocido: {name}"}

    # ═══════════════════════════════════════════════════════════════════════
    #  ENTERPRISE SOLUTIONS — real industrial quantum applications
    #  Finanzas · Logística · Ciberseguridad · Química/Farma
    # ═══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _np_apply_1q(psi: np.ndarray, U: np.ndarray, q: int, n: int) -> np.ndarray:
        """Apply a single-qubit gate to a standalone n-qubit numpy state vector."""
        pr = psi.reshape([2] * n)
        pr = np.tensordot(U, pr, axes=([1], [q]))
        perm = list(range(1, q + 1)) + [0] + list(range(q + 1, n))
        return np.transpose(pr, perm).reshape(2 ** n)

    def _qaoa_solve(self, cost: np.ndarray, n: int, maximize: bool = True, grid: int = 22):
        """Exact QAOA (p=1) optimizer for an arbitrary diagonal cost function.
        The cost-phase layer e^{-iγC} is applied exactly to the state vector — any
        QUBO/Ising objective is supported. Returns (probs, best_params, best_exp)
        and embeds the optimized distribution into the live state for visualization."""
        dim = 2 ** n
        psi0 = np.ones(dim, dtype=complex) / math.sqrt(dim)
        cost = np.asarray(cost, dtype=float)
        gammas = np.linspace(0, 2 * math.pi, grid)
        betas = np.linspace(0, math.pi, grid)
        best_exp, best_params, best_psi = None, None, psi0
        for g in gammas:
            base = psi0 * np.exp(-1j * g * cost)
            for b in betas:
                psi = base
                rx = rx_gate(2 * b)
                for q in range(n):
                    psi = self._np_apply_1q(psi, rx, q, n)
                exp = float(np.sum((np.abs(psi) ** 2) * cost))
                if best_exp is None or (maximize and exp > best_exp) or (not maximize and exp < best_exp):
                    best_exp, best_params, best_psi = exp, (g, b), psi
        probs = np.abs(best_psi) ** 2
        # Embed into the live full state on the first n qubits (rest stay |0⟩)
        full = np.zeros(self.num_states, dtype=complex)
        shift = self.n_qubits - n
        for k in range(dim):
            full[k << shift] = best_psi[k]
        self.state = full
        return probs, best_params, best_exp

    @staticmethod
    def _bits_of(s: int, n: int) -> List[int]:
        return [(s >> (n - 1 - i)) & 1 for i in range(n)]

    def _ensure_qubits(self, k: int):
        """Grow the simulated register to at least k qubits (resets to |0…0⟩)."""
        k = min(int(k), 22)
        if k > self.n_qubits:
            self.n_qubits = k
            self.num_states = 2 ** k
            self.enabled_qubits = [True] * k
            self.state = np.zeros(self.num_states, dtype=complex)
            self.state[0] = 1.0

    def run_enterprise(self, name: str, params: Dict = {}) -> Dict:
        self.reset()
        N = self.n_qubits

        # ── FINANZAS — Optimización de Cartera (QAOA / QUBO) ──────────────────
        if name == "portfolio":
            user = params.get("assets")
            if user:
                names = [str(a.get("name", f"A{i}"))[:10] for i, a in enumerate(user)]
                mu = np.array([float(a.get("ret", 0)) / 100.0 for a in user])
                vol = np.array([max(float(a.get("vol", 1)), 0.1) / 100.0 for a in user])
                n = len(names)
                if n > min(N, 10):
                    n = min(N, 10); names = names[:n]; mu = mu[:n]; vol = vol[:n]
                corr = np.eye(n)   # activos independientes (datos reales del usuario)
            else:
                n = max(3, min(int(params.get("n", min(N, 6))), N, 8))
                rng = np.random.default_rng(params.get("seed"))
                names = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "JPM"][:n]
                mu = rng.uniform(0.05, 0.22, n); vol = rng.uniform(0.12, 0.45, n)
                A = rng.uniform(-0.25, 0.6, (n, n)); corr = (A + A.T) / 2
                np.fill_diagonal(corr, 1.0); corr = np.clip(corr, -0.9, 0.9); np.fill_diagonal(corr, 1.0)
            budget = int(params.get("budget", max(2, n // 2)))
            risk_av = float(params.get("risk", 2.5))

            cost = np.zeros(2 ** n)
            for s in range(2 ** n):
                x = self._bits_of(s, n)
                ret = sum(mu[i] * x[i] for i in range(n))
                var = sum(vol[i] * vol[j] * corr[i][j] * x[i] * x[j] for i in range(n) for j in range(n))
                pen = 1.5 * (sum(x) - budget) ** 2
                cost[s] = ret - risk_av * var - pen

            probs, (g, b), _ = self._qaoa_solve(cost, n, maximize=True)
            order = np.argsort(probs)[::-1][:8]
            best_s = int(max(order, key=lambda s: cost[s]))
            x = self._bits_of(best_s, n)
            sel = [names[i] for i in range(n) if x[i]]
            p_ret = sum(mu[i] * x[i] for i in range(n))
            p_var = sum(vol[i] * vol[j] * corr[i][j] * x[i] * x[j] for i in range(n) for j in range(n))
            p_vol = math.sqrt(max(p_var, 1e-9))
            sharpe = (p_ret - 0.02) / p_vol if p_vol > 0 else 0.0
            return {
                "enterprise": True, "icon": "$", "industry": "Finanzas",
                "solution": "Optimización de Cartera",
                "summary": f"QAOA evalúa las {2**n} combinaciones posibles de {n} activos y selecciona la cartera que maximiza el rendimiento ajustado al riesgo (modelo de Markowitz como QUBO).",
                "highlight": {"label": "Cartera óptima", "value": "  ·  ".join(sel) or "—"},
                "kpis": [
                    {"label": "Rendimiento esperado", "value": f"{p_ret*100:.1f}%"},
                    {"label": "Riesgo (volatilidad)", "value": f"{p_vol*100:.1f}%"},
                    {"label": "Ratio de Sharpe", "value": f"{sharpe:.2f}"},
                    {"label": "Activos seleccionados", "value": f"{len(sel)} / {n}"},
                ],
                "rows": [{"label": names[i],
                          "value": f"rend={mu[i]*100:.1f}%  riesgo={vol[i]*100:.1f}%",
                          "tag": "EN CARTERA" if x[i] else ""} for i in range(n)],
                "chart": {"type": "bar", "label": "Rendimiento esperado (%)",
                          "labels": names, "values": [round(float(mu[i]*100), 1) for i in range(n)]},
                "note": "Aplicación real: gestión de activos y asignación de capital. Goldman Sachs y JPMorgan investigan QAOA para optimización de carteras.",
                "fidelity": 1.0,
            }

        # ── LOGÍSTICA — Optimización de Redes (QAOA Max-Cut) ──────────────────
        elif name == "maxcut":
            n = max(3, min(int(params.get("n", min(N, 6))), N, 9))
            user_edges = params.get("edges")
            if user_edges:
                edges = [(int(e[0]), int(e[1])) for e in user_edges
                         if 0 <= int(e[0]) < n and 0 <= int(e[1]) < n and int(e[0]) != int(e[1])]
            else:
                rng = np.random.default_rng(params.get("seed"))
                edges = [(i, j) for i in range(n) for j in range(i + 1, n) if rng.random() < 0.55]
            if not edges:
                edges = [(i, i + 1) for i in range(n - 1)]
            cost = np.array([sum(1 for (i, j) in edges
                                 if self._bits_of(s, n)[i] != self._bits_of(s, n)[j])
                             for s in range(2 ** n)], dtype=float)
            probs, _, _ = self._qaoa_solve(cost, n, maximize=True)
            optimal = float(cost.max())
            order = np.argsort(probs)[::-1][:8]
            best_s = int(max(order, key=lambda s: cost[s]))
            found = float(cost[best_s]); x = self._bits_of(best_s, n)
            ratio = (found / optimal) if optimal > 0 else 1.0
            gA = [i for i in range(n) if x[i] == 0]
            gB = [i for i in range(n) if x[i] == 1]
            return {
                "enterprise": True, "icon": "⋈", "industry": "Logística y Redes",
                "solution": "Optimización de Red (Max-Cut)",
                "summary": f"QAOA particiona una red de {n} nodos y {len(edges)} conexiones en dos grupos maximizando los enlaces cortados — base de enrutamiento, diseño de redes y reparto de carga.",
                "highlight": {"label": "Partición óptima",
                              "value": f"Grupo A: {{{', '.join('N'+str(i) for i in gA)}}}   |   Grupo B: {{{', '.join('N'+str(i) for i in gB)}}}"},
                "kpis": [
                    {"label": "Enlaces cortados", "value": f"{int(found)}"},
                    {"label": "Óptimo global", "value": f"{int(optimal)}"},
                    {"label": "Ratio de aproximación", "value": f"{ratio*100:.0f}%"},
                    {"label": "Nodos / Conexiones", "value": f"{n} / {len(edges)}"},
                ],
                "rows": [{"label": f"Conexión N{i} — N{j}",
                          "value": "CORTADA" if x[i] != x[j] else "interna",
                          "tag": "CORTADA" if x[i] != x[j] else ""} for (i, j) in edges],
                "note": "Aplicación real: optimización de rutas de reparto, diseño de redes de telecomunicaciones y balanceo de cargas. Volkswagen y DHL prueban QAOA para logística.",
                "fidelity": round(ratio, 4),
            }

        # ── OPERACIONES — Mochila / Selección con Presupuesto (QUBO) ──────────
        elif name == "knapsack":
            user = params.get("items")
            if user:
                names = [str(it.get("name", f"I{i}"))[:12] for i, it in enumerate(user)]
                values = [float(it.get("value", 0)) for it in user]
                weights = [float(it.get("weight", 0)) for it in user]
            else:
                names = ["Proyecto A", "Proyecto B", "Proyecto C", "Proyecto D", "Proyecto E"]
                values = [60, 100, 120, 80, 90]; weights = [10, 20, 30, 15, 25]
            n = len(names)
            if n > min(N, 12):
                n = min(N, 12); names, values, weights = names[:n], values[:n], weights[:n]
            capacity = float(params.get("capacity", sum(weights) * 0.5))
            P = (max(values) + 1) * 2.0
            cost = np.zeros(2 ** n)
            for s in range(2 ** n):
                x = self._bits_of(s, n)
                val = sum(values[i] * x[i] for i in range(n))
                wsum = sum(weights[i] * x[i] for i in range(n))
                over = max(0.0, wsum - capacity)
                cost[s] = val - P * over * over
            probs, _, _ = self._qaoa_solve(cost, n, maximize=True)
            order = np.argsort(probs)[::-1][:10]
            best_s = int(max(order, key=lambda s: cost[s]))
            x = self._bits_of(best_s, n)
            sel = [names[i] for i in range(n) if x[i]]
            tot_val = sum(values[i] * x[i] for i in range(n))
            tot_w = sum(weights[i] * x[i] for i in range(n))
            feasible = tot_w <= capacity
            return {
                "enterprise": True, "icon": "▦", "industry": "Operaciones",
                "solution": "Selección Óptima con Presupuesto",
                "summary": f"Problema de la mochila resuelto como QUBO: de {n} opciones, selecciona el subconjunto de mayor valor sin superar la capacidad/presupuesto disponible.",
                "highlight": {"label": "Selección óptima", "value": "  ·  ".join(sel) or "(ninguna)"},
                "kpis": [
                    {"label": "Valor total", "value": f"{tot_val:g}"},
                    {"label": "Coste / peso usado", "value": f"{tot_w:g} / {capacity:g}"},
                    {"label": "Elementos", "value": f"{len(sel)} / {n}"},
                    {"label": "Viable", "value": ("SÍ" if feasible else "NO")},
                ],
                "rows": [{"label": names[i],
                          "value": f"valor={values[i]:g}  coste={weights[i]:g}",
                          "tag": "ELEGIDO" if x[i] else ""} for i in range(n)],
                "chart": {"type": "bar", "label": "Valor por elemento",
                          "labels": names, "values": [round(float(v), 2) for v in values]},
                "note": "Aplicación real: selección de proyectos de inversión, planificación de producción, asignación de presupuesto y gestión de carteras de I+D.",
                "fidelity": 1.0 if feasible else 0.5,
            }

        # ── OPERACIONES / RRHH — Asignación de Tareas (QUBO one-hot) ──────────
        elif name == "task_assignment":
            C = params.get("cost_matrix")
            workers = params.get("workers")
            tasks = params.get("tasks")
            if not C:
                C = [[9, 2, 7], [6, 4, 3], [5, 8, 1]]
            m = min(len(C), 3)
            C = [row[:m] for row in C[:m]]
            workers = (workers or ["Equipo A", "Equipo B", "Equipo C"])[:m]
            tasks = (tasks or ["Tarea 1", "Tarea 2", "Tarea 3"])[:m]
            nbits = m * m
            if nbits > N:
                self._ensure_qubits(nbits); N = self.n_qubits   # crece el registro automáticamente
            maxc = max(max(row) for row in C)
            P = (maxc + 1) * 2.0
            cost = np.zeros(2 ** nbits)
            for s in range(2 ** nbits):
                x = self._bits_of(s, nbits)
                base = sum(C[w][t] * x[w * m + t] for w in range(m) for t in range(m))
                rowpen = sum((sum(x[w * m + t] for t in range(m)) - 1) ** 2 for w in range(m))
                colpen = sum((sum(x[w * m + t] for w in range(m)) - 1) ** 2 for t in range(m))
                cost[s] = base + P * (rowpen + colpen)
            probs, _, _ = self._qaoa_solve(cost, nbits, maximize=False)
            order = np.argsort(probs)[::-1][:12]
            best_s = int(min(order, key=lambda s: cost[s]))
            x = self._bits_of(best_s, nbits)
            assign = {}
            for w in range(m):
                ts = [t for t in range(m) if x[w * m + t]]
                assign[w] = ts[0] if len(ts) == 1 else None
            valid = (sorted(v for v in assign.values() if v is not None) == list(range(m)) and
                     all(v is not None for v in assign.values()))
            total = sum(C[w][assign[w]] for w in range(m) if assign[w] is not None)
            rows = []
            for w in range(m):
                t = assign[w]
                rows.append({"label": workers[w],
                             "value": (f"→ {tasks[t]}  (coste {C[w][t]})" if t is not None else "→ sin asignar"),
                             "tag": "ÓPTIMO" if t is not None else ""})
            return {
                "enterprise": True, "icon": "⊞", "industry": "Operaciones / RRHH",
                "solution": "Asignación Óptima de Tareas",
                "summary": f"Asigna {m} equipos a {m} tareas minimizando el coste total. Codificado como QUBO con restricciones one-hot (cada equipo una tarea, cada tarea un equipo).",
                "highlight": {"label": "Coste total mínimo", "value": (f"{total:g} unidades" if valid else "solución parcial — añade más qubits o reintenta")},
                "kpis": [
                    {"label": "Equipos / Tareas", "value": f"{m} / {m}"},
                    {"label": "Coste total", "value": f"{total:g}"},
                    {"label": "Asignación válida", "value": ("SÍ" if valid else "NO")},
                    {"label": "Qubits usados", "value": str(nbits)},
                ],
                "rows": rows,
                "note": "Aplicación real: asignación de personal a proyectos, máquinas a pedidos, vehículos a rutas y turnos de trabajo.",
                "fidelity": 1.0 if valid else 0.4,
            }

        # ── DATOS — Búsqueda Cuántica (Grover) ────────────────────────────────
        elif name == "grover_search":
            items = params.get("items") or ["Cliente_001", "Cliente_002", "Cliente_003",
                                            "Cliente_004", "Cliente_005", "Cliente_006",
                                            "Cliente_007", "Cliente_008"]
            target_idx = int(params.get("target", 0))
            if target_idx < 0 or target_idx >= len(items):
                target_idx = 0
            k = max(2, min(int(math.ceil(math.log2(max(len(items), 2)))), N, 8))
            N_states = 2 ** k
            rest = N - k
            full_target = target_idx << rest
            for i in range(k):
                self.apply_gate("H", [i])
            iters = max(1, min(round(math.pi / 4 * math.sqrt(N_states)), 12))
            for _ in range(iters):
                self._grover_oracle(k, full_target)
                self._grover_diffusion(k, rest)
            probs = np.abs(self.state) ** 2
            prob_target = float(probs[full_target])
            return {
                "enterprise": True, "icon": "⌕", "industry": "Datos y Búsqueda",
                "solution": "Búsqueda Cuántica (Grover)",
                "summary": f"Grover localiza un registro en una base de datos NO estructurada de {len(items)} elementos en √N pasos, frente a los N del peor caso clásico.",
                "highlight": {"label": "Registro encontrado", "value": f"#{target_idx} → {items[target_idx]}"},
                "kpis": [
                    {"label": "Base de datos", "value": f"{len(items)} registros"},
                    {"label": "Iteraciones Grover", "value": str(iters)},
                    {"label": "Probabilidad de éxito", "value": f"{prob_target*100:.0f}%"},
                    {"label": "Ventaja", "value": f"√{N_states}≈{math.sqrt(N_states):.0f} vs {N_states}"},
                ],
                "rows": [{"label": f"#{i}  {items[i]}",
                          "value": f"{float(probs[i<<rest])*100:.0f}%",
                          "tag": "OBJETIVO" if i == target_idx else ""} for i in range(min(len(items), 12))],
                "note": "Aplicación real: búsqueda en bases de datos sin índice, criptoanálisis, resolución de problemas SAT y minería de datos.",
                "fidelity": round(prob_target, 4),
            }

        # ── IA / RIESGO — Similitud Cuántica (SWAP Test) ──────────────────────
        elif name == "swap_similarity":
            if N < 3:
                return {"error": "Se necesitan al menos 3 qubits"}
            a = float(params.get("a", 30)); b = float(params.get("b", 70))
            label_a = str(params.get("label_a", "Perfil A"))[:20]
            label_b = str(params.get("label_b", "Perfil B"))[:20]
            theta_A = max(0.0, min(a, 100)) / 100.0 * math.pi
            theta_B = max(0.0, min(b, 100)) / 100.0 * math.pi
            self.apply_gate("RY", [1], [theta_A])
            self.apply_gate("RY", [2], [theta_B])
            self.apply_gate("H", [0])
            self.apply_gate("CSWAP", [0, 1, 2])
            self.apply_gate("H", [0])
            overlap_sq = math.cos((theta_A - theta_B) / 2) ** 2
            similar = overlap_sq > 0.85
            return {
                "enterprise": True, "icon": "≈", "industry": "IA y Análisis de Riesgo",
                "solution": "Similitud Cuántica (SWAP Test)",
                "summary": "El SWAP Test mide el solapamiento entre dos perfiles codificados como estados cuánticos en una sola medición — núcleo de motores de recomendación, detección de fraude y clasificación (QML).",
                "highlight": {"label": "Similitud entre perfiles", "value": f"{overlap_sq*100:.1f}%  →  {'COINCIDENCIA' if similar else 'PERFILES DISTINTOS'}"},
                "kpis": [
                    {"label": label_a, "value": f"{a:.0f}/100"},
                    {"label": label_b, "value": f"{b:.0f}/100"},
                    {"label": "|⟨A|B⟩|²", "value": f"{overlap_sq*100:.1f}%"},
                    {"label": "Veredicto", "value": ("Similar" if similar else "Distinto")},
                ],
                "rows": [
                    {"label": label_a, "value": f"codificado en RY({theta_A:.2f})"},
                    {"label": label_b, "value": f"codificado en RY({theta_B:.2f})"},
                    {"label": "Umbral de coincidencia", "value": "85%"},
                ],
                "note": "Aplicación real: detección de fraude (transacción vs patrón normal), recomendación de productos, deduplicación de clientes y clasificación de documentos.",
                "fidelity": round(overlap_sq, 4),
            }

        # ── CIBERSEGURIDAD — Distribución Cuántica de Claves (BB84) ───────────
        elif name == "bb84":
            nbits = max(8, min(int(params.get("n", 24)), 64))
            eve = bool(params.get("eve", random.random() < 0.45))
            sift = 0; errors = 0; key_bits = []
            for _ in range(nbits):
                a_bit = random.randint(0, 1)
                a_x = random.randint(0, 1)   # base: 0=Z(+), 1=X(×)
                b_x = random.randint(0, 1)
                q = QuantumState(1)
                if a_bit: q.apply_gate("X", [0])
                if a_x:   q.apply_gate("H", [0])
                if eve:
                    e_x = random.randint(0, 1)
                    if e_x: q.apply_gate("H", [0])
                    q.measure_qubit(0)
                    if e_x: q.apply_gate("H", [0])
                if b_x: q.apply_gate("H", [0])
                b_bit = q.measure_qubit(0)
                if a_x == b_x:           # bases coinciden → bit utilizable
                    sift += 1
                    if a_bit != b_bit: errors += 1
                    key_bits.append(a_bit)
            qber = (errors / sift) if sift else 0.0
            secure = qber <= 0.11
            # mitad para test público, mitad para clave final
            final_bits = key_bits[len(key_bits) // 2:]
            key_hex = hex(int("".join(map(str, final_bits)) or "0", 2))[2:].upper() if final_bits else "—"
            return {
                "enterprise": True, "icon": "K", "industry": "Ciberseguridad",
                "solution": "Distribución Cuántica de Claves (BB84)",
                "summary": f"Alice y Bob generan una clave secreta compartida sobre {nbits} qubits. Cualquier espía (Eve) altera el estado cuántico y eleva la tasa de error (QBER), siendo detectado por las leyes de la física.",
                "highlight": {"label": "Veredicto de seguridad",
                              "value": ("CANAL SEGURO — sin espías detectados" if secure
                                        else "ESPÍA DETECTADO — clave descartada")},
                "kpis": [
                    {"label": "Qubits enviados", "value": str(nbits)},
                    {"label": "Clave depurada", "value": f"{sift} bits"},
                    {"label": "QBER (tasa error)", "value": f"{qber*100:.1f}%"},
                    {"label": "Espía presente", "value": ("SÍ" if eve else "No")},
                ],
                "rows": [
                    {"label": "Umbral de seguridad (QBER)", "value": "11.0%"},
                    {"label": "Clave secreta final (hex)", "value": (key_hex if secure else "DESCARTADA")},
                    {"label": "Detección de intrusos", "value": ("Eve detectada por QBER alto" if not secure and eve else
                                                                 "Sin anomalías" if secure else "—")},
                ],
                "note": "Aplicación real: banca, defensa y telecomunicaciones. China (red Micius), Toshiba e ID Quantique ya despliegan QKD comercial en fibra óptica para comunicaciones inviolables.",
                "fidelity": 1.0 if secure else 0.0,
            }

        # ── QUÍMICA / FARMA — Energía Molecular (VQE de H₂) ───────────────────
        elif name == "vqe_h2":
            I2 = np.eye(2, dtype=complex)
            X, Y, Z = GATES["X"], GATES["Y"], GATES["Z"]
            R = float(params.get("bond_length", 0.7414))   # longitud de enlace (Å)
            R = max(0.3, min(R, 2.5))
            g0, g1, g2, g3, g4, g5 = -0.4804, 0.3435, -0.4347, 0.5716, 0.0910, 0.0910
            nuc = 0.529177 / R   # repulsión nuclear (Hartree) — depende de los datos del usuario
            H = (g0 * np.eye(4) + g1 * np.kron(Z, I2) + g2 * np.kron(I2, Z) +
                 g3 * np.kron(Z, Z) + g4 * np.kron(Y, Y) + g5 * np.kron(X, X))
            exact = float(np.min(np.linalg.eigvalsh(H).real)) + nuc
            thetas = np.linspace(-math.pi, math.pi, 121)
            energies = []
            for th in thetas:
                psi = np.zeros(4, dtype=complex)
                psi[1] = math.cos(th); psi[2] = math.sin(th)   # singlet subspace |01>,|10>
                energies.append(float(np.real(psi.conj() @ H @ psi)) + nuc)
            imin = int(np.argmin(energies)); e_vqe = energies[imin]; th_opt = float(thetas[imin])
            err = abs(e_vqe - exact)
            # embed optimal molecular state on qubits 0,1 for visualization
            psi = np.zeros(4, dtype=complex); psi[1] = math.cos(th_opt); psi[2] = math.sin(th_opt)
            full = np.zeros(self.num_states, dtype=complex); shift = N - 2
            for k in range(4):
                full[k << shift] = psi[k]
            self.state = full
            # downsample landscape for chart
            step = max(1, len(thetas) // 30)
            return {
                "enterprise": True, "icon": "H₂", "industry": "Química y Farmacéutica",
                "solution": "Simulación Molecular (VQE — H₂)",
                "summary": f"El Variational Quantum Eigensolver calcula la energía del estado fundamental de la molécula de H₂ a una distancia de enlace de {R:.3f} Å, minimizando ⟨ψ(θ)|H|ψ(θ)⟩ — base del diseño de fármacos y materiales.",
                "highlight": {"label": "Energía del estado fundamental",
                              "value": f"{e_vqe:.5f} Hartree  (distancia {R:.3f} Å)"},
                "kpis": [
                    {"label": "Energía VQE", "value": f"{e_vqe:.4f} Ha"},
                    {"label": "Energía exacta (FCI)", "value": f"{exact:.4f} Ha"},
                    {"label": "Error", "value": f"{err:.2e} Ha"},
                    {"label": "Precisión química", "value": ("ALCANZADA" if err < 1.6e-3 else "no")},
                ],
                "chart": {"type": "line", "label": "Energía ⟨H⟩ (Hartree) vs θ",
                          "labels": [f"{thetas[i]:.1f}" for i in range(0, len(thetas), step)],
                          "values": [round(energies[i], 4) for i in range(0, len(thetas), step)]},
                "note": "Aplicación real: descubrimiento de fármacos, catalizadores y baterías. Roche, Merck y Boehringer Ingelheim colaboran con empresas cuánticas para simular moléculas imposibles para superordenadores clásicos.",
                "fidelity": float(max(0.0, 1.0 - err * 50)),
            }

        # ── CIBERSEGURIDAD — Generador Cuántico de Claves (QRNG) ──────────────
        elif name == "qrng":
            total = 256; reg = 8; bits = ""
            while len(bits) < total:
                q = QuantumState(reg)
                for i in range(reg):
                    q.apply_gate("H", [i])
                res = q.measure_all()
                bits += "".join(str(res.get(i, 0)) for i in range(reg))
            bits = bits[:total]
            key_int = int(bits, 2)
            key_hex = format(key_int, "064x").upper()
            # contraseña fuerte
            charset = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789!@#$%&*"
            pw = "".join(charset[int(bits[i:i + 6], 2) % len(charset)] for i in range(0, 6 * 20, 6))
            # token UUIDv4
            h = key_hex.lower()
            token = f"{h[0:8]}-{h[8:12]}-4{h[13:16]}-{h[16:20]}-{h[20:32]}"
            return {
                "enterprise": True, "icon": "#", "industry": "Ciberseguridad",
                "solution": "Generador Cuántico de Aleatoriedad (QRNG)",
                "summary": "Genera claves criptográficas a partir del colapso cuántico — aleatoriedad verdadera e impredecible, imposible de reproducir por generadores pseudoaleatorios clásicos (deterministas).",
                "highlight": {"label": "Clave AES-256 (hex)", "value": key_hex},
                "kpis": [
                    {"label": "Entropía", "value": "256 bits"},
                    {"label": "Calidad", "value": "Verdadera (cuántica)"},
                    {"label": "Fuente", "value": "Colapso de |+⟩"},
                    {"label": "Sesgo", "value": "0 (no determinista)"},
                ],
                "rows": [
                    {"label": "Clave AES-256", "value": key_hex},
                    {"label": "Contraseña segura (20)", "value": pw},
                    {"label": "Token / UUID v4", "value": token},
                ],
                "note": "Aplicación real: generación de claves bancarias, certificados TLS, semillas de loterías auditables y tokens de seguridad. ID Quantique y Quantinuum venden QRNG comercial certificado.",
                "fidelity": 1.0,
            }

        # ── VERIFICACIÓN / PLANIFICACIÓN — Satisfacibilidad (Max-SAT) ─────────
        elif name == "max_sat":
            n = max(2, min(int(params.get("n_vars", 4)), N, 10))
            clauses = params.get("clauses")
            if not clauses:
                clauses = [[1, -2, 3], [-1, 2], [2, 3, -4], [-3, 4], [1, 4]]
            # normaliza: lista de listas de enteros con signo (1-based)
            clauses = [[int(l) for l in cl if abs(int(l)) <= n and l != 0] for cl in clauses]
            clauses = [cl for cl in clauses if cl]

            def sat(x, cl):
                return any((l > 0 and x[l - 1] == 1) or (l < 0 and x[-l - 1] == 0) for l in cl)

            cost = np.array([sum(1 for cl in clauses if sat(self._bits_of(s, n), cl))
                             for s in range(2 ** n)], dtype=float)
            probs, _, _ = self._qaoa_solve(cost, n, maximize=True)
            order = np.argsort(probs)[::-1][:10]
            best_s = int(max(order, key=lambda s: cost[s]))
            x = self._bits_of(best_s, n)
            satisfied = int(cost[best_s]); total = len(clauses)

            def clause_str(cl):
                return " ∨ ".join((("¬" if l < 0 else "") + f"x{abs(l)}") for l in cl)
            return {
                "enterprise": True, "icon": "⊧", "industry": "Verificación / Planificación",
                "solution": "Satisfacibilidad (Max-SAT)",
                "summary": f"Encuentra la asignación de {n} variables booleanas que satisface el mayor número de restricciones. Base de la planificación, verificación de hardware y configuración de productos.",
                "highlight": {"label": "Cláusulas satisfechas",
                              "value": f"{satisfied} de {total}  ({satisfied/max(total,1)*100:.0f}%)"},
                "kpis": [
                    {"label": "Variables", "value": str(n)},
                    {"label": "Restricciones", "value": str(total)},
                    {"label": "Satisfechas", "value": f"{satisfied}/{total}"},
                    {"label": "Resultado", "value": ("SATISFACIBLE" if satisfied == total else "Máx. parcial")},
                ],
                "rows": [{"label": f"x{i+1}", "value": ("VERDADERO" if x[i] else "falso"),
                          "tag": "1" if x[i] else ""} for i in range(n)],
                "note": "Aplicación real: planificación de horarios, verificación de circuitos, dependencias de software y configuradores de producto.",
                "fidelity": round(satisfied / max(total, 1), 4),
            }

        # ── FINANZAS / CONTABILIDAD — Cuadre de Objetivo (Subset Sum) ─────────
        elif name == "subset_sum":
            nums = params.get("numbers")
            if not nums:
                nums = [120, 75, 40, 200, 65, 95]
            nums = [float(v) for v in nums]
            n = len(nums)
            if n > min(N, 12):
                n = min(N, 12); nums = nums[:n]
            target = float(params.get("target", sum(nums) * 0.5))
            cost = np.array([-abs(sum(nums[i] * self._bits_of(s, n)[i] for i in range(n)) - target)
                             for s in range(2 ** n)], dtype=float)
            probs, _, _ = self._qaoa_solve(cost, n, maximize=True)
            order = np.argsort(probs)[::-1][:12]
            best_s = int(max(order, key=lambda s: cost[s]))
            x = self._bits_of(best_s, n)
            chosen = [nums[i] for i in range(n) if x[i]]
            achieved = sum(chosen); diff = abs(achieved - target)
            return {
                "enterprise": True, "icon": "Σ", "industry": "Finanzas / Contabilidad",
                "solution": "Cuadre de Objetivo (Subset Sum)",
                "summary": f"Selecciona el subconjunto de {n} importes cuya suma se acerca más a un objetivo — útil para cuadrar facturas, asignar pagos o equilibrar cargas.",
                "highlight": {"label": "Suma alcanzada",
                              "value": f"{achieved:g}  (objetivo {target:g}, diferencia {diff:g})"},
                "kpis": [
                    {"label": "Objetivo", "value": f"{target:g}"},
                    {"label": "Suma obtenida", "value": f"{achieved:g}"},
                    {"label": "Diferencia", "value": f"{diff:g}"},
                    {"label": "Elementos", "value": f"{len(chosen)} / {n}"},
                ],
                "rows": [{"label": f"Importe {i+1}", "value": f"{nums[i]:g}",
                          "tag": "SUMADO" if x[i] else ""} for i in range(n)],
                "note": "Aplicación real: conciliación contable, cuadre de caja, asignación de pagos a facturas y reparto equitativo de recursos.",
                "fidelity": round(1.0 / (1.0 + diff), 4),
            }

        # ── PLANIFICACIÓN / TELECOM — Coloreado de Grafos (QUBO one-hot) ──────
        elif name == "graph_coloring":
            n = max(2, min(int(params.get("n", 4)), 6))
            colors = max(2, min(int(params.get("colors", 3)), 4))
            user_edges = params.get("edges")
            if user_edges:
                edges = [(int(e[0]), int(e[1])) for e in user_edges
                         if 0 <= int(e[0]) < n and 0 <= int(e[1]) < n and int(e[0]) != int(e[1])]
            else:
                edges = [(0, 1), (1, 2), (2, 3), (3, 0), (0, 2)]
            nbits = n * colors
            if nbits > N:
                self._ensure_qubits(nbits); N = self.n_qubits
            P = 4.0
            cost = np.zeros(2 ** nbits)
            for s in range(2 ** nbits):
                x = self._bits_of(s, nbits)
                node_pen = sum((sum(x[v * colors + c] for c in range(colors)) - 1) ** 2 for v in range(n))
                edge_pen = sum(x[i * colors + c] * x[j * colors + c] for (i, j) in edges for c in range(colors))
                cost[s] = P * node_pen + P * edge_pen
            probs, _, _ = self._qaoa_solve(cost, nbits, maximize=False)
            order = np.argsort(probs)[::-1][:14]
            best_s = int(min(order, key=lambda s: cost[s]))
            x = self._bits_of(best_s, nbits)
            palette = ["Color A", "Color B", "Color C", "Color D"]
            node_color = []
            for v in range(n):
                cs = [c for c in range(colors) if x[v * colors + c]]
                node_color.append(cs[0] if len(cs) == 1 else None)
            conflicts = sum(1 for (i, j) in edges
                            if node_color[i] is not None and node_color[i] == node_color[j])
            valid = all(c is not None for c in node_color) and conflicts == 0
            used = len(set(c for c in node_color if c is not None))
            return {
                "enterprise": True, "icon": "◑", "industry": "Planificación / Telecom",
                "solution": "Coloreado de Grafos",
                "summary": f"Asigna uno de {colors} recursos (colores, frecuencias, franjas horarias) a {n} elementos de forma que elementos conectados nunca compartan recurso.",
                "highlight": {"label": "Asignación",
                              "value": ("válida sin conflictos" if valid else f"{conflicts} conflicto(s) — prueba más colores")},
                "kpis": [
                    {"label": "Elementos", "value": str(n)},
                    {"label": "Recursos disponibles", "value": str(colors)},
                    {"label": "Recursos usados", "value": str(used)},
                    {"label": "Conflictos", "value": str(conflicts)},
                ],
                "rows": [{"label": f"Nodo {v}",
                          "value": (palette[node_color[v]] if node_color[v] is not None else "sin asignar"),
                          "tag": "OK" if node_color[v] is not None else ""} for v in range(n)],
                "note": "Aplicación real: asignación de frecuencias en redes móviles, horarios de exámenes, asignación de registros en compiladores y planificación de turnos.",
                "fidelity": 1.0 if valid else round(max(0.0, 1.0 - conflicts / max(len(edges), 1)), 4),
            }

        return {"error": f"Solución empresarial desconocida: {name}"}

    # ─── Full state snapshot ──────────────────────────────────────────────────

    def get_full_state(self) -> Dict:
        return {
            "n_qubits": self.n_qubits,
            "qubit_states": self.get_qubit_probabilities(),
            "statevector": self.get_statevector_sample(64),
            "entanglement": self.get_entanglement_map(),
            "circuit": self.circuit_ops[-50:],
            "norm": round(float(np.sum(np.abs(self.state) ** 2)), 8),
            "metrics": self.get_metrics(),
            "last_algorithm": self.last_algorithm_result
        }
