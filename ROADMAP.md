# 🗺️ Project Roadmap: Dynamic Fixed-Point ADMM Solver on FPGA

> **Project Goal:** Design, implement, and verify a sub-microsecond, dynamic fixed-point ADMM hardware accelerator for real-time constrained portfolio optimization on the **AMD Xilinx Kria KV260 (Zynq UltraScale+ ZU5EV)**.

---

## 📅 Multi-Phase Execution Plan

```
 Phase 1: Math & Golden Model  ──► Phase 2: Fixed-Point Analysis ──► Phase 3: RTL Block Design
              │                                │                                │
              ▼                                ▼                                ▼
 Phase 6: Publication & Thesis ◄── Phase 5: KV260 On-Chip Demo  ◄── Phase 4: IP & Vivado Integration
```

---

### 🟢 Phase 1: Mathematical Modeling & Golden Reference (Weeks 1 - 2)
* [ ] **Task 1.1:** Develop float64 solver in Python (`model/admm_golden_float64.py`) using NumPy.
* [ ] **Task 1.2:** Cross-validate convergence and optimal weights $w^*$ against CVXPY (ECOS/OSQP solvers).
* [ ] **Task 1.3:** Implement exact 1D Water-Filling algorithm for box-constrained simplex projection:
  $$\sum_{i=1}^N \text{clip}(\tilde{z}_i - \nu^*, 0, w_{\max}) = 1$$
* [ ] **Task 1.4:** Generate synthetic and real-market return data (S&P 500 / NASDAQ tick data) for benchmark test vectors.

---

### 🟡 Phase 2: Dynamic Fixed-Point & Error Budget Analysis (Weeks 3 - 4)
* [ ] **Task 2.1:** Construct bit-true Python simulation (`model/admm_fixedpoint.py`) supporting parameterized `Q<I>.<F>` formats.
* [ ] **Task 2.2:** Profile dynamic range across all algorithm stages:
  * Covariance elements $\Sigma_{ij}$ and Cholesky factors $L_{ij}$.
  * Quadratic solve output $w^{k+1}$.
  * Soft-thresholding output $\tilde{z}$ and projected weights $z^{k+1}$.
  * Dual accumulator $u^{k+1}$.
* [ ] **Task 2.3:** Run automated error sweep (`model/quant_error_sweep.py`):
  * Analyze Relative Error vs. Fractional Bits ($F \in [10, 24]$).
  * Determine optimal data-path width (`Q4.14` for 18-bit DSP input, `Q4.20` for dual accumulator).
* [ ] **Task 2.4:** Formulate analytical fixed-point error bounds and convergence guarantees for academic reporting.

---

### 🟠 Phase 3: RTL Architecture & Core IP Blocks (Weeks 5 - 8)
* [ ] **Task 3.1: Triangular Solver (`rtl/cholesky_solver.sv`)**
  * Implement 1D Folded Systolic Array for Forward ($L y = b$) and Backward ($L^T w = y$) substitution.
  * Pipeline DSP48E2 MAC units with division by diagonal elements via reciprocal multiplication.
* [ ] **Task 3.2: Rank-1 Cholesky Updater (`rtl/cholesky_rank1_up.sv`)**
  * Implement Givens rotation pipeline to update $L_A$ in $\mathcal{O}(N^2)$ upon new price tick $r_t$.
* [ ] **Task 3.3: Proximal & Projection Engine (`rtl/soft_threshold.sv`, `rtl/water_filling.sv`)**
  * Design parallel SIMD Soft-Thresholding operator.
  * Build Fully Pipelined Bitonic Sorting Network (`rtl/bitonic_sort.sv`) with $\mathcal{O}(\log_2^2 N)$ latency.
  * Implement prefix-sum water-filling logic to compute exact $\nu^*$ in deterministic cycles.
* [ ] **Task 3.4: Dual Accumulator & Stopping Criterion (`rtl/dual_accumulator.sv`, `rtl/convergence_check.sv`)**
  * Design 24-bit accumulator with 6 guard bits to avoid catastrophic cancellation.
  * Build pipelined binary tree comparator for $\ell_\infty$ norm residual checks.
* [ ] **Task 3.5: Top-Level Integration (`rtl/admm_top.sv`)**
  * Encapsulate control FSM (Idle $\rightarrow$ Load $\rightarrow$ Iterate $\rightarrow$ Convergence Check $\rightarrow$ Output).
  * Wrap with standard AXI4-Stream interface (Slave: inputs, Master: optimal weights).

---

### 🔵 Phase 4: Verification & Vivado Synthesis (Weeks 9 - 10)
* [ ] **Task 4.1:** Build SystemVerilog/UVM testbench (`sim/tb_admm_top.sv`) with automated file I/O comparison against Python golden vectors.
* [ ] **Task 4.2:** Run functional simulation across 10,000+ random market scenarios with 100% test pass rate.
* [ ] **Task 4.3:** Vivado Out-of-Context (OOC) Synthesis & Implementation:
  * Target clock frequency: $250\text{ MHz} - 300\text{ MHz}$.
  * Achieve zero setup/hold timing violations.
  * Verify resource footprint ($< 20\%$ LUT, $< 8\%$ DSP on XCZU5EV).

---

### 🟣 Phase 5: Kria KV260 Deployment & Hardware-in-the-Loop (Weeks 11 - 12)
* [ ] **Task 5.1:** Create Vivado Block Design (`syn/create_kv260_bd.tcl`):
  * Connect ADMM Core IP to Zynq UltraScale+ Processing System (PS) via AXI DMA.
* [ ] **Task 5.2:** Generate Bitstream and package device tree overlay (`.xclbin` / `.bit` + `.hwh`).
* [ ] **Task 5.3:** Develop PYNQ / C++ embedded Linux driver (`sw/driver.py`):
  * Direct memory access (DMA) buffer management for streaming price vectors.
  * Measure end-to-end latency (Tick-to-Trade) using hardware performance counters (APM/AXI Timer).
* [ ] **Task 5.4:** Hardware-in-the-Loop (HIL) benchmark against CPU (Intel Core i7 / Xeon running OSQP) and GPU.

---

### 🔴 Phase 6: Thesis & Paper Publication Preparation (Weeks 13 - 14)
* [ ] **Task 6.1:** Comprehensive benchmarking table:
  * Clock cycles, Latency ($\mu\text{s}$), Power (Watts), Energy per Rebalance ($\mu\text{J}$).
  * Comparison with state-of-the-art FPGA QP/ADMM solvers in literature.
* [ ] **Task 6.2:** High-quality diagrams (Architecture, Timing waveforms, Convergence plots, Floorplan).
* [ ] **Task 6.3:** Complete thesis document and draft manuscript targeting IEEE TCAD / ACM TRETS / IEEE TVLSI.

---

## 🎯 Milestone Checklist

| Milestone | Deliverable | Status | Target Date |
| :--- | :--- | :---: | :---: |
| **M1: Golden Reference** | Python Float64 & Fixed-point models matching CVXPY | ⏳ In Progress | Week 2 |
| **M2: RTL Verification** | Bit-true RTL simulation matching Python within $10^{-4}$ | ⏳ Pending | Week 8 |
| **M3: Timing Closure** | 250 MHz synthesis on ZU5EV without timing violations | ⏳ Pending | Week 10 |
| **M4: On-Chip Demo** | Real-time KV260 execution with $< 2 \mu\text{s}$ latency | ⏳ Pending | Week 12 |
| **M5: Publication Ready** | Complete experimental results & paper draft | ⏳ Pending | Week 14 |
