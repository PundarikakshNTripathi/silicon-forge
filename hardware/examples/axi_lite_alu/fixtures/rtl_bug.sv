// Reference synthesizable SystemVerilog-2012 module for SiliconForge
module axi_lite_alu (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        valid_i,
    output logic        ready_o,
    input  logic [2:0]  op_i,
    input  logic [31:0] a_i,
    input  logic [31:0] b_i,
    output logic        valid_o,
    output logic [31:0] result_o,
    output logic        zero_o
);
    logic [31:0] alu_comb;

    assign ready_o = 1'b1;

    always_comb begin
        alu_comb = 32'd0;
        case (op_i)
            3'd0:    alu_comb = a_i + b_i;
            3'd1:    alu_comb = a_i - b_i;
            3'd2:    alu_comb = a_i & b_i;
            3'd3:    alu_comb = a_i | b_i;
            3'd4:    alu_comb = a_i ^ b_i;
            default: alu_comb = 32'd0;
        endcase
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_o  <= 1'b0;
            result_o <= 32'd0;
            zero_o   <= 1'b1;
        end else begin
            valid_o <= valid_i & ready_o;
            if (valid_i & ready_o) begin
                result_o <= alu_comb;
                zero_o   <= (alu_comb != 32'd0);
            end
        end
    end
endmodule
