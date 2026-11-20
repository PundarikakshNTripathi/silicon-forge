// Reference SymbiYosys SVA formal properties for axi_lite_alu
`ifdef FORMAL
    reg f_past_valid;
    initial f_past_valid = 1'b0;
    always @(posedge clk) f_past_valid <= 1'b1;
    always @(*) if (!f_past_valid) assume(!rst_n);

    always @(posedge clk) begin
        if (f_past_valid && $past(!rst_n)) begin
            P_RST_VALID_LOW:   assert(valid_o == 1'b0);
            P_RST_RESULT_ZERO: assert(result_o == 32'd0);
        end
        if (f_past_valid && rst_n && valid_o) begin
            P_ZERO_FLAG_CONSISTENT: assert(zero_o == (result_o == 32'd0));
        end
        if (f_past_valid && rst_n) begin
            C_REACH_VALID_OUT: cover(valid_o && (result_o != 32'd0));
        end
    end
`endif
