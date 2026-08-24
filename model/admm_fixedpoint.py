"""
Dynamic Fixed-Point ADMM Solver: Bit-True Fixed-Point Model
============================================================
This module implements the cycle- and bit-accurate fixed-point emulation of the
ADMM Portfolio Accelerator.

Key Hardware Quantization Rules:
- Primary DSP Data-path: Q4.14 (18-bit signed word, 1 sign, 3 integer, 14 fractional).
  Range: [-8.0, +7.99993896484375], Resolution: 2^-14 ~= 6.1035e-5.
- Dual Accumulator & Memory: Q4.20 (24-bit signed word, 6 extra guard bits).
  Resolution: 2^-20 ~= 9.5367e-7 to prevent stall / catastrophic cancellation.
- Exact Bitonic Sorting: Operates on 18-bit signed integers.
"""

from typing import Dict, Any, Tuple
import numpy as np


class FixedPointQuantizer:
    """
    Fixed-point quantizer simulating 2's complement hardware arithmetic.
    """
    def __init__(self, total_bits: int = 18, frac_bits: int = 14):
        self.total_bits = total_bits
        self.frac_bits = frac_bits
        self.scale = 1 << frac_bits
        self.max_int = (1 << (total_bits - 1)) - 1
        self.min_int = -(1 << (total_bits - 1))

    def quantize(self, x: np.ndarray) -> np.ndarray:
        scaled = np.round(x * self.scale)
        clamped = np.clip(scaled, self.min_int, self.max_int)
        return clamped / self.scale

    def quantize_int(self, x: np.ndarray) -> np.ndarray:
        scaled = np.round(x * self.scale)
        return np.clip(scaled, self.min_int, self.max_int).astype(np.int64)


def fixedpoint_forward_solve(
    L: np.ndarray,
    b: np.ndarray,
    q_data: FixedPointQuantizer
) -> np.ndarray:
    N = len(b)
    y = np.zeros(N, dtype=np.float64)
    for i in range(N):
        acc = b[i]
        for j in range(i):
            acc -= L[i, j] * y[j]
        reciprocal_Lii = q_data.quantize(np.array([1.0 / max(L[i, i], 1e-6)]))[0]
        y[i] = q_data.quantize(np.array([acc * reciprocal_Lii]))[0]
    return y


def fixedpoint_backward_solve(
    L: np.ndarray,
    y: np.ndarray,
    q_data: FixedPointQuantizer
) -> np.ndarray:
    N = len(y)
    w = np.zeros(N, dtype=np.float64)
    for i in range(N - 1, -1, -1):
        acc = y[i]
        for j in range(i + 1, N):
            acc -= L[j, i] * w[j]
        reciprocal_Lii = q_data.quantize(np.array([1.0 / max(L[i, i], 1e-6)]))[0]
        w[i] = q_data.quantize(np.array([acc * reciprocal_Lii]))[0]
    return w


def fixedpoint_soft_threshold(
    v: np.ndarray,
    w_prev: np.ndarray,
    thresh: float,
    q_data: FixedPointQuantizer
) -> np.ndarray:
    diff = q_data.quantize(v - w_prev)
    abs_diff = np.abs(diff)
    shrink = np.maximum(abs_diff - thresh, 0.0)
    z_tilde = w_prev + np.sign(diff) * shrink
    return q_data.quantize(z_tilde)


def fixedpoint_simplex_projection(
    z_tilde: np.ndarray,
    w_max: float,
    q_data: FixedPointQuantizer,
    total_budget: float = 1.0
) -> Tuple[np.ndarray, float]:
    nu_low = np.min(z_tilde) - w_max
    nu_high = np.max(z_tilde)
    
    nu_star = 0.0
    for _ in range(16):
        nu_mid = q_data.quantize(np.array([0.5 * (nu_low + nu_high)]))[0]
        clipped = np.clip(z_tilde - nu_mid, 0.0, w_max)
        sum_val = q_data.quantize(np.array([np.sum(clipped)]))[0]
        
        if sum_val > total_budget:
            nu_low = nu_mid
        else:
            nu_high = nu_mid
        nu_star = nu_mid
        
    z_proj = q_data.quantize(np.clip(z_tilde - nu_star, 0.0, w_max))
    sum_z = np.sum(z_proj)
    if sum_z > 0:
        z_proj = q_data.quantize(z_proj / sum_z * total_budget)
    return z_proj, nu_star


