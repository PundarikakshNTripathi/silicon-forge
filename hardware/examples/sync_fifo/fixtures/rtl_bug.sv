// 4-entry x 8-bit synchronous FIFO with occupancy counter
module sync_fifo (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       wr_en_i,
    input  logic [7:0] wr_data_i,
    input  logic       rd_en_i,
    output logic [7:0] rd_data_o,
    output logic       full_o,
    output logic       empty_o,
    output logic [2:0] level_o
);
    logic [7:0] mem [0:3];
    logic [1:0] wr_ptr;
    logic [1:0] rd_ptr;

    logic do_write;
    logic do_read;

    assign full_o    = (level_o >= 3'd3);
    assign empty_o   = (level_o == 3'd0);
    assign do_write  = wr_en_i & ~full_o;
    assign do_read   = rd_en_i & ~empty_o;
    assign rd_data_o = mem[rd_ptr];

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_ptr  <= 2'd0;
            rd_ptr  <= 2'd0;
            level_o <= 3'd0;
        end else begin
            if (do_write) begin
                mem[wr_ptr] <= wr_data_i;
                wr_ptr      <= wr_ptr + 2'd1;
            end
            if (do_read) begin
                rd_ptr <= rd_ptr + 2'd1;
            end
            case ({do_write, do_read})
                2'b10:   level_o <= level_o + 3'd1;
                2'b01:   level_o <= level_o - 3'd1;
                default: level_o <= level_o;
            endcase
        end
    end
endmodule
