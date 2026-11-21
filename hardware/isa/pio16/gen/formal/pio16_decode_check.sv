// Generated: formal check of pio16_decode.
module pio16_decode_check (input wire [15:0] instr);
  wire is_nop;
  wire is_ldx;
  wire is_ldy;
  wire is_pins;
  wire is_oe;
  wire is_delay;
  wire is_jmp;
  wire is_djnz;
  wire is_jpin;
  wire is_halt;
  wire illegal;
  pio16_decode dut (.instr(instr), .is_nop(is_nop), .is_ldx(is_ldx), .is_ldy(is_ldy), .is_pins(is_pins), .is_oe(is_oe), .is_delay(is_delay), .is_jmp(is_jmp), .is_djnz(is_djnz), .is_jpin(is_jpin), .is_halt(is_halt), .illegal(illegal));
  wire [9:0] hits = {is_nop, is_ldx, is_ldy, is_pins, is_oe, is_delay, is_jmp, is_djnz, is_jpin, is_halt};
  always @* begin
    assert ((hits & (hits - 1'b1)) == 0);       // at most one instruction matches any word
    assert (illegal == (hits == 0));            // illegal means exactly: nothing matched
  end
endmodule
