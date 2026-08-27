# 🗺️ Project Roadmap & Progress Dashboard: Dynamic Fixed-Point ADMM Solver on FPGA

> **Project Goal:** Design, implement, verify, and deploy a deterministic sub-microsecond, dynamic fixed-point ADMM hardware accelerator for real-time constrained portfolio optimization on the **AMD Xilinx Kria KV260 (Zynq UltraScale+ XCZU5EV)**.

---

## 📊 1. Executive Progress & Verification Statistics

| Metric | Target Specification | Current Status | Verification Source |
| :--- | :--- | :---: | :--- |
| **Float64 Model Convergence** | 100% convergence across random PSD matrices | **✅ PASSED (100%)** | `tests/test_admm_models.py::test_float64_solver_convergence` |
| **CVXPY Equivalence** | Relative Objective Gap $< 10^{-4}$ | **✅ PASSED ($2.99 \times 10^{-6}$)** | `tests/test_admm_models.py::test_cvxpy_equivalence` |
| **Fixed-Point Quantization** | Max Weight Error $< 0.05\%$ with SQNR $> 50\text{ dB}$ | **✅ PASSED ($56.51\text{ dB}$)** | `model/quant_error_sweep.py` (Q4.14 Data, Q4.20 Dual) |
| **DSP Allocation Target** | 1x DSP48E2 ($18 \times 27$) per MAC unit | **✅ PASSED (18-bit fit)** | Parameterized Q4.14 Datapath |
| **Simplex Constraint Satisfaction** | $\sum w_i = 1.0 \;\land\; 0 \le w_i \le w_{\max}$ | **✅ PASSED (Exact)** | `tests/test_admm_models.py::test_simplex_projection` |
| **Testbench Vectors Generated** | Multi-scenario Hex vectors for $N = 8, 16, 32$ | **✅ COMPLETED** | `sim/vectors/*.hex` |
| **Unit & Integration Test Suite** | 7/7 automated tests passing | **✅ PASSED (100%)** | `pytest -v` (7 passed in 1.30s) |
| **Hardware Core Clock ($f_{\max}$)** | $250\text{ MHz} - 300\text{ MHz}$ on ZU5EV | ⏳ Pending (Phase 4) | Vivado OOC Synthesis Constraints |
| **Tick-to-Trade Latency** | $< 1.8 \;\mu\text{s}$ for $N=16$ Assets | ⏳ Pending (Phase 5) | Hardware-in-the-Loop on Kria KV260 |

---

## 📅 2. Multi-Phase Execution Plan (Enhanced v2.0)

```
 Phase 1: Math & Golden Model [DONE]  ──► Phase 2: Fixed-Point Analysis [DONE] ──► Phase 3: RTL Architecture [IN PROGRESS]
                  │                                         │                                      │
                  ▼                                         ▼                                      ▼
 Phase 6: Publication & Thesis [PENDING] ◄── Phase 5: KV260 Board Bringup [PENDING] ◄── Phase 4: Verification & Vivado [PENDING]
```

---

### 🟢 Phase 1: Mathematical Modeling & Golden Reference (Weeks 1 - 2) — [STATUS: 100% COMPLETE]
* [x] **Task 1.1:** Develop float64 solver in Python (`model/admm_golden_float64.py`) with handcrafted Cholesky-Banachiewicz and Forward/Backward substitution matching 1D Systolic Array.
* [x] **Task 1.2:** Cross-validate convergence and optimal weights $w^*$ against CVXPY (Clarabel / OSQP / ECOS solvers) with objective gap $< 10^{-6}$ (`model/verify_with_cvxpy.py`).
* [x] **Task 1.3:** Implement exact 1D Water-Filling algorithm for box-constrained simplex projection:
  $$\sum_{i=1}^N \text{clip}(\tilde{z}_i - \nu^*, \; 0, \; w_{\max}) = 1.0$$
* [x] **Task 1.4:** Build realistic multi-factor market generator and export Q4.14 hex test vectors (`inputs_N*.hex`, `expected_w_N*.hex`) for $N = 8, 16, 32$ into `sim/vectors/` (`model/generate_test_vectors.py`).

