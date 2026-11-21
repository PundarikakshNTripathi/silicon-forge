; UART transmit of one byte (0x55), 8N1, LSB first, on pin 0.
; Bit time is exactly 16 cycles: each `pins` costs 1 cycle and each `delay 14` costs 15, and `.expect 16` makes the
; timing checker prove that the gap between every pair of pin events is 16, not just observe it.
        oe     0x01
        pins   0x01          ; line idle high
        delay  14
        .expect 16
        pins   0x00          ; start bit
        delay  14
        .expect 16
        pins   0x01          ; d0 = 1
        delay  14
        .expect 16
        pins   0x00          ; d1 = 0
        delay  14
        .expect 16
        pins   0x01          ; d2 = 1
        delay  14
        .expect 16
        pins   0x00          ; d3 = 0
        delay  14
        .expect 16
        pins   0x01          ; d4 = 1
        delay  14
        .expect 16
        pins   0x00          ; d5 = 0
        delay  14
        .expect 16
        pins   0x01          ; d6 = 1
        delay  14
        .expect 16
        pins   0x00          ; d7 = 0
        delay  14
        .expect 16
        pins   0x01          ; stop bit
        delay  14
        halt