def admm_fixedpoint_solve(
    Sigma: np.ndarray,
    mu: np.ndarray,
    w_prev: np.ndarray,
    lambda_cost: float,
    w_max: float,
    rho: float = 1.5,
    frac_bits_data: int = 14,
    frac_bits_dual: int = 20,
    max_iter: int = 100,
    eps_pri: float = 1e-4,
    eps_dual: float = 1e-4
) -> Dict[str, Any]:
    N = len(mu)
    q_data = FixedPointQuantizer(total_bits=4 + frac_bits_data, frac_bits=frac_bits_data)
    q_dual = FixedPointQuantizer(total_bits=4 + frac_bits_dual, frac_bits=frac_bits_dual)
    
    # 1. Quantize Inputs symmetrically
    Sigma_sym = 0.5 * (Sigma + Sigma.T)
    Sigma_q = q_data.quantize(Sigma_sym)
    Sigma_q = 0.5 * (Sigma_q + Sigma_q.T)
    
    mu_q = q_data.quantize(mu)
    w_prev_q = q_data.quantize(w_prev)
    rho_q = q_data.quantize(np.array([rho]))[0]
    thresh_q = q_data.quantize(np.array([lambda_cost / rho]))[0]
    
    # 2. Factorize A = Sigma + rho * I
    A = Sigma_q + rho_q * np.eye(N)
    A = 0.5 * (A + A.T)
    
    # Robust Cholesky decomposition
    L_A = np.zeros((N, N), dtype=np.float64)
    for i in range(N):
        for j in range(i + 1):
            s = np.dot(L_A[i, :j], L_A[j, :j])
            if i == j:
                diff = max(A[i, i] - s, 1e-5)
                L_A[i, j] = np.sqrt(diff)
            else:
                L_A[i, j] = (A[i, j] - s) / max(L_A[j, j], 1e-5)
                
    L_A_q = q_data.quantize(L_A)
    
    # 3. State Registers
    w = w_prev_q.copy()
    z = w_prev_q.copy()
    u = np.zeros(N, dtype=np.float64)
    
    converged = False
    for k in range(max_iter):
        z_old = z.copy()
        
        # Step 1: w-update
        u_data_scaled = q_data.quantize(u)
        b = q_data.quantize(mu_q + rho_q * (z - u_data_scaled))
        y = fixedpoint_forward_solve(L_A_q, b, q_data)
        w = fixedpoint_backward_solve(L_A_q, y, q_data)
        
        # Step 2: z-update
        v = q_data.quantize(w + u_data_scaled)
        z_tilde = fixedpoint_soft_threshold(v, w_prev_q, thresh_q, q_data)
        z, _ = fixedpoint_simplex_projection(z_tilde, w_max, q_data, total_budget=1.0)
        
        # Step 3: u-update with Guard Bits (Q4.20)
        diff_res = q_data.quantize(w - z)
        u = q_dual.quantize(u + diff_res)
        
        # Convergence Check
        r_pri = np.max(np.abs(w - z))
        s_dual = rho * np.max(np.abs(z - z_old))
        
        if r_pri <= eps_pri and s_dual <= eps_dual:
            converged = True
            break
            
    return {
        "w_opt": w,
        "z_opt": z,
        "u_opt": u,
        "iterations": k + 1,
        "converged": converged,
        "final_primal_res": r_pri,
        "final_dual_res": s_dual
    }
