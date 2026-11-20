// 8-bit loadable up/down counter with wrap flag
module up_down_counter (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       en_i,
    input  logic       load_i,
    input  logic       up_i,
    input  logic [7:0] load_val_i,
    output logic [7:0] count_o,
    output logic       wrap_o
);
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            count_o <= 8'd0;
            wrap_o  <= 1'b0;
        end else begin
            wrap_o <= 1'b0;
            if (load_i) begin
                count_o <= load_val_i;
            end else if (en_i) begin
                if (up_i) begin
                    count_o <= count_o + 8'd1;
                    wrap_o  <= (count_o == 8'hFF);
                end else begin
                    count_o <= count_o - 8'd1;
                    wrap_o  <= (count_o == 8'h00);
                end
            end
        end
    end
endmodule