---

### 🟢 Phase 2: Dynamic Fixed-Point & Error Budget Analysis (Weeks 3 - 4) — [STATUS: 100% COMPLETE]
* [x] **Task 2.1:** Construct bit-true Python simulation (`model/admm_fixedpoint.py`) with parameterized two's complement quantizers.
* [x] **Task 2.2:** Establish Dual-Scale Fixed-Point architecture:
  * Primary Datapath: `Q4.14` (18-bit signed) matching a single DSP48E2 multiplier port.
  * Dual Variable Register $u$: `Q4.20` (24-bit signed with 6 guard bits) to eliminate stall / catastrophic cancellation.
* [x] **Task 2.3:** Run automated fractional bit sweep ($F \in [10, 20]$) in `model/quant_error_sweep.py`, proving $F=14$ achieves $56.51\text{ dB}$ SQNR and $< 0.047\%$ max weight error.
* [x] **Task 2.4:** Build automated regression test suite (`tests/test_admm_models.py`) with 7/7 tests passing.

---

### 🟡 Phase 3: RTL Architecture & Core IP Blocks (Weeks 5 - 8) — [STATUS: IN PROGRESS]
* [ ] **Task 3.1: Triangular Solver Core (`rtl/cholesky_solver.sv`)**
  * Implement 1D Folded Systolic Array for Forward ($L y = b$) and Backward ($L^T w = y$) triangular substitution.
  * Pipeline DSP48E2 MAC engines with 48-bit internal accumulation to prevent intermediate overflow.
  * Implement diagonal division via LUT-based reciprocal multipliers ($1 / L_{ii}$).
* [ ] **Task 3.2: Real-Time Rank-1 Cholesky Updater (`rtl/cholesky_rank1_up.sv`)**
  * Implement Givens rotation pipeline to update $L_A$ in $\mathcal{O}(N^2)$ upon tick arrival without matrix refactorization.
  * Add periodic re-synchronization counter to bound cumulative quantization drift.
* [ ] **Task 3.3: Proximal & Projection Engine (`rtl/soft_threshold.sv`, `rtl/water_filling.sv`)**
  * Design parallel SIMD Soft-Thresholding operator with bypass multiplexers.
  * Build Fully Pipelined Bitonic Sorting Network (`rtl/bitonic_sort.sv`) with inter-stage registers for 300MHz timing closure.
  * Implement prefix-sum water-filling logic to calculate exact Lagrange multiplier $\nu^*$ in deterministic cycles.
* [ ] **Task 3.4: Dual Accumulator & Stopping Criterion (`rtl/dual_accumulator.sv`, `rtl/convergence_check.sv`)**
  * Design 24-bit accumulator with 6 guard bits for dual variable integration.
  * Build pipelined binary tree comparator for $\ell_\infty$-norm residual checking ($\|r^{k+1}\|_\infty \le \epsilon_{\text{pri}} \;\land\; \|s^{k+1}\|_\infty \le \epsilon_{\text{dual}}$).
* [ ] **Task 3.5: Top-Level Integration & CDC (`rtl/admm_top.sv`)**
  * Implement Master FSM (Idle $\to$ Load $\to$ Iterate $\to$ Check $\to$ Output).
  * Wrap with AXI4-Stream interface and Dual-Clock Asynchronous FIFO for safe Clock Domain Crossing (CDC) between 100MHz PS and 300MHz PL.

---

### 🟠 Phase 4: Verification & Vivado Timing Closure (Weeks 9 - 10) — [STATUS: PENDING]
* [ ] **Task 4.1:** Build SystemVerilog self-checking testbench (`sim/tb_admm_top.sv`) with cycle-by-cycle trace assertion against Python model.
* [ ] **Task 4.2:** Run functional regression over 10,000+ market vectors in Vivado xsim / ModelSim (100% PASS).
* [ ] **Task 4.3:** Out-of-Context (OOC) Synthesis & Place-and-Route on XCZU5EV:
  * Constrain clock to $f_{\text{target}} = 300\text{ MHz}$ ($T_{\text{clk}} = 3.333\text{ ns}$).
  * Verify Worst Negative Slack ($\text{WNS} \ge 0\text{ ns}$) and Total Negative Slack ($\text{TNS} = 0\text{ ns}$).
  * Confirm resource budget: $< 20\%$ LUTs, $< 8\%$ DSPs, $< 10\%$ BRAMs.
