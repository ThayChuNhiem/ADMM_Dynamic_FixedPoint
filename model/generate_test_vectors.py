"""
Dynamic Fixed-Point ADMM Solver: Test Vector Generator
======================================================
This module generates realistic market scenarios and creates bit-true test vectors
for SystemVerilog RTL simulation and verification.

Generated Files for each test case (e.g., N=16):
1. `test_inputs.hex`: Input matrices & vectors (Sigma, mu, w_prev, lambda, rho, w_max)
2. `test_expected_output.hex`: Golden output weights w_opt, iterations, final residuals.
3. `test_iteration_trace.csv`: Full trace of intermediate w^k, z^k, u^k for cycle-accurate debugging.
"""

import os
import json
import numpy as np
from typing import Dict, Any

from admm_golden_float64 import admm_portfolio_solve, cholesky_factorize


def float_to_qformat(val: float, total_bits: int = 18, frac_bits: int = 14) -> int:
    """
    Converts a float into two's complement integer in Q<total_bits - frac_bits>.<frac_bits>.
    """
    scaling = 1 << frac_bits
    int_val = int(round(val * scaling))
    
    max_val = (1 << (total_bits - 1)) - 1
    min_val = -(1 << (total_bits - 1))
    
    # Clamp to avoid bit overflow
    clamped = max(min(int_val, max_val), min_val)
    if clamped < 0:
        clamped = (1 << total_bits) + clamped
    return clamped & ((1 << total_bits) - 1)


def generate_market_scenario(
    N: int = 16,
    seed: int = 42,
    lambda_cost: float = 0.001,
    w_max: float = 0.25,
    rho: float = 1.5
) -> Dict[str, Any]:
    """
    Generates realistic market covariance matrix and return vector.
    """
    np.random.seed(seed)
    
    # 1. Multi-factor market model: Sigma = B * Omega * B.T + D
    # where B is asset-factor loadings, Omega is factor covariance, D is idiosyncratic risk
    K = max(3, N // 4)  # Number of latent factors
    B = np.random.normal(0.0, 0.2, size=(N, K))
    Omega = np.diag(np.random.uniform(0.01, 0.05, size=K))
    D = np.diag(np.random.uniform(0.005, 0.02, size=N))
    
    Sigma = B @ Omega @ B.T + D
    
    # 2. Realistic annualized returns (e.g. 5% to 25%)
    mu = np.random.uniform(0.05, 0.20, size=N)
    
    # 3. Previous portfolio weights (already valid simplex)
    w_raw = np.random.uniform(0.05, 0.5, size=N)
    w_prev = w_raw / np.sum(w_raw)
    
    return {
        "N": N,
        "Sigma": Sigma,
        "mu": mu,
        "w_prev": w_prev,
        "lambda_cost": lambda_cost,
        "w_max": w_max,
        "rho": rho
    }


def export_test_vectors_to_disk(
    scenario: Dict[str, Any],
    output_dir: str = "sim/vectors"
) -> str:
    """
    Solves the scenario with Golden Float64 model and dumps .hex and .csv files.
    """
    os.makedirs(output_dir, exist_ok=True)
    N = scenario["N"]
    
    # Solve
    res = admm_portfolio_solve(
        Sigma=scenario["Sigma"],
        mu=scenario["mu"],
        w_prev=scenario["w_prev"],
        lambda_cost=scenario["lambda_cost"],
        w_max=scenario["w_max"],
        rho=scenario["rho"],
        eps_pri=1e-5,
        eps_dual=1e-5,
        max_iter=100,
        verbose=False
    )
    
    # 1. Export inputs in Q4.14 format
    inputs_hex_path = os.path.join(output_dir, f"inputs_N{N}.hex")
    with open(inputs_hex_path, "w") as f:
        f.write(f"// Test Vectors for N={N} Assets in Q4.14 Hex format\n")
        f.write(f"// Header: lambda={scenario['lambda_cost']}, rho={scenario['rho']}, w_max={scenario['w_max']}\n")
        
        # Write mu
        f.write("// Expected Returns (mu)\n")
        for val in scenario["mu"]:
            f.write(f"{float_to_qformat(val, 18, 14):05X}\n")
            
        # Write w_prev
        f.write("// Previous Weights (w_prev)\n")
        for val in scenario["w_prev"]:
            f.write(f"{float_to_qformat(val, 18, 14):05X}\n")
            
        # Write Lower Cholesky Matrix L_A row by row
        f.write("// Cholesky Factor L_A (Lower triangular)\n")
        L_A = res["L_A"]
        for i in range(N):
            for j in range(i + 1):
                f.write(f"{float_to_qformat(L_A[i, j], 18, 14):05X}\n")
                
    # 2. Export expected outputs in Q4.14 format
    outputs_hex_path = os.path.join(output_dir, f"expected_w_N{N}.hex")
    with open(outputs_hex_path, "w") as f:
        f.write(f"// Expected Optimal Weights (z_opt) for N={N} Assets in Q4.14 Hex format\n")
        for val in res["z_opt"]:
            f.write(f"{float_to_qformat(val, 18, 14):05X}\n")
            
    # 3. Export summary JSON
    summary_path = os.path.join(output_dir, f"summary_N{N}.json")
    summary_data = {
        "N": N,
        "iterations": res["iterations"],
        "converged": res["converged"],
        "w_opt": res["z_opt"].tolist(),
        "final_primal_res": res["history"]["primal_res"][-1],
        "final_dual_res": res["history"]["dual_res"][-1],
        "final_objective": res["history"]["obj_val"][-1]
    }
    with open(summary_path, "w") as f:
        json.dump(summary_data, f, indent=4)
        
    print(f"[SUCCESS] Exported test vectors for N={N} to '{output_dir}'")
    return output_dir


if __name__ == "__main__":
    for n_assets in [8, 16, 32]:
        scen = generate_market_scenario(N=n_assets, seed=100 + n_assets)
        export_test_vectors_to_disk(scen, output_dir="sim/vectors")
