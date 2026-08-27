# Dynamic Fixed-Point ADMM Solver for Real-Time Portfolio Optimization

[![Target Platform](https://img.shields.io/badge/Platform-AMD%20Xilinx%20Kria%20KV260%20(ZU5EV)-orange.svg)](https://www.xilinx.com/products/som/kria/kv260-vision-starter-kit.html)
[![Language](https://img.shields.io/badge/Language-SystemVerilog%20%7C%20Python%20%7C%20C%2B%2B-blue.svg)]()
[![License](https://img.shields.io/badge/License-MIT-green.svg)]()

A high-throughput, deterministic ultra-low latency hardware accelerator for **Convex Quadratic Programming with $\ell_1$ Regularization and Simplex Constraints** via the **Alternating Direction Method of Multipliers (ADMM)**, implemented in native SystemVerilog on the **AMD Xilinx Kria KV260 (Zynq UltraScale+ XCZU5EV)**.

---

## 📌 1. Mathematical Formulation

### 1.1. Portfolio Optimization with $\ell_1$ Transaction Costs
The real-time portfolio rebalancing problem with transaction costs $\lambda$ and simplex/box constraints $\mathcal{C} = \{w \in \mathbb{R}^N \mid \mathbf{1}^T w = 1, \; 0 \le w \le w_{\max}\}$ is formulated as:

$$\min_{w \in \mathbb{R}^N} \frac{1}{2} w^T \Sigma w - \mu^T w + \lambda \|w - w_{\text{prev}}\|_1 + \mathbb{I}_{\mathcal{C}}(w)$$

Where:
* $\Sigma \in \mathbb{S}_{++}^N$: Asset covariance matrix (symmetric positive definite).
* $\mu \in \mathbb{R}^N$: Expected return vector.
* $w_{\text{prev}} \in \mathbb{R}^N$: Portfolio weights held from previous tick.
* $\lambda > 0$: $\ell_1$ transaction penalty parameter.
* $\mathbb{I}_{\mathcal{C}}(w)$: Indicator function ($\mathbb{I}_{\mathcal{C}}(w) = 0$ if $w \in \mathcal{C}$, and $+\infty$ otherwise).

---

### 1.2. ADMM Variable Splitting & Scaled Augmented Lagrangian
We introduce auxiliary variable $z \in \mathbb{R}^N$ to decouple the smooth quadratic objective $f(w)$ from the non-smooth/constrained objective $g(z)$:

$$\min_{w, z \in \mathbb{R}^N} f(w) + g(z) \quad \text{s.t.} \quad w - z = 0$$

$$f(w) = \frac{1}{2} w^T \Sigma w - \mu^T w, \qquad g(z) = \lambda \|z - w_{\text{prev}}\|_1 + \mathbb{I}_{\mathcal{C}}(z)$$

The Scaled Augmented Lagrangian with penalty parameter $\rho > 0$ and scaled dual variable $u = \frac{1}{\rho} y$ is:

$$\mathcal{L}_\rho(w, z, u) = f(w) + g(z) + \frac{\rho}{2} \|w - z + u\|_2^2 - \frac{\rho}{2} \|u\|_2^2$$

---

### 1.3. Three-Step ADMM Closed-Form Updates (Iteration $k+1$)

```
    ┌────────────────────────────────────────────────────────┐
    │ 1. w-update (Quadratic Step via Cholesky)              │
    │    (Σ + ρI) w^(k+1) = μ + ρ(z^k - u^k)                 │
    └───────────────────────────┬────────────────────────────┘
                                │
                                ▼
    ┌────────────────────────────────────────────────────────┐
    │ 2. z-update (Proximal Soft-Thresholding + Simplex Proj)│
    │    v = w^(k+1) + u^k                                   │
    │    z^(k+1) = Proj_C ( w_prev + SoftThresh(v-w_prev, λ/ρ)│
    └───────────────────────────┬────────────────────────────┘
                                │
                                ▼
    ┌────────────────────────────────────────────────────────┐
    │ 3. u-update (Dual Accumulation)                        │
    │    u^(k+1) = u^k + (w^(k+1) - z^(k+1))                 │
    └────────────────────────────────────────────────────────┘
```

#### Step 1: $w$-Update (Linear System Solve via Cholesky)
$$(\Sigma + \rho I_N) w^{k+1} = \mu + \rho (z^k - u^k)$$
Let $A = \Sigma + \rho I_N$. Pre-compute or rank-1 update Cholesky decomposition $A = L_A L_A^T$, then solve:
1. **Forward Substitution:** $L_A y = \mu + \rho (z^k - u^k)$
2. **Backward Substitution:** $L_A^T w^{k+1} = y$

#### Step 2: $z$-Update (Soft-Thresholding & Exact Simplex Projection)
Let $v = w^{k+1} + u^k$:
1. **Soft-Thresholding:**
   $$\tilde{z}_i = w_{\text{prev}, i} + \text{sign}(v_i - w_{\text{prev}, i}) \cdot \max\left(|v_i - w_{\text{prev}, i}| - \frac{\lambda}{\rho}, \, 0\right)$$
2. **Exact Box-Constrained Simplex Projection ($\Pi_{\mathcal{C}}$):**
   $$z_i^{k+1} = \text{clip}(\tilde{z}_i - \nu^*, \; 0, \; w_{\max}) \quad \text{s.t.} \quad \sum_{i=1}^N \text{clip}(\tilde{z}_i - \nu^*, \; 0, \; w_{\max}) = 1$$
   *(Calculated via Pipelined Bitonic Sorting & Water-Filling for Lagrange multiplier $\nu^*$).*

#### Step 3: $u$-Update (Dual Integrator)
$$u^{k+1} = u^k + (w^{k+1} - z^{k+1})$$

#### Convergence Check (KKT Stopping Criteria)
$$\|w^{k+1} - z^{k+1}\|_\infty \le \epsilon_{\text{pri}} \quad \text{and} \quad \rho \|z^{k+1} - z^k\|_\infty \le \epsilon_{\text{dual}}$$

---

## 🏗️ 2. Hardware Architecture on FPGA (Xilinx KV260)

```
                       AXI4-Stream Slave (Inputs: mu, z_prev, price tick)
                                         │
                                         ▼
                     ┌───────────────────────────────────────┐
                     │       Rank-1 Cholesky Updater         │
                     │         (Givens Rotations)            │
                     └───────────────────┬───────────────────┘
                                         │ L_A
                                         ▼
 ┌─────────────────────────────────────────────────────────────────────────────────┐
 │                                ADMM CORE PIPELINE                               │
 │                                                                                 │
 │   ┌───────────────────────┐        ┌──────────────────────────────────────┐     │
 │   │ 1D Systolic Array     │ w^(k+1)│ SIMD Soft-Thresholding Core          │     │
 │   │ (Forward/Backward Sol)├───────►│ + Pipelined Bitonic Simplex Engine   │     │
 │   └───────────────────────┘        └──────────────────┬───────────────────┘     │
 │               ▲                                       │ z^(k+1)                 │
 │               │                                       ▼                         │
 │               │ u^(k+1)            ┌──────────────────────────────────────┐     │
 │               └────────────────────┤ Dual Accumulator (Q4.20 with Guard)  │     │
 │                                    └──────────────────┬───────────────────┘     │
 │                                                       │ r^(k+1), s^(k+1)        │
 │                                                       ▼                         │
 │                                    ┌──────────────────────────────────────┐     │
 │                                    │ Pipelined Convergence Tree (Norm-inf)│     │
 │                                    └──────────────────────────────────────┘     │
 └───────────────────────────────────────────────────────┬─────────────────────────┘
                                                         │
                                                         ▼
                       AXI4-Stream Master (Output: w_optimal, iter_count)
```

### Key Hardware Blocks
1. **Folded 1D Systolic Array (DSP48E2 MAC Engines):**
   * Computes Forward/Backward triangular substitution in $\mathcal{O}(N^2 / P)$ cycles without stalling.
2. **Pipelined Bitonic Sorting & Water-Filling Network:**
   * Fully pipelined sorting network with latency $\mathcal{O}(\log_2^2 N)$ and throughput of 1 vector/cycle for finding exact $\nu^*$.
3. **Dual-Scale Fixed-Point Arithmetic:**
   * Data path: `Q4.14` (18-bit signed) matching DSP48E2 pre-adders/multipliers.
   * Accumulator & Dual variable $u$: `Q4.20` (24-bit signed) with 6 guard bits to prevent catastrophic cancellation.
4. **Rank-1 Givens Rotation Unit:**
   * Updates Cholesky factor $L_{A, \text{new}} L_{A, \text{new}}^T = L_{A, \text{old}} L_{A, \text{old}}^T + r_t r_t^T$ in $\mathcal{O}(N^2)$ without full matrix refactorization.

---

## 📁 3. Repository Structure

```text
ADMM_Dynamic_FixedPoint/
├── docs/                      # Architectural specs, math proofs, fixed-point error budget
│   └── NOVELTIES_AND_COMPARISON.md # Scientific novelties & state-of-the-art paper comparison
├── rtl/                       # SystemVerilog RTL Source Files
│   ├── admm_top.sv            # Top-level IP Core with AXI4-Stream
│   ├── cholesky_solver.sv     # 1D Systolic Array for triangular solve
│   ├── cholesky_rank1_up.sv   # Givens rotation rank-1 updater
│   ├── soft_threshold.sv      # SIMD L1 proximal operator
│   ├── bitonic_sort.sv        # Pipelined bitonic sorting network
│   ├── water_filling.sv       # Exact box-constrained simplex projection
│   ├── dual_accumulator.sv    # Guard-bit dual integrator
│   └── convergence_check.sv   # Pipelined Infinity-norm comparator
├── sim/                       # Simulation testbenches & verification
│   ├── tb_admm_top.sv         # UVM/SystemVerilog top testbench
│   └── run_sim.py             # Automated simulation runner (Questa/Vivado xsim)
├── model/                     # Python Golden Models & Quantization Analysis
│   ├── admm_golden_float64.py # Exact floating-point solver (NumPy/CVXPY)
│   ├── admm_fixedpoint.py     # Bit-true dynamic fixed-point model (FixedPoint library)
│   └── quant_error_sweep.py   # SNR vs bit-width sweep & convergence proof
├── tests/                     # Automated unit and regression test suite
│   └── test_admm_models.py    # 7/7 automated pytest suite
├── syn/                       # Vivado TCL Scripts & Synthesis Constraints
│   ├── build_ip.tcl           # Package RTL as Vivado IP
│   ├── create_kv260_bd.tcl    # Block Design script for Zynq MPSoC
│   └── constraints_kv260.xdc  # Timing & Pin constraints for KV260
├── sw/                        # Embedded Linux / PYNQ Driver for KV260
│   ├── driver.py              # PYNQ DMA interface script
│   └── benchmark.py           # Real-time tick latency benchmark
├── README.md
└── ROADMAP.md
```

---

## 🚀 4. Quick Start Guide

### Prerequisites
* **Vivado ML Standard / Enterprise:** 2022.2 or newer.
* **Target Board:** Kria KV260 Vision Starter Kit (`xck26-sfvc784-2LV-c`).
* **Python Environment:** Python 3.9+ (`numpy`, `scipy`, `cvxpy`, `bitstring`, `pynq`).

### Running Golden Model & Fixed-Point Verification
```bash
# 1. Run float64 reference vs fixed-point bit-true simulation
python model/admm_fixedpoint.py --num_assets 16 --fractional_bits 14

# 2. Sweep error budget across portfolio sizes N = 8, 16, 32
python model/quant_error_sweep.py
```

### Running RTL Simulation
```bash
# Run Vivado xsim testbench
cd sim
vivado -mode batch -source run_sim.tcl
```

### Building Vivado Project for KV260
```bash
cd syn
vivado -mode batch -source build_ip.tcl
vivado -mode batch -source create_kv260_bd.tcl
```

---

## 📊 5. Target Specifications on Kria KV260 (N = 16 Assets)

| Metric | Specification | Note |
| :--- | :--- | :--- |
| **Clock Frequency ($f_{\max}$)** | **250 MHz – 300 MHz** | Clean timing closure on UltraScale+ |
| **Tick-to-Trade Latency** | **$< 1.8 \; \mu\text{s}$** | ~400 clock cycles at 250 MHz |
| **Resource Utilization** | **$< 20\%$ LUTs, $< 8\%$ DSPs** | Fits easily on ZU5EV MPSoC |
| **Quantization Precision** | **$< 10^{-4}$ Relative Error** | Verified against float64 CVXPY |
| **Throughput** | **550,000 Rebalances / sec** | Pipelined batch processing |

---

## 📜 6. References & Literature
1. Boyd, S., et al. (2011). *Distributed Optimization and Statistical Learning via the Alternating Direction Method of Multipliers.* Foundations and Trends in Machine Learning.
2. Markowitz, H. (1952). *Portfolio Selection.* The Journal of Finance.
3. Condat, L. (2016). *Fast projection onto the simplex and the $\ell_1$ ball.* Mathematical Programming.
4. Golub, G. H., & Van Loan, C. F. (2013). *Matrix Computations (4th ed.).* Johns Hopkins University Press.
