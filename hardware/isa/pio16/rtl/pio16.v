// pio16: reference implementation of the PIO16 teaching ISA (hardware/isa/pio16/isa.yaml).
//
// It exists to show the toolchain end to end: the decoder is the generated one (gen/rtl/pio16_decode.v), the golden
// model and assembler come from the same isa.yaml, and test/test_lockstep.py checks this RTL against that model after
// every instruction. It is a teaching design, not an optimised one.
//
// Timing contract (matches the spec): an instruction's effects land on the clock edge that issues it, and the next
// instruction issues `cycles` clocks later (`delay n` therefore holds the engine for n extra clocks).
`default_nettype none
`include "pio16_isa.vh"

module pio16 (
    input  wire        clk,
    input  wire        rst_n,
    // program memory: write while run = 0
    input  wire        prog_we,
    input  wire [4:0]  prog_addr,
    input  wire [15:0] prog_data,
    input  wire        run,
    // pins
    input  wire [7:0]  pins_in,
    output reg  [7:0]  pins_out,
    output reg  [7:0]  oe,
    // architectural state, exposed for lockstep checking
    output reg  [4:0]  pc,
    output reg  [7:0]  x,
    output reg  [7:0]  y,
    output reg         halted,
    output reg         retire     // high for one clock after each issued instruction
);
  reg [15:0] imem [0:31];
  reg [7:0]  wait_cnt;

  wire [15:0] instr = imem[pc];
  wire is_nop, is_ldx, is_ldy, is_pins, is_oe, is_delay, is_jmp, is_djnz, is_jpin, is_halt, illegal;
  pio16_decode dec (
      .instr(instr), .is_nop(is_nop), .is_ldx(is_ldx), .is_ldy(is_ldy), .is_pins(is_pins), .is_oe(is_oe),
      .is_delay(is_delay), .is_jmp(is_jmp), .is_djnz(is_djnz), .is_jpin(is_jpin), .is_halt(is_halt),
      .illegal(illegal)
  );

  wire [7:0] x_dec = x - 8'd1;
  wire [4:0] target = `PIO16_JMP_T(instr);
  wire       pin_sel = pins_in[`PIO16_JPIN_P(instr)];

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      pc <= 5'd0; x <= 8'd0; y <= 8'd0; pins_out <= 8'd0; oe <= 8'd0; halted <= 1'b0;
      wait_cnt <= 8'd0; retire <= 1'b0;
    end else begin
      retire <= 1'b0;
      if (prog_we && !run) imem[prog_addr] <= prog_data;
      if (run && !halted) begin
        if (wait_cnt != 8'd0) begin
          wait_cnt <= wait_cnt - 8'd1;
        end else begin
          retire <= 1'b1;
          pc <= pc + 5'd1;
          if (is_ldx)   x <= `PIO16_LDX_V(instr);
          if (is_ldy)   y <= `PIO16_LDY_V(instr);
          if (is_pins)  pins_out <= `PIO16_PINS_V(instr);
          if (is_oe)    oe <= `PIO16_OE_V(instr);
          if (is_delay) wait_cnt <= `PIO16_DELAY_N(instr);
          if (is_jmp)   pc <= target;
          if (is_djnz) begin
            x <= x_dec;
            if (x_dec != 8'd0) pc <= target;
          end
          if (is_jpin && pin_sel) pc <= `PIO16_JPIN_T(instr);
          if (is_halt || illegal) halted <= 1'b1;
        end
      end
    end
  end
endmodule
`default_nettype wire
