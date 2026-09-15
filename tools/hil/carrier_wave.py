#!/usr/bin/env python3
"""CW/RX/TX lab check using a separate cw_observer_rak4631 over USB.

Requires preconfigured lab DUT: primary 909.5 MHz / BW125 / SF7 / CR5,
preamble 32, TX -9 dBm. Saves radio2 off/919.5 MHz and toggles RXPS during
testing. Never run against a production node. No bootloader or flashing code.
"""
import argparse
import json
from pathlib import Path
import re
import statistics
import time
import serial


class Bench:
    def __init__(self, dut, observer):
        assert Path(dut).resolve() != Path(observer).resolve()
        self.dut = serial.Serial(dut, 115200, timeout=.02)
        self.observer = serial.Serial(observer, 115200, timeout=.02)
        self.results = {'commands': [], 'measurements': [], 'packets': []}
        time.sleep(.3)
        self.dut.reset_input_buffer(); self.observer.reset_input_buffer()

    def command(self, text, observer=False, wait=.25, timeout=8):
        port = self.observer if observer else self.dut
        port.write((text+'\r\n').encode()); port.flush()
        start = last = time.monotonic(); data = b''
        while time.monotonic()-start < timeout:
            chunk = port.read(8192)
            if chunk: data += chunk; last = time.monotonic()
            complete = (b'\n' in data if observer else
                        b'\r\n> ' in data or
                        any(line.lstrip().startswith(b'-> ') for line in data.splitlines()))
            if complete and time.monotonic()-start > wait and time.monotonic()-last > .12: break
        result = data.decode(errors='replace')
        self.results['commands'].append({'command': text, 'observer': observer, 'reply': result})
        assert result, f'No response: {text}'
        assert not re.search(r'\b(?:Error|ERROR|error)[:\"]', result), (text,result)
        return result

    def listen(self, khz):
        # Match the DUT profile: its preamble-detect hold uses that setting.
        # An unrelated longer probe can time out during dual-profile scanning.
        reply = self.command(f'listen {khz} 125 7 5 32', observer=True)
        assert '"listen_rc":0' in reply, reply

    def wait_profile_mode(self, mode):
        # Flash writes and an earlier RXPS transition can delay the CLI reply.
        # Poll the active profile, rather than count two seconds from USB send.
        end = time.monotonic()+10
        while time.monotonic()<end:
            if f'> {mode};' in self.command('get radio2.status'): return
            time.sleep(.25)
        raise AssertionError(f'radio2 did not become {mode}')

    def measure(self, name, seconds, khz, manual=None, signal=True):
        self.listen(khz)
        self.command('cw off')
        self.observer.reset_input_buffer(); self.dut.reset_input_buffer()
        actual = seconds if manual is None else manual
        window = max(4.5, actual+2.5)
        self.observer.write(f'sample {int(window*1000)}\n'.encode()); self.observer.flush()
        start = time.monotonic(); sent = None; off = False; reply = b''; rows = []
        attempts = 0
        while time.monotonic()-start < window+.2:
            elapsed = time.monotonic()-start
            if sent is None and elapsed >= .75 and attempts < 6:
                self.dut.write(f'{name} on {seconds}\r\n'.encode()); self.dut.flush()
                sent = elapsed; attempts += 1
            if sent is not None and manual is not None and elapsed >= sent+manual and not off:
                self.dut.write(b'cw off\r\n'); self.dut.flush(); off = True
            line = self.observer.readline()
            if line:
                try: row = json.loads(line)
                except (ValueError,UnicodeDecodeError): continue
                if 'rssi_x10' in row:
                    row['host_s'] = time.monotonic()-start; rows.append(row)
            if self.dut.in_waiting:
                reply += self.dut.read(self.dut.in_waiting)
                if b'Error: radio busy' in reply and b'OK - cw' not in reply:
                    reply = b''; sent = None  # RXPS may be in its brief BUSY sleep window
        text = reply.decode(errors='replace')
        result = {'command': name, 'seconds': seconds, 'manual_off': manual, 'listen_khz': khz,
                  'expect_signal': signal, 'reply': text, 'samples': rows, 'attempts': attempts}
        self.results['measurements'].append(result)
        assert sent is not None and f'OK - {name} on' in text, text
        before = [r['rssi_x10']/10 for r in rows if r['host_s'] < sent-.1]
        during = [r['rssi_x10']/10 for r in rows if sent+.06 < r['host_s'] < sent+actual-.03]
        after = [r['rssi_x10']/10 for r in rows if r['host_s'] > sent+actual+.35]
        assert len(before)>=20 and len(during)>=5 and len(after)>=20, 'Insufficient samples'
        b,d,a = map(statistics.median,(before,during,after))
        result.update(before_dbm=b, during_dbm=d, after_dbm=a, rise_db=d-b)
        high = [r for r in rows if r['rssi_x10']/10 > b+12]
        if signal:
            assert d-b >= 15, result | {'samples':'omitted'}
            assert a-b <= 8, result | {'samples':'omitted'}
            measured = high[-1]['ms']/1000-high[0]['ms']/1000+.01
            result['observed_seconds'] = round(measured,3)
            assert abs(measured-actual) <= .18, result | {'samples':'omitted'}
        else:
            assert d-b < 10, result | {'samples':'omitted'}
        assert '> off' in self.command('get cw')
        result['passed'] = True
        print(json.dumps({k:v for k,v in result.items() if k!='samples'}), flush=True)

    def packet_check(self, role, sequence):
        self.listen(909500)
        before = self.command('stats-radio-diag')
        n0 = int(re.search(r'recv=(\d+)',before)[1])
        probe = self.command(f'packet {sequence}',observer=True)
        assert '"tx_rc":0' in probe and '"rx_rc":0' in probe, probe
        time.sleep(.25)
        after = self.command('stats-radio-diag')
        n1 = int(re.search(r'recv=(\d+)',after)[1])
        assert n1>n0, (before,after)
        self.observer.reset_input_buffer()
        advert = self.command('advert.zerohop' if role=='repeater' else 'advert')
        end = time.monotonic()+5; received = []
        while time.monotonic()<end:
            line = self.observer.readline()
            if not line: continue
            try: row=json.loads(line)
            except (ValueError,UnicodeDecodeError): continue
            if row.get('received') and row.get('rc')==0: received.append(row)
        assert received, 'Observer did not receive DUT advert after CW'
        result={'sequence':sequence,'dut_rx_before':n0,'dut_rx_after':n1,'observer_received':received,
                'dut_reply':advert,'passed':True}
        self.results['packets'].append(result)
        print(json.dumps(result),flush=True)

    def run(self,role,second_only=False):
        self.results['version'] = self.command('ver')
        assert '909.5,125,7,5' in self.command('get radio')
        assert '> -9' in self.command('get tx')
        self.command('set radio2 off',wait=2.5)
        self.wait_profile_mode('off')
        self.command('set radio.rxps off')
        if not second_only:
            for sequence,duration in enumerate((.25,1.25),1):
                self.measure('cw',duration,909500)
                self.packet_check(role,sequence)
            self.measure('cw',5,909500,manual=1)
            self.packet_check(role,3)
            self.command('set radio.rxps on')
            self.measure('cw',1.25,909500)
            self.packet_check(role,4)
        self.command('set radio.rxps off')
        self.command('set radio2 919.5,125,7,5,rxtx,32',wait=2.5)
        self.wait_profile_mode('rxtx')
        self.measure('cw2',1.25,919500)
        self.packet_check(role,5)
        self.measure('cw2',.5,909500,signal=False)
        self.command('set radio2 off',wait=2.5)
        self.wait_profile_mode('off')
        self.results['final'] = self.command('stats-radio-diag')
        self.results['passed'] = True

    def close(self):
        try:
            self.dut.write(b'cw off\r\n'); self.dut.flush()
            self.observer.write(b'stop\n'); self.observer.flush()
        finally:
            self.dut.close(); self.observer.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dut',required=True);parser.add_argument('--observer',required=True)
    parser.add_argument('--role',choices=('repeater','companion'),required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--second-only',action='store_true')
    args=parser.parse_args(); bench=Bench(args.dut,args.observer)
    try: bench.run(args.role,args.second_only)
    finally:
        bench.close()
        args.output.write_text(json.dumps(bench.results,indent=2)+'\n')


if __name__=='__main__': main()
