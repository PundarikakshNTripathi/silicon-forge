// 8N1 UART transmitter, LSB first, 4 clocks per bit
module uart_tx (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       start_i,
    input  logic [7:0] data_i,
    output logic       tx_o,
    output logic       busy_o
);
    typedef enum logic [1:0] {S_IDLE, S_START, S_DATA, S_STOP} state_t;

    state_t      state;
    logic [1:0]  tick;
    logic [2:0]  bit_idx;
    logic [7:0]  shreg;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state   <= S_IDLE;
            tick    <= 2'd0;
            bit_idx <= 3'd0;
            shreg   <= 8'd0;
            tx_o    <= 1'b1;
            busy_o  <= 1'b0;
        end else begin
            case (state)
                S_IDLE: begin
                    tx_o <= 1'b1;
                    if (start_i) begin
                        shreg   <= data_i;
                        state   <= S_START;
                        tick    <= 2'd0;
                        tx_o    <= 1'b0;
                    end
                end
                S_START: begin
                    tick <= tick + 2'd1;
                    if (tick == 2'd3) begin
                        state   <= S_DATA;
                        bit_idx <= 3'd0;
                        tx_o    <= shreg[0];
                        busy_o  <= 1'b1;
                    end
                end
                S_DATA: begin
                    tick <= tick + 2'd1;
                    if (tick == 2'd3) begin
                        if (bit_idx == 3'd7) begin
                            state <= S_STOP;
                            tx_o  <= 1'b1;
                        end else begin
                            bit_idx <= bit_idx + 3'd1;
                            tx_o    <= shreg[bit_idx + 3'd1];
                        end
                    end
                end
                S_STOP: begin
                    tick <= tick + 2'd1;
                    if (tick == 2'd3) begin
                        state  <= S_IDLE;
                        busy_o <= 1'b0;
                    end
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
