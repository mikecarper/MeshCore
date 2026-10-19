#!/usr/bin/env python3
"""Check canonical Reader replies and stack using the linked RAK Full ARM ELF.

Requires unicorn==2.1.4. Every first page and all pages of the three longest
verses are compared with the independently ASCII-normalized pinned source.
--every-page reconstructs every verse. Only replyVerse and its callee frames
are measured; the fixed caller reserve is not a physical high-water reading
and does not establish ISR/SoftDevice/whole-firmware stack or hardware safety.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from firmware_elf import FirmwareElf
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn import arm_const as arm

ROOT = Path(__file__).resolve().parents[1]
TYPOGRAPHY = str.maketrans({'\u00a0':' ', '\u2013':'-', '\u2014':'-', '\u2018':"'", '\u2019':"'", '\u201c':'"', '\u201d':'"'})


def check(path, every_page=False):
    elf = FirmwareElf(path)
    if elf.unpack('H', 18)[0] != 40:
        raise ValueError('Expected ARM ELF')
    names = [n for n in elf.symbols if 'replyVerse' in n]
    # AAPCS passes the three-byte Selection aggregate in r0, then page,
    # reply and capacity in r1-r3. Reject ABI-changing LTO clones rather than
    # accidentally invoking one with the original source function's ABI.
    expected_name = '_ZN4mesh12_GLOBAL__N_110replyVerseENS0_9SelectionEhPcj'
    if names != [expected_name]:
        raise ValueError('Reply function needs manual ABI review: ' + repr(names))
    name = names[0]
    uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    uc.mem_map(0, 0x100000)
    uc.mem_map(0x20000000, 0x40000)
    for section in elf.sections:
        if section[1] != 8 and section[2] & 2 and section[5]:
            uc.mem_write(section[3], elf.section_bytes(section))
    stop, top, reply = 0x1000, 0x2003e000, 0x20030000
    uc.mem_write(stop, b'\0\xbe')
    state = {'minimum_sp': top, 'instructions': 0}
    def instruction(machine, address, size, data):
        state['minimum_sp'] = min(state['minimum_sp'], machine.reg_read(arm.UC_ARM_REG_SP))
        state['instructions'] += 1
    uc.hook_add(UC_HOOK_CODE, instruction)
    document = json.loads((ROOT / 'tools/bible/reader-web.json').read_text(encoding='utf-8'))
    counts = (51, 25, 36, 54, 47, 71, 53, 59, 41, 42, 57,
              50, 38, 31, 27, 33, 26, 40, 42, 31, 25)
    references = [f'{chapter}:{verse}' for chapter, count in enumerate(counts, 1)
                  for verse in range(1, count + 1)]
    if set(document['verses']) != set(references):
        raise ValueError('Canonical source must include exactly the 879 references')
    # Every first page exercises every compressed block. Also fully reconstruct
    # the three longest verses in the normal CI run, including 21:17.
    complete = set(sorted(references, key=lambda ref: len(document['verses'][ref]),
                          reverse=True)[:3])
    maximum, calls, total_instructions, checked_bytes = 0, 0, 0, 0
    max_ref = None
    guard = b'\x91\xa2\xb3\xc4'
    for index, ref in enumerate(references):
        raw = document['verses'][ref]
        chapter, verse = map(int, ref.split(':'))
        selection = chapter | (verse << 8) | (verse << 16)
        expected = raw.translate(TYPOGRAPHY).encode('ascii')
        capacity, page, reconstructed = 157, 1, b''
        while True:
            uc.reg_write(arm.UC_ARM_REG_SP, top)
            uc.reg_write(arm.UC_ARM_REG_LR, stop | 1)
            for register, value in zip((arm.UC_ARM_REG_R0, arm.UC_ARM_REG_R1, arm.UC_ARM_REG_R2, arm.UC_ARM_REG_R3), (selection, page, reply, capacity)):
                uc.reg_write(register, value)
            uc.mem_write(reply-4, guard + b'?'*capacity + guard)
            state['minimum_sp'], state['instructions'] = top, 0
            uc.emu_start(elf.address(name) | 1, stop, timeout=10000000, count=10000000)
            if uc.reg_read(arm.UC_ARM_REG_PC) != stop or uc.reg_read(arm.UC_ARM_REG_SP) != top:
                raise ValueError('Function return/stack restoration failed for ' + ref)
            if bytes(uc.mem_read(reply-4, 4)) != guard or bytes(uc.mem_read(reply+capacity, 4)) != guard:
                raise ValueError('Reply buffer guard overwritten for ' + ref)
            output = bytes(uc.mem_read(reply, capacity))
            if b'\0' not in output:
                raise ValueError('Reply unterminated for ' + ref)
            output = output.split(b'\0', 1)[0]
            header, body = output.split(b'\n', 1)
            if not re.fullmatch(rb'Reader ' + ref.encode() + rb' (?:\(WEB\)|\[' + str(page).encode() + rb'/[0-9]+\])', header):
                raise ValueError('Unexpected header for ' + ref + ': ' + repr(header))
            marker = body.find(b'\nNext: ')
            next_command = body[marker+7:] if marker >= 0 else None
            fragment = body[:marker] if marker >= 0 else body
            if not fragment or expected[len(reconstructed):len(reconstructed)+len(fragment)] != fragment:
                raise ValueError('Canonical linked text mismatch for ' + ref + ' page ' + str(page))
            reconstructed += fragment
            checked_bytes += len(fragment)
            used = top - state['minimum_sp']
            if used + 1536 > 8192:
                raise ValueError('Reader frames exceed 8 KiB with caller reserve: ' + str(used))
            if used > maximum:
                maximum, max_ref = used, ref
            calls += 1
            total_instructions += state['instructions']
            if next_command is None:
                if reconstructed != expected:
                    raise ValueError('Canonical text incomplete for ' + ref)
                break
            if next_command != f'get reader {ref} {page+1}'.encode():
                raise ValueError('Continuation mismatch for ' + ref)
            if not every_page and ref not in complete:
                break
            page += 1
            if page > 32:
                raise ValueError('Unexpected page loop for ' + ref)
        if index % 100 == 0:
            print(f'checked {index+1}/879 references; max stack {maximum}', file=sys.stderr, flush=True)
    source_digest = hashlib.sha256(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'passed': True, 'elf_sha256': hashlib.sha256(elf.data).hexdigest(), 'reply_symbol': name,
            'references': len(document['verses']), 'calls': calls,
            'canonical_bytes_checked': checked_bytes, 'all_pages': every_page,
            'fully_reconstructed_references': references if every_page else sorted(complete),
            'canonical_source_sha256': source_digest,
            'maximum_reply_verse_stack_bytes': maximum, 'maximum_stack_reference': max_ref,
            'caller_reserve_bytes': 1536, 'loop_stack_bytes': 8192,
            'total_arm_instructions': total_instructions,
            'scope': 'Actual linked replyVerse instructions and callees; excludes firmware caller/ISR/task and physical high-water.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('elf')
    parser.add_argument('--every-page', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.elf, args.every_page), indent=2))
    except (ValueError, OSError) as error:
        parser.exit(1, f'FAILED: {error}\n')
