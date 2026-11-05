#!/usr/bin/env python3
"""Exercise the actual LR2021 RXPS command encoder and staging failures."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'src/helpers/radiolib/CustomLR2021.h').read_text()
start = source.index('    int16_t labStartRxDutyCycle(')
end = source.index('    int16_t labSpiCommand(', start)
method = source[start:end]
program = r'''
#include <cassert>
#include <cstdint>
#include <cstddef>
#include <vector>
#include "helpers/radiolib/W12RxDutyTiming.h"
#include "helpers/radiolib/W12PramLabTrace.h"
using RadioLibIrqFlags_t = uint32_t;
constexpr int RADIOLIB_ERR_NONE=0, RADIOLIB_ERR_INVALID_RX_PERIOD=-1;
constexpr int RADIOLIB_LR2021_RX_TIMEOUT_INF=0xFFFFFF;
constexpr int RADIOLIB_RADIO_MODE_RX=1, RADIOLIB_LR2021_CMD_SET_RX_DUTY_CYCLE=0x0210;
struct RadioModeConfig_t { struct { uint32_t timeout=0, irqFlags=0, irqMask=0, len=0; } receive; };
class Driver {
public:
  int staged=0, result=0, stage_calls=0, spi_calls=0; RadioModeConfig_t config{};
  std::vector<uint8_t> bytes;
  int stageMode(int m, RadioModeConfig_t* c) { assert(m==1); ++stage_calls; config=*c; return staged; }
  int SPIcommand(int op, bool write, uint8_t* data, size_t n) {
    assert(op==0x210 && write); ++spi_calls; bytes.assign(data,data+n); return result;
  }
''' + method + r'''
};
int main() {
  Driver d;
  assert(d.labStartRxDutyCycle(65625,60000,0x135,0x125)==0);
  assert(d.stage_calls==1 && d.spi_calls==1);
  assert(d.config.receive.timeout==0xFFFFFF && d.config.receive.irqFlags==0x135 && d.config.receive.irqMask==0x125);
  assert((d.bytes==std::vector<uint8_t>{0,0x08,0x66,0,0x0f,0xf3,0}));
  d.staged=-7; assert(d.labStartRxDutyCycle(65625,60000,1,1)==-7 && d.spi_calls==1);
  assert(d.labStartRxDutyCycle(0,60000,1,1)==-1 && d.stage_calls==2);
  d.staged=0; d.result=-9; assert(d.labStartRxDutyCycle(65625,60000,1,1)==-9);
}
'''
with tempfile.TemporaryDirectory() as td:
    cpp = Path(td) / 'test.cpp'
    exe = Path(td) / 'test'
    cpp.write_text(program)
    subprocess.run(['g++', '-std=c++17', '-I', str(root / 'src'), str(cpp), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
print('PASS actual LR2021 RXPS staging, timer encoding and failure propagation')
