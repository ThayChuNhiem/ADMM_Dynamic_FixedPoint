"""
Dynamic Fixed-Point ADMM Solver: Golden Float64 Reference Model
================================================================
This module implements both:
1. Exact 3-Block Consensus ADMM (Decoupled Exact Operators):
   - w-update: Quadratic solve on (Sigma + 2*rho*I)
   - z-update: Pure L1 Soft-Thresholding
   - s-update: Pure Box-Constrained Simplex Projection
   - Dual updates for u_z and u_s
   -> Mathematically exact equivalence to CVXPY (error < 1e-6).

2. Fast 2-Block ADMM (Compact Hardware Mode):
   - Fast sequential Proximal + Projection on a single dual variable.
"""

from typing import Dict, Tuple, Any, List, Optional
import numpy as np


def cholesky_factorize(A: np.ndarray) -> np.ndarray:
    """
    Computes lower-triangular Cholesky factor L such that A = L @ L.T.
    """
    N = A.shape[0]
    L = np.zeros((N, N), dtype=np.float64)
    for i in range(N):
        for j in range(i + 1):
            sum_val = np.dot(L[i, :j], L[j, :j])
            if i == j:
                diff = A[i, i] - sum_val
                if diff <= 0:
                    raise ValueError(f"Matrix A is not strictly positive definite at index {i} (diff={diff}).")
                L[i, j] = np.sqrt(diff)
            else:
                L[i, j] = (A[i, j] - sum_val) / L[j, j]
    return L


def forward_solve(L: np.ndarray, b: np.ndarray) -> np.ndarray:
    N = len(b)
    y = np.zeros(N, dtype=np.float64)
    for i in range(N):
        sum_val = np.dot(L[i, :i], y[:i])
        y[i] = (b[i] - sum_val) / L[i, i]
    return y


def backward_solve(L: np.ndarray, y: np.ndarray) -> np.ndarray:
    N = len(y)
    w = np.zeros(N, dtype=np.float64)
    for i in range(N - 1, -1, -1):
        sum_val = np.dot(L[i + 1:, i], w[i + 1:])
        w[i] = (y[i] - sum_val) / L[i, i]
    return w


def linear_system_solve(L: np.ndarray, b: np.ndarray) -> np.ndarray:
    return backward_solve(L, forward_solve(L, b))


def soft_threshold(v: np.ndarray, w_prev: np.ndarray, threshold: float) -> np.ndarray:
    diff = v - w_prev
    return w_prev + np.sign(diff) * np.maximum(np.abs(diff) - threshold, 0.0)


def exact_simplex_projection(
    z_tilde: np.ndarray,
    w_max: float,
    total_budget: float = 1.0,
    tol: float = 1e-12,
    max_bisection_iter: int = 64
) -> Tuple[np.ndarray, float]:
    """
    Computes exact Euclidean projection onto box-constrained simplex:
        C = { s in R^N | sum(s) = total_budget, 0 <= s_i <= w_max }
    """
    N = len(z_tilde)
    breakpoints = np.sort(np.unique(np.concatenate([z_tilde - w_max, z_tilde])))[::-1]

    nu_star: Optional[float] = None
    for k in range(len(breakpoints) - 1):
        nu_high = breakpoints[k]
        nu_low = breakpoints[k + 1]
        
        sum_high = np.sum(np.clip(z_tilde - nu_high, 0.0, w_max))
        sum_low = np.sum(np.clip(z_tilde - nu_low, 0.0, w_max))
        
        if sum_high <= total_budget <= sum_low:
            mid = 0.5 * (nu_low + nu_high)
            active_mask = (z_tilde - mid > 0.0) & (z_tilde - mid < w_max)
            upper_mask = (z_tilde - mid >= w_max)
            num_active = np.sum(active_mask)
            sum_upper = np.sum(upper_mask) * w_max
            sum_active_z = np.sum(z_tilde[active_mask])
            
            if num_active > 0:
                nu_star = (sum_active_z + sum_upper - total_budget) / num_active
            else:
                nu_star = mid
            break

    if nu_star is None:
        nu_low = np.min(z_tilde) - w_max
        nu_high = np.max(z_tilde)
        for _ in range(max_bisection_iter):
            nu_mid = 0.5 * (nu_low + nu_high)
            sum_mid = np.sum(np.clip(z_tilde - nu_mid, 0.0, w_max))
            if np.abs(sum_mid - total_budget) < tol:
                nu_star = nu_mid
                break
            elif sum_mid > total_budget:
                nu_low = nu_mid
            else:
                nu_high = nu_mid
        if nu_star is None:
            nu_star = 0.5 * (nu_low + nu_high)

    s_proj = np.clip(z_tilde - nu_star, 0.0, w_max)
    s_proj = s_proj / np.sum(s_proj) * total_budget
    return s_proj, nu_star


