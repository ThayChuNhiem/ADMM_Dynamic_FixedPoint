"""
Unit & Integration Test Suite for ADMM Golden & Fixed-Point Models
==================================================================
Tests:
1. Cholesky factorization & Forward/Backward Triangular Solvers.
2. Soft-Thresholding L1 Proximal Operator.
3. Exact Simplex Water-Filling Projection.
4. Float64 ADMM Solver Convergence.
5. Bit-True Fixed-Point Model Accuracy vs. Float64.
6. Mathematical Equivalence against CVXPY.
7. Hex Test Vector Generation Validity.
"""

import os
import sys
import numpy as np
import pytest

# Add model path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "model")))

from admm_golden_float64 import (
    cholesky_factorize,
    forward_solve,
    backward_solve,
    linear_system_solve,
    soft_threshold,
    exact_simplex_projection,
    admm_portfolio_solve
)
from admm_fixedpoint import (
    FixedPointQuantizer,
    admm_fixedpoint_solve
)
from verify_with_cvxpy import solve_with_cvxpy
from generate_test_vectors import (
    generate_market_scenario,
    export_test_vectors_to_disk,
    float_to_qformat
)


def test_cholesky_and_triangular_solves():
    """Verify handcrafted Cholesky & triangular solvers against NumPy np.linalg.solve."""
    np.random.seed(101)
    N = 16
    F = np.random.randn(N, N)
    A = F @ F.T + np.eye(N)
    b = np.random.randn(N)
    
    # Custom solver
    L = cholesky_factorize(A)
    assert np.allclose(L @ L.T, A, atol=1e-10), "Cholesky factor L @ L.T does not reconstruct A!"
    
    y = forward_solve(L, b)
    assert np.allclose(L @ y, b, atol=1e-10), "Forward solve L y = b failed!"
    
    w = backward_solve(L, y)
    assert np.allclose(L.T @ w, y, atol=1e-10), "Backward solve L.T w = y failed!"
    
    # Check against NumPy direct solve
    w_np = np.linalg.solve(A, b)
    assert np.allclose(w, w_np, atol=1e-10), "Custom Cholesky solve deviates from np.linalg.solve!"


def test_soft_thresholding():
    """Verify L1 Soft-Thresholding operator behavior and sparsity generation."""
    w_prev = np.array([0.1, 0.2, 0.3])
    v = np.array([0.105, 0.15, 0.4])  # diff: [+0.005, -0.05, +0.1]
    threshold = 0.01
    
    z_tilde = soft_threshold(v, w_prev, threshold)
    # Asset 0: |diff| = 0.005 <= 0.01 -> clamped to w_prev[0] = 0.1
    assert np.isclose(z_tilde[0], 0.1), "Small difference below threshold was not shrunk to w_prev!"
    # Asset 1: diff = -0.05 -> shrunk towards 0.2 by 0.01 -> 0.2 - 0.04 = 0.16
    assert np.isclose(z_tilde[1], 0.16), "Negative shrink failed!"
    # Asset 2: diff = +0.10 -> shrunk towards 0.3 by 0.01 -> 0.3 + 0.09 = 0.39
    assert np.isclose(z_tilde[2], 0.39), "Positive shrink failed!"


def test_simplex_projection():
    """Verify box-constrained simplex projection satisfies sum(w)=1 and 0 <= w_i <= w_max."""
    np.random.seed(202)
    N = 16
    w_max = 0.20
    
    for _ in range(20):
        z_raw = np.random.randn(N)
        z_proj, nu_star = exact_simplex_projection(z_raw, w_max=w_max, total_budget=1.0)
        
        # 1. Budget conservation
        assert np.isclose(np.sum(z_proj), 1.0, atol=1e-7), f"Sum of weights = {np.sum(z_proj)} != 1.0"
        # 2. Lower bound
        assert np.all(z_proj >= -1e-8), f"Negative weight detected: min={np.min(z_proj)}"
        # 3. Upper bound
        assert np.all(z_proj <= w_max + 1e-8), f"Weight exceeded w_max: max={np.max(z_proj)} > {w_max}"


def test_float64_solver_convergence():
    """Verify ADMM Float64 solver converges and satisfies all KKT conditions."""
    scen = generate_market_scenario(N=16, seed=303, lambda_cost=0.001, w_max=0.25, rho=1.0)
    res = admm_portfolio_solve(
        Sigma=scen["Sigma"],
        mu=scen["mu"],
        w_prev=scen["w_prev"],
        lambda_cost=scen["lambda_cost"],
        w_max=scen["w_max"],
        rho=scen["rho"],
        eps_pri=1e-4,
        eps_dual=1e-4,
        max_iter=300,
        exact_consensus=True
    )
    assert res["converged"], f"Solver failed to converge in {res['iterations']} iterations!"
    assert np.isclose(np.sum(res["w_opt"]), 1.0, atol=1e-4), "Optimal weights do not sum to 1.0!"
    assert np.all(res["w_opt"] >= -1e-5), "Optimal weights contain negative values!"
    assert np.all(res["w_opt"] <= scen["w_max"] + 1e-5), "Optimal weights exceed w_max!"


