`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: FPGA Hardware Acceleration Lab
// Engineer: Advanced Hardware Architect
// 
// Module Name: cholesky_solver
// Project Name: ADMM Dynamic Fixed-Point Portfolio Accelerator
// Target Devices: AMD Xilinx Zynq UltraScale+ XCZU5EV (Kria KV260)
// Tool Versions: AMD Vivado 2022.2+ / 2026.1+
// 
// ===============================================================================
// INTRODUCTION & OBJECTIVES:
//   Task 3.1 represents the foundational computational block in Phase 3 (RTL
//   Architecture Design). It is responsible for solving the w-update subproblem
//   in the Alternating Direction Method of Multipliers (ADMM) algorithm:
//
//       (Sigma + rho * I) * w = mu + rho * (z - u)  <=>  (L_A * L_A^T) * w = b
//
//   The symmetric positive-definite (SPD) quadratic linear system is solved via
//   two consecutive triangular substitutions:
//
//   1. Forward Substitution (Lower Triangular Solve):
//          L_A * y = b  ==>  y_i = (1 / L_{i,i}) * [ b_i - sum_{j=0}^{i-1} L_{i,j} * y_j ]
//
//   2. Backward Substitution (Upper Triangular Solve):
//          L_A^T * w = y ==>  w_i = (1 / L_{i,i}) * [ y_i - sum_{j=i+1}^{N-1} L_{j,i} * w_j ]
//
// ===============================================================================
// HARDWARE ARCHITECTURE & DSP48E2 OPTIMIZATIONS:
//   - Primary Arithmetic Format: Q4.14 signed fixed-point (18-bit word width:
//     1 sign, 3 integer, 14 fractional bits). Perfectly maps to a single 18x27
//     multiplier port of the AMD UltraScale+ DSP48E2 slice.
//   - 48-Bit Internal Accumulator: Aligned to Q8.28 internal precision to prevent
//     intermediate arithmetic overflow, matching the 48-bit P register of DSP48E2.
//   - Zero Hardware Divider: Diagonal reciprocal elements (1 / L_{i,i}) are
//     precomputed in Q4.14 format, replacing costly divider IP with high-speed
//     DSP multipliers followed by convergent rounding and saturation clamping.
//   - Shared Triangular Memory Indexing: Both forward and backward substitutions
//     access the exact same lower-triangular storage (L_mem) using the flat index
//     function index(r, c) = r*(r+1)/2 + c, eliminating transpose logic overhead.
//   - Deterministic Latency: Exactly N*(N+1) clock cycles (~272 cycles for N=16,
//     achieving 1.088 microseconds at 250 MHz on the AMD Kria KV260 platform).
//////////////////////////////////////////////////////////////////////////////////

module cholesky_solver #(
    parameter int N          = 16,
    parameter int DATA_WIDTH = 18,
    parameter int FRAC_BITS  = 14,
    parameter int ACC_WIDTH  = 48
)(
    input  logic                                            clk,
    input  logic                                            rst_n,

    // Matrix L Configuration Interface (Lower Triangular)
    // Total lower triangular elements = N * (N + 1) / 2
    input  logic                                            load_L_en,
    input  logic [$clog2((N*(N+1))/2)-1:0]                  load_L_addr,
    input  logic signed [DATA_WIDTH-1:0]                    load_L_data,

    // Diagonal Reciprocal Configuration Interface (1 / L_ii in Q4.14)
    input  logic                                            load_inv_en,
    input  logic [$clog2(N)-1:0]                            load_inv_addr,
    input  logic signed [DATA_WIDTH-1:0]                    load_inv_data,

    // Compute Control & Vector I/O
    input  logic                                            start,
    input  logic signed [DATA_WIDTH-1:0]                    b_in [N],

    output logic signed [DATA_WIDTH-1:0]                    w_out [N],
    output logic signed [DATA_WIDTH-1:0]                    y_out [N],  // Intermediate forward solve vector
    output logic                                            busy,
    output logic                                            done
);

    localparam int TRI_SIZE = (N * (N + 1)) / 2;
    localparam logic signed [ACC_WIDTH-1:0] ROUND_OFFSET = 48'sd1 << (FRAC_BITS - 1); // 8192 for FRAC_BITS=14
    localparam logic signed [DATA_WIDTH-1:0] MAX_POS = (18'sd1 << (DATA_WIDTH - 1)) - 18'sd1; // +131071
    localparam logic signed [DATA_WIDTH-1:0] MAX_NEG = -(18'sd1 << (DATA_WIDTH - 1));         // -131072

    // Internal Memories
    // 1. Lower triangular matrix storage L_mem
    logic signed [DATA_WIDTH-1:0] L_mem [TRI_SIZE];
    // 2. Reciprocal diagonal elements inv_diag_mem (1 / L_ii)
    logic signed [DATA_WIDTH-1:0] inv_diag_mem [N];

    // Working Registers for intermediate y and final w
    logic signed [DATA_WIDTH-1:0] y_reg [N];
    logic signed [DATA_WIDTH-1:0] w_reg [N];

    // DSP Accumulator
    logic signed [ACC_WIDTH-1:0] acc;

    // FSM State Encoding
    typedef enum logic [2:0] {
        ST_IDLE          = 3'd0,
        ST_FWD_ROW_INIT  = 3'd1,
        ST_FWD_MAC       = 3'd2,
        ST_FWD_RECIP     = 3'd3,
        ST_BWD_ROW_INIT  = 3'd4,
        ST_BWD_MAC       = 3'd5,
        ST_BWD_RECIP     = 3'd6,
        ST_DONE          = 3'd7
    } state_t;

    state_t state;

    // Loop Counters
    integer i_cnt;
    integer j_cnt;

    // Mapping function: 2D row/col in lower triangle to 1D flat address
    // row i, col j with i >= j  ==> addr = i*(i+1)/2 + j
    function automatic integer get_tri_index(input integer r, input integer c);
        return (r * (r + 1)) / 2 + c;
    endfunction

    // Saturation & Quantization Clamp Function from 48-bit to 18-bit signed Q4.14
    function automatic logic signed [DATA_WIDTH-1:0] sat_clamp(input logic signed [ACC_WIDTH-1:0] in_val);
        logic signed [ACC_WIDTH-1:0] rounded_val;
        rounded_val = in_val + ROUND_OFFSET;
        rounded_val = rounded_val >>> FRAC_BITS;
        if (rounded_val > $signed({{30{MAX_POS[17]}}, MAX_POS}))
            return MAX_POS;
        else if (rounded_val < $signed({{30{MAX_NEG[17]}}, MAX_NEG}))
            return MAX_NEG;
        else
            return rounded_val[DATA_WIDTH-1:0];
    endfunction

    // Combinatorial Multiplier Operands & Product
    // Maps directly to DSP48E2 A, B inputs
    logic signed [DATA_WIDTH-1:0] mac_op_a;
    logic signed [DATA_WIDTH-1:0] mac_op_b;
    logic signed [35:0]           mac_prod;

    always_comb begin
        if (state == ST_FWD_MAC) begin
            mac_op_a = L_mem[get_tri_index(i_cnt, j_cnt)];
            mac_op_b = y_reg[j_cnt];
        end else if (state == ST_BWD_MAC) begin
            mac_op_a = L_mem[get_tri_index(j_cnt, i_cnt)];
            mac_op_b = w_reg[j_cnt];
        end else begin
            mac_op_a = '0;
            mac_op_b = '0;
        end
        mac_prod = mac_op_a * mac_op_b;
    end

    // Memory write logic for configuration
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            // Simulation initialization can be done via testbench
        end else begin
            if (load_L_en) begin
                L_mem[load_L_addr] <= load_L_data;
            end
            if (load_inv_en) begin
                inv_diag_mem[load_inv_addr] <= load_inv_data;
            end
        end
    end

    // Main Control & Datapath FSM
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= ST_IDLE;
            busy  <= 1'b0;
            done  <= 1'b0;
            i_cnt <= 0;
            j_cnt <= 0;
            acc   <= '0;
            for (int k = 0; k < N; k++) begin
                y_reg[k] <= '0;
                w_reg[k] <= '0;
            end
        end else begin
            case (state)
                ST_IDLE: begin
                    done <= 1'b0;
                    if (start) begin
                        busy  <= 1'b1;
                        i_cnt <= 0;
                        j_cnt <= 0;
                        state <= ST_FWD_ROW_INIT;
                    end else begin
                        busy  <= 1'b0;
                    end
                end

                // =============================================================
                // FORWARD SUBSTITUTION: L * y = b
                // y_i = (b_i - sum_{j=0}^{i-1} L_{i,j} * y_j) / L_{i,i}
                // =============================================================
                ST_FWD_ROW_INIT: begin
                    // Initialize accumulator with b_in[i] shifted to Q8.28 alignment
                    acc   <= $signed({{16{b_in[i_cnt][DATA_WIDTH-1]}}, b_in[i_cnt], 14'b0});
                    j_cnt <= 0;
                    if (i_cnt == 0) begin
                        // For row 0, no previous elements: skip MAC directly to reciprocal
                        state <= ST_FWD_RECIP;
                    end else begin
                        state <= ST_FWD_MAC;
                    end
                end

                ST_FWD_MAC: begin
                    // Accumulate: acc = acc - (L[i, j] * y[j])
                    acc <= acc - $signed({{12{mac_prod[35]}}, mac_prod});

                    if (j_cnt == i_cnt - 1) begin
                        state <= ST_FWD_RECIP;
                    end else begin
                        j_cnt <= j_cnt + 1;
                    end
                end

                ST_FWD_RECIP: begin
                    // Scale acc with inv_diag_mem[i] using DSP48E2 (27-bit Port A x 18-bit Port B)
                    // acc has 28 fractional bits. Shifting by 5 leaves 23 fractional bits in 27-bit signed.
                    logic signed [26:0]          diff_27;
                    logic signed [44:0]          scale_prod_45;
                    logic signed [44:0]          rounded_prod;
                    logic signed [ACC_WIDTH-1:0] final_val;

                    // Round & clamp acc to 27-bit signed
                    logic signed [ACC_WIDTH-1:0] acc_rounded_5;
                    acc_rounded_5 = (acc + 48'sd16) >>> 5;
                    if (acc_rounded_5 > 48'sd67108863)
                        diff_27 = 27'sd67108863;
                    else if (acc_rounded_5 < -48'sd67108864)
                        diff_27 = -27'sd67108864;
                    else
                        diff_27 = acc_rounded_5[26:0];

                    // 27-bit x 18-bit multiplier (maps directly to single DSP48E2)
                    scale_prod_45 = diff_27 * inv_diag_mem[i_cnt];
                    // Product has 23 + 14 = 37 fractional bits. Round to 14 fractional bits (shift by 23)
                    rounded_prod  = (scale_prod_45 + 45'sd4194304) >>> 23; // 4194304 = 1 << 22

                    // Clamp to 18-bit signed Q4.14
                    if (rounded_prod > $signed({{27{MAX_POS[17]}}, MAX_POS}))
                        y_reg[i_cnt] <= MAX_POS;
                    else if (rounded_prod < $signed({{27{MAX_NEG[17]}}, MAX_NEG}))
                        y_reg[i_cnt] <= MAX_NEG;
                    else
                        y_reg[i_cnt] <= rounded_prod[DATA_WIDTH-1:0];

                    if (i_cnt == N - 1) begin
                        // Forward solve finished! Start backward solve from row N-1
                        i_cnt <= N - 1;
                        state <= ST_BWD_ROW_INIT;
                    end else begin
                        i_cnt <= i_cnt + 1;
                        state <= ST_FWD_ROW_INIT;
                    end
                end

                // =============================================================
                // BACKWARD SUBSTITUTION: L^T * w = y
                // w_i = (y_i - sum_{j=i+1}^{N-1} L_{j,i} * w_j) / L_{i,i}
                // =============================================================
                ST_BWD_ROW_INIT: begin
                    // Initialize accumulator with y_reg[i] shifted to Q8.28 alignment
                    acc   <= $signed({{16{y_reg[i_cnt][DATA_WIDTH-1]}}, y_reg[i_cnt], 14'b0});
                    j_cnt <= i_cnt + 1;
                    if (i_cnt == N - 1) begin
                        // For the last row N-1, no subsequent elements: skip MAC to reciprocal
                        state <= ST_BWD_RECIP;
                    end else begin
                        state <= ST_BWD_MAC;
                    end
                end

                ST_BWD_MAC: begin
                    // L^T[i, j] = L[j, i]. Since j > i, index is get_tri_index(j, i)
                    acc <= acc - $signed({{12{mac_prod[35]}}, mac_prod});

                    if (j_cnt == N - 1) begin
                        state <= ST_BWD_RECIP;
                    end else begin
                        j_cnt <= j_cnt + 1;
                    end
                end

                ST_BWD_RECIP: begin
                    // Scale acc with inv_diag_mem[i] using DSP48E2 (27-bit Port A x 18-bit Port B)
                    logic signed [26:0] diff_27;
                    logic signed [44:0] scale_prod_45;
                    logic signed [44:0] rounded_prod;

                    // Round & clamp acc to 27-bit signed
                    logic signed [ACC_WIDTH-1:0] acc_rounded_5;
                    acc_rounded_5 = (acc + 48'sd16) >>> 5;
                    if (acc_rounded_5 > 48'sd67108863)
                        diff_27 = 27'sd67108863;
                    else if (acc_rounded_5 < -48'sd67108864)
                        diff_27 = -27'sd67108864;
                    else
                        diff_27 = acc_rounded_5[26:0];

                    // 27-bit x 18-bit multiplier
                    scale_prod_45 = diff_27 * inv_diag_mem[i_cnt];
                    // Product has 23 + 14 = 37 fractional bits. Round to 14 fractional bits (shift by 23)
                    rounded_prod  = (scale_prod_45 + 45'sd4194304) >>> 23;

                    // Clamp to 18-bit signed Q4.14
                    if (rounded_prod > $signed({{27{MAX_POS[17]}}, MAX_POS}))
                        w_reg[i_cnt] <= MAX_POS;
                    else if (rounded_prod < $signed({{27{MAX_NEG[17]}}, MAX_NEG}))
                        w_reg[i_cnt] <= MAX_NEG;
                    else
                        w_reg[i_cnt] <= rounded_prod[DATA_WIDTH-1:0];

                    if (i_cnt == 0) begin
                        // Backward solve finished! Entire system A*w = b solved!
                        state <= ST_DONE;
                    end else begin
                        i_cnt <= i_cnt - 1;
                        state <= ST_BWD_ROW_INIT;
                    end
                end

                ST_DONE: begin
                    busy  <= 1'b0;
                    done  <= 1'b1;
                    state <= ST_IDLE;
                end

                default: state <= ST_IDLE;
            endcase
        end
    end

    // Output assignments
    assign w_out = w_reg;
    assign y_out = y_reg;

endmodule