* [ ] **Task 4.4:** Run **Post-Implementation Gate-Level & Timing Simulation** with SDF back-annotation.

---

### 🟣 Phase 5: Kria KV260 Board Bringup & Hardware-in-the-Loop (Weeks 11 - 12) — [STATUS: PENDING]
* [ ] **Task 5.1:** Generate Vivado Block Design (`syn/create_kv260_bd.tcl`):
  * Connect ADMM Core IP to Zynq UltraScale+ PS via AXI DMA (High-Performance HP port).
  * Insert Vivado ILA (Integrated Logic Analyzer) IP core on AXI-Stream interfaces for real-time hardware probing.
* [ ] **Task 5.2:** Build bitstream and generate hardware handoff files (`admm_kv260.bit`, `admm_kv260.hwh`).
* [ ] **Task 5.3:** Develop embedded Linux / PYNQ driver (`sw/driver.py`) on Ubuntu 22.04 LTS:
  * Contiguous memory DMA buffer allocation with cache flush/invalidation.
  * Real-time tick latency benchmark via AXI Timer hardware performance counter.
* [ ] **Task 5.4:** Execute Hardware-in-the-Loop (HIL) benchmark comparing KV260 with Intel x86 (OSQP) and GPU.

---

### 🔴 Phase 6: Thesis & Paper Publication Preparation (Weeks 13 - 14) — [STATUS: PENDING]
* [ ] **Task 6.1:** Construct comprehensive benchmarking tables:
  * Deterministic latency ($\mu\text{s}$), Throughput (Rebalances/sec), Power dissipation (Watts), Energy per tick ($\mu\text{J}$).
* [ ] **Task 6.2:** Produce high-resolution figures:
  * System pipeline architecture, Vivado floorplan layout, timing waveforms, and convergence SQNR plots.
* [ ] **Task 6.3:** Complete final graduation thesis and finalize manuscript targeting **IEEE TCAD / ACM TRETS / IEEE TVLSI**.

---

## 🎯 3. Milestone Summary & Target Dates

| Milestone | Deliverable Description | Completion Date | Verification Artifact |
| :--- | :--- | :---: | :---: |
| **M1: Golden Reference** | Python Float64 & Fixed-Point bit-true models matching CVXPY | **✅ Completed (Week 2)** | `tests/test_admm_models.py` |
| **M2: RTL Core Design** | Clean SystemVerilog RTL modules passing unit tests | ⏳ Target: Week 8 | `rtl/*.sv` |
| **M3: Timing Closure** | 300 MHz clean synthesis on ZU5EV with WNS > 0 | ⏳ Target: Week 10 | Vivado Timing Report |
| **M4: KV260 On-Chip Demo** | Sub-microsecond real-time portfolio solver running on hardware | ⏳ Target: Week 12 | PYNQ Driver & ILA Waveforms |
| **M5: Paper & Thesis** | Complete manuscript & final thesis document ready for submission | ⏳ Target: Week 14 | Final PDF Report & Thesis |

---

## 🛠️ 4. Standardized Toolchain & Hardware Environment

* **Target Silicon:** AMD Xilinx Zynq UltraScale+ MPSoC `XCZU5EV-2SFVC784-E` (Kria KV260 Starter Kit).
* **FPGA Toolchain:** AMD Vivado ML Enterprise / Standard `2022.2`.
* **Embedded OS & Framework:** Ubuntu 22.04 LTS (Kernel 5.15) + PYNQ v3.0.1.
* **Verification Environment:** Python 3.12 (`numpy`, `scipy`, `cvxpy`, `pytest`, `pandas`).
