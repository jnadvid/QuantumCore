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
        elif gn == "SWAP": self._apply_swap(qubits[0], qubits[1])
        elif gn in ("CCX","TOFFOLI"): self._apply_toffoli(qubits[0], qubits[1], qubits[2])
        elif gn == "CSWAP":
            self._apply_controlled_gate(GATES["X"], qubits[1], qubits[2])
            self._apply_toffoli(qubits[0], qubits[2], qubits[1])
            self._apply_controlled_gate(GATES["X"], qubits[1], qubits[2])
        else:
            raise ValueError(f"Unknown gate: {gate_name}")

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

    def get_entanglement_map(self) -> List[List[float]]:
        n = self.n_qubits
        limit = min(n, 12)
        emap = [[0.0] * limit for _ in range(limit)]
        bloch_cache = {i: self._bloch_vector(i) for i in range(limit)}
        for i in range(limit):
            bi = bloch_cache[i]
            ri = math.sqrt(bi["x"]**2 + bi["y"]**2 + bi["z"]**2)
            for j in range(i + 1, limit):
                bj = bloch_cache[j]
                rj = math.sqrt(bj["x"]**2 + bj["y"]**2 + bj["z"]**2)
                dot = bi["x"]*bj["x"] + bi["y"]*bj["y"] + bi["z"]*bj["z"]
                ent = round(max(0.0, min(1.0, 1.0 - ri * rj + abs(dot) * 0.3)), 4)
                emap[i][j] = ent
                emap[j][i] = ent
        return emap

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

        return {"error": f"Algoritmo desconocido: {name}"}

    # ─── Full state snapshot ──────────────────────────────────────────────────

    def get_full_state(self) -> Dict:
        return {
            "n_qubits": self.n_qubits,
            "qubit_states": self.get_qubit_probabilities(),
            "statevector": self.get_statevector_sample(64),
            "entanglement": self.get_entanglement_map(),
            "circuit": self.circuit_ops[-50:],
            "norm": round(float(np.sum(np.abs(self.state) ** 2)), 8),
            "last_algorithm": self.last_algorithm_result
        }
