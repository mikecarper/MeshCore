; SX126x V2D 2D02 PRAM. Command 0x9B accepts the four SetRfFrequency
; bytes, MSB first. The low byte commits the synthesizer frequency.
; Assemble using sxplug (beala/lr2021-re issue 2), without its runtime:
; python3 -m sxplug build profile_frequency.asm --no-runtime -f h -o profile_frequency
.org 0x8000
        jump unused
        jump unused
        jump unused
        jump unused
        calls write_tag
        ret
; The ROM enters the update, 0x9A, 0x9B and 0x9C handlers at six-word intervals.
.org 0x8006
        jump activate
.org 0x800c
        jump unsupported
.org 0x8012
        jump tune
.org 0x8018
        jump unsupported
unused:
        move r2, #0
        move r3, #0
        rets
unsupported:
        move r2, #8
        move r3, #0
        rets
write_tag:
        ld16 i0, 0x032e
        move a, #'K'
        move (i0), a
        rets
activate:
        spush ip
        calls write_tag
        spop ip
        move r2, #2
        move r3, #0
        rets
tune:
        move r3, [0x9a]
        move [0x6b], r3
        move r3, [0x9b]
        move [0x6c], r3
        move r3, [0x9c]
        move [0x6d], r3
        move r3, [0x9d]
        move [0x6e], r3
        move r2, #2
        move r3, #0
        rets