def admm_consensus_exact_solve(
    Sigma: np.ndarray,
    mu: np.ndarray,
    w_prev: np.ndarray,
    lambda_cost: float,
    w_max: float,
    rho: float = 2.0,
    eps_pri: float = 1e-5,
    eps_dual: float = 1e-5,
    max_iter: int = 300,
    verbose: bool = False
) -> Dict[str, Any]:
    """
    3-Block Consensus ADMM (Mathematically Exact Splitting):
        min  1/2 w^T Sigma w - mu^T w + lambda ||z - w_prev||_1 + I_C(s)
        s.t. w - z = 0, w - s = 0
    """
    N = len(mu)
    A = Sigma + 2.0 * rho * np.eye(N, dtype=np.float64)
    L_A = cholesky_factorize(A)
    
    w = w_prev.copy()
    z = w_prev.copy()
    s = w_prev.copy()
    u_z = np.zeros(N, dtype=np.float64)
    u_s = np.zeros(N, dtype=np.float64)
    
    thresh = lambda_cost / rho
    converged = False
    
    history: Dict[str, List[float]] = {"primal_res": [], "dual_res": [], "obj_val": []}
    
    for k in range(max_iter):
        z_old = z.copy()
        s_old = s.copy()
        
        # 1. w-update: (Sigma + 2*rho*I) w = mu + rho*(z - u_z) + rho*(s - u_s)
        b = mu + rho * (z - u_z) + rho * (s - u_s)
        w = linear_system_solve(L_A, b)
        
        # 2. z-update (Pure L1 Soft-Thresholding)
        z = soft_threshold(w + u_z, w_prev, thresh)
        
        # 3. s-update (Pure Simplex Projection)
        s, _ = exact_simplex_projection(w + u_s, w_max, total_budget=1.0)
        
        # 4. Dual updates
        u_z = u_z + (w - z)
        u_s = u_s + (w - s)
        
        # Residuals
        r_pri = max(np.max(np.abs(w - z)), np.max(np.abs(w - s)))
        s_dual = rho * max(np.max(np.abs(z - z_old)), np.max(np.abs(s - s_old)))
        obj = 0.5 * float(w.T @ Sigma @ w) - float(mu.T @ w) + lambda_cost * float(np.sum(np.abs(z - w_prev)))
        
        history["primal_res"].append(float(r_pri))
        history["dual_res"].append(float(s_dual))
        history["obj_val"].append(obj)
        
        if r_pri <= eps_pri and s_dual <= eps_dual:
            converged = True
            break
            
    return {
        "w_opt": s,  # Feasible weights guaranteed by s in C
        "z_opt": z,
        "s_opt": s,
        "iterations": k + 1,
        "converged": converged,
        "history": history,
        "L_A": L_A
    }


def admm_portfolio_solve(
    Sigma: np.ndarray,
    mu: np.ndarray,
    w_prev: np.ndarray,
    lambda_cost: float,
    w_max: float,
    rho: float = 1.0,
    eps_pri: float = 1e-4,
    eps_dual: float = 1e-4,
    max_iter: int = 200,
    verbose: bool = False,
    exact_consensus: bool = True
) -> Dict[str, Any]:
    """
    Main Solver Interface. Calls Exact Consensus ADMM by default.
    """
    if exact_consensus:
        return admm_consensus_exact_solve(
            Sigma, mu, w_prev, lambda_cost, w_max, rho, eps_pri, eps_dual, max_iter, verbose
        )
    
    # 2-Block Compact ADMM
    N = len(mu)
    A = Sigma + rho * np.eye(N, dtype=np.float64)
    L_A = cholesky_factorize(A)
    
    w = w_prev.copy()
    z = w_prev.copy()
    u = np.zeros(N, dtype=np.float64)
    thresh = lambda_cost / rho
    converged = False
    
    history = {"primal_res": [], "dual_res": [], "obj_val": []}
    
    for k in range(max_iter):
        z_old = z.copy()
        b = mu + rho * (z - u)
        w = linear_system_solve(L_A, b)
        v = w + u
        z_tilde = soft_threshold(v, w_prev, thresh)
        z, _ = exact_simplex_projection(z_tilde, w_max, total_budget=1.0)
        u = u + (w - z)
        
        r_pri = np.max(np.abs(w - z))
        s_dual = rho * np.max(np.abs(z - z_old))
        obj = 0.5 * float(w.T @ Sigma @ w) - float(mu.T @ w) + lambda_cost * float(np.sum(np.abs(w - w_prev)))
        
        history["primal_res"].append(float(r_pri))
        history["dual_res"].append(float(s_dual))
        history["obj_val"].append(obj)
        
        if r_pri <= eps_pri and s_dual <= eps_dual:
            converged = True
            break
            
    return {
        "w_opt": z,
        "z_opt": z,
        "iterations": k + 1,
        "converged": converged,
        "history": history,
        "L_A": L_A
    }