def test_fixedpoint_bit_true_vs_float64():
    """Verify Q4.14 Fixed-Point model matches Float64 model with error < 0.5%."""
    scen = generate_market_scenario(N=16, seed=404, lambda_cost=0.001, w_max=0.25, rho=1.5)
    
    # Float64
    fl_res = admm_portfolio_solve(
        Sigma=scen["Sigma"], mu=scen["mu"], w_prev=scen["w_prev"],
        lambda_cost=scen["lambda_cost"], w_max=scen["w_max"], rho=scen["rho"],
        eps_pri=1e-4, eps_dual=1e-4, max_iter=200, exact_consensus=False
    )
    
    # Fixed-Point Q4.14
    fx_res = admm_fixedpoint_solve(
        Sigma=scen["Sigma"], mu=scen["mu"], w_prev=scen["w_prev"],
        lambda_cost=scen["lambda_cost"], w_max=scen["w_max"], rho=scen["rho"],
        frac_bits_data=14, frac_bits_dual=20, max_iter=200, eps_pri=1e-4, eps_dual=1e-4
    )
    
    max_err = np.max(np.abs(fl_res["z_opt"] - fx_res["z_opt"]))
    assert max_err < 0.005, f"Fixed-Point error {max_err} exceeded budget 0.005 (0.5%)!"
    assert np.isclose(np.sum(fx_res["z_opt"]), 1.0, atol=1e-3), "Fixed-point weights do not sum to 1.0!"


def test_cvxpy_equivalence():
    """Verify our ADMM solver matches CVXPY optimal objective and weights."""
    scen = generate_market_scenario(N=16, seed=505, lambda_cost=0.001, w_max=0.25, rho=0.5)
    
    cvx_res = solve_with_cvxpy(
        Sigma=scen["Sigma"], mu=scen["mu"], w_prev=scen["w_prev"],
        lambda_cost=scen["lambda_cost"], w_max=scen["w_max"]
    )
    
    admm_res = admm_portfolio_solve(
        Sigma=scen["Sigma"], mu=scen["mu"], w_prev=scen["w_prev"],
        lambda_cost=scen["lambda_cost"], w_max=scen["w_max"], rho=scen["rho"],
        eps_pri=1e-5, eps_dual=1e-5, max_iter=500, exact_consensus=True
    )
    
    diff_w = np.max(np.abs(admm_res["w_opt"] - cvx_res["w_opt"]))
    obj_admm = admm_res["history"]["obj_val"][-1]
    obj_cvx = cvx_res["obj_val"]
    rel_obj_gap = np.abs(obj_admm - obj_cvx) / (np.abs(obj_cvx) + 1e-9)
    
    assert diff_w < 5e-3, f"Weight deviation from CVXPY: {diff_w} >= 5e-3"
    assert rel_obj_gap < 1e-4, f"Objective gap from CVXPY: {rel_obj_gap} >= 1e-4"


def test_vector_generator_and_hex_conversion():
    """Verify Q4.14 hex conversions and export functions."""
    # Test hex conversion
    val = 0.5  # In Q4.14: 0.5 * 2^14 = 8192 = 0x2000
    hex_val = float_to_qformat(val, total_bits=18, frac_bits=14)
    assert hex_val == 8192, f"Q4.14 conversion failed for 0.5: got {hex_val}"
    
    val_neg = -0.5 # In 18-bit two's complement: 2^18 - 8192 = 262144 - 8192 = 253952 = 0x3E000
    hex_neg = float_to_qformat(val_neg, total_bits=18, frac_bits=14)
    assert hex_neg == 253952, f"Q4.14 negative conversion failed for -0.5: got {hex_neg}"
    
    # Test generation and file existence
    scen = generate_market_scenario(N=8, seed=606)
    out_dir = export_test_vectors_to_disk(scen, output_dir="sim/vectors_test")
    assert os.path.exists(os.path.join(out_dir, "inputs_N8.hex"))
    assert os.path.exists(os.path.join(out_dir, "expected_w_N8.hex"))
    assert os.path.exists(os.path.join(out_dir, "summary_N8.json"))
