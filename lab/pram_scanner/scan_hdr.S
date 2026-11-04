; LR2021 PRAM blob: on-chip multi-channel LoRa CAD scanner (scan.c). Separate image from pram.bin.
; Same blob format as sweep.S: magic, entry count, entry table; entry 0 runs at activation.
        .arc_attribute Tag_ARC_ABI_rf16, 1
        .section .text
        .global _start
_start:
        .word   0x600DB002              ; magic
        .word   0x00000007              ; 7 entries
        .word   install                 ; 0
        .word   scan_cmd                ; 1 (scan.c): blocking scan
        .word   hello                   ; 2
        .word   cpuinfo                 ; 3
        .word   bg_cmd                  ; 4 (scan.c): background scan
        .word   aux_cmd                 ; 5 (scan.c): aux register read/write
        .word   bench_cmd               ; 6 (scan.c): no-RF config timing
        .word   0

install:
        mov_s   r0, 2
        j_s     [blink]

; 2: returns 'S' 'C' 'N' '2'
hello:
        ld_s    r3, [r2, 4]
        mov_s   r12, 0x53
        stb_s   r12, [r3, 0]
        mov_s   r12, 0x43
        stb_s   r12, [r3, 1]
        mov_s   r12, 0x4E
        stb_s   r12, [r3, 2]
        mov_s   r12, 0x33
        stb_s   r12, [r3, 3]
        mov_s   r12, 4
        st_s    r12, [r2, 0]
        mov_s   r0, 3
        j_s     [blink]

; 3: returns aux AUX_IRQ_ACT (0x43), STATUS32 (0x0A), IRQ status 0xF2A00C, AUX_IRQ_PRIORITY... as 3 x u32 BE
cpuinfo:
        ld_s    r3, [r2, 4]
        lr      r12, [0x43]
        lsr     r0, r12, 24
        stb_s   r0, [r3, 0]
        lsr     r0, r12, 16
        stb_s   r0, [r3, 1]
        lsr     r0, r12, 8
        stb_s   r0, [r3, 2]
        stb_s   r12, [r3, 3]
        lr      r12, [0x0A]
        lsr     r0, r12, 24
        stb_s   r0, [r3, 4]
        lsr     r0, r12, 16
        stb_s   r0, [r3, 5]
        lsr     r0, r12, 8
        stb_s   r0, [r3, 6]
        stb_s   r12, [r3, 7]
        mov     r1, 0xF2A00C
        ld      r12, [r1]
        lsr     r0, r12, 24
        stb_s   r0, [r3, 8]
        lsr     r0, r12, 16
        stb_s   r0, [r3, 9]
        lsr     r0, r12, 8
        stb_s   r0, [r3, 10]
        stb_s   r12, [r3, 11]
        mov_s   r12, 12
        st_s    r12, [r2, 0]
        mov_s   r0, 3
        j_s     [blink]
