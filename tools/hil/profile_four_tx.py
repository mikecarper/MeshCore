"""Four fixed physical transmitters / V4 fast single-pass RX, full finite sample.

First 100 stationary controls per transmitter, then 100 per channel scanning.
RF misses never trigger retries, reinitialization or a shortened sample.
Hardware/USB/identity failures remain fatal and are not labeled RF losses.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import secrets
import subprocess
import time
import serial
from profile_four_tx_fixture import TXS,RX,resolve
from profile_switch import exchange,exchange_ready,read_packet_response,configure_session
from profile_switch_channels import capacity_levels,channel_dwell_us,reception_validity

ROOT=Path(__file__).resolve().parent

def open_radio(target):
    name=resolve(target)
    p=serial.Serial();p.port=name;p.baudrate=115200;p.timeout=.2;p.write_timeout=2
    configure_session(p);p.open();p.reset_input_buffer()
    try:
        # The asynchronous boot greeting has a bench name but no board key.
        # Wait for the actual info reply instead of accepting that greeting.
        info=exchange(p,'info','board',5)
        if info.get('bench')!='production-profile-switch-v8' or not info.get('ready'):
            raise RuntimeError('Wrong/unready bench image')
        return p,info
    except Exception:p.close();raise

def check_sent(sent,t,expected_count):
    expected=dict(channel=t['channel'],len=16,preamble=32,rc=0,power_dbm=-9,sf=10,bw_khz=125,
                  freq_khz=909500+1000*t['channel'],channel_step_khz=1000,rf_commands=0,
                  modulation_commands=0,fixed_tx=True,failed=False,packet_count=expected_count)
    if any(sent.get(k)!=v for k,v in expected.items()):
        raise RuntimeError('Fixed TX failed, retuned, reset or mismatched: '+str(sent))

def check_scan(status,attempted,received):
    n=status['switch']['n']
    with_mod=status['with_modulation']['n'];without_mod=status['without_modulation']['n']
    expected=dict(channels=4,received=received,missed=attempted-received,failures=0,rx_mode_errors=0,
                  cache_errors=0,rx_gain_reg=0x94,retune_passes=1,first_pass_detour=False,
                  frequency_repeat=False,second_pass_blocked=0,rollback=0,settle_us=0,
                  modulation_cache=True,modulation_commands=with_mod,rf_commands=n,optimized_rx_resumes=n,
                  accept_offchannel=False,continue_on_miss=True,mixed_profiles=False)
    # Normal packet RX staging revokes the owned-hop cache. The next hop must
    # acknowledge modulation again; unchanged subsequent hops can omit 0x8B.
    # Count actual cache-hit/miss hops rather than assuming zero writes for
    # an entire run that includes packet processing.
    if not n or not without_mod or with_mod+without_mod!=n or any(status.get(k)!=v for k,v in expected.items()):
        raise RuntimeError('Actual RX command counts/policies/counts disagree: '+str(status))

def parse_args(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dwell-symbols',type=float,default=5.1)
    args=parser.parse_args(argv)
    try:args.dwell_us=channel_dwell_us(10,args.dwell_symbols)
    except ValueError as error:parser.error(str(error))
    return args


def main():
    args=parse_args()
    path=ROOT/'results.json'
    if path.exists():raise RuntimeError('Existing capture; no implicit rerun')
    if not json.loads((ROOT/'deployment.json').read_text()).get('complete'):
        raise RuntimeError('Deployment not verified')
    result=dict(schema=1,experiment='four_fixed_tx_single_pass_sf10_125',complete=False,
                started_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                sf=10,bw_khz=125,cr=5,preamble_symbols=32,dwell_symbols=args.dwell_symbols,
                dwell_us=args.dwell_us,settle_us=0,channel_step_khz=1000,
                retune_passes=1,frequency_repeat=False,modulation_cache=True,normal_rx_gain=True,
                samples_per_channel=100,stationary_samples_per_tx=100,seed=606125,
                transmitters=TXS,receiver=RX,baseline=[],sender_info=[],sender_setup=[],
                rf_power_note='-9 dBm at chip; Heltec external PA paths are not calibrated antenna-port powers')
    rx=None;senders=[];counts=[0]*4;sequence=secrets.randbelow(0x1fffffff)+1
    rng=random.Random(606125)
    def seq():
        nonlocal sequence
        sequence+=1;return sequence
    def save(progress=None):
        if progress:result.update(progress)
        path.write_text(json.dumps(result,indent=2)+'\n')
    def rx_reboot():
        nonlocal rx
        if rx:
            rx.write(b'reboot\n');rx.flush();rx.close();rx=None
        time.sleep(2)
        rx,info=open_radio(RX)
        if info.get('board')!='Heltec V4' or info.get('rx_gain_reg')!=0x94:
            raise RuntimeError('Wrong RX hardware/gain')
        result['receiver_info']=info
        if exchange(rx,'scanstep 1000','channel_step_khz',2)['channel_step_khz']!=1000:
            raise RuntimeError('RX spacing mismatch')
    def send_receive(ch,s,timeout):
        with ThreadPoolExecutor(max_workers=1) as pool:
            incoming=pool.submit(read_packet_response,rx,'received',s,timeout)
            sent=exchange(senders[ch],f'scantx {ch} {s} 16 32 10 125','sent',4,s,cached=True)
            counts[ch]+=1;check_sent(sent,TXS[ch],counts[ch])
            received=incoming.result()
        if received.get('device_errors') or (received.get('timeout') and received.get('rx_mode')!=1):
            raise RuntimeError('Receiver hardware failure: '+str(received))
        return sent,received
    try:
        save()
        for t in TXS:
            p,info=open_radio(t);senders.append(p)
            if any(info.get(k)!=v for k,v in dict(board=t['board'],sd_fwid=t['fwid'],app_base=t['app_base'],
                                                  fixed_tx=1,prepared=False,failed=False,packet_count=0).items()):
                raise RuntimeError('TX identity/layout/non-fresh state mismatch')
            result['sender_info'].append(info)
            prep=exchange(p,f"txprepare {t['channel']}",'prepared',5)
            if any(prep.get(k)!=v for k,v in dict(prepared=True,rc=0,channel=t['channel'],
                        freq_khz=909500+1000*t['channel'],sf=10,bw_khz=125,cr=5,preamble=32).items()):
                raise RuntimeError('TX preparation mismatch: '+str(prep))
            result['sender_setup'].append(prep);save()
        rx_reboot()
        for ch in range(4):
            if ch:rx_reboot()
            s=seq();setup=exchange(rx,f'basestart {ch} 10 {s}','listening',5,s,cached=True)
            if any(setup.get(k)!=v for k,v in dict(channel=ch,freq_khz=909500+1000*ch,
                        sf=10,bw_khz=125,preamble=32,rx_gain_reg=0x94,full_init=True,stationary=True).items()):
                raise RuntimeError('Stationary control setup mismatch')
            baseline=dict(channel=ch,setup=setup,trials=[],complete=False)
            result['baseline'].append(baseline);result['phase']=f'baseline_channel_{ch}';save()
            for sample in range(100):
                s=seq();armed=exchange(rx,f'baseexpect {s}','listening',2,s,cached=True)
                if armed.get('channel')!=ch or not armed.get('stationary'):raise RuntimeError('Baseline arm mismatch')
                time.sleep(rng.uniform(0,.2))
                sent,received=send_receive(ch,s,5)
                valid=received.get('valid') is True and received.get('len')==16 and received.get('channel')==ch
                baseline['trials'].append(dict(sequence=s,channel=ch,sent=sent,received=received,valid=valid))
                save()
                if (sample+1)%10==0:print(json.dumps({'phase':'baseline','channel':ch,'attempted':sample+1,
                                  'received':sum(t['valid'] for t in baseline['trials'])}),flush=True)
            s=seq();status=exchange(rx,f'basestatus {s}','result',3,s,cached=True)
            successes=sum(t['valid'] for t in baseline['trials'])
            if any(status.get(k)!=v for k,v in dict(received=successes,missed=100-successes,rf_commands=0,
                          rx_gain_reg=0x94,rx_mode=1,failures=0,device_errors=0).items()):
                raise RuntimeError('Baseline hardware/counter check failed')
            baseline.update(complete=True,received=successes,status=status);save()
        # Full controls are retained even when RF losses occur. A completely
        # missing link is a fixture problem, not a useful channel-scan capacity test.
        if any(b['received']==0 for b in result['baseline']):raise RuntimeError('A transmitter failed all 100 fixed-channel controls')
        rx_reboot()
        policies=(('scanfirstdetour 0','first_pass_detour',False),('scanretunepasses 1','retune_passes',1),
                  ('scanfreqrepeat 0','frequency_repeat',False),('scanmixed 0','mixed_profiles',False),
                  ('scanrollback 0','rollback',0),('scanmodcache 1','modulation_cache',True),
                  ('scansettle 0','settle_us',0),('scanacceptoffchannel 0','accept_offchannel',False),
                  ('scancontinue 1','continue_on_miss',True))
        result['policies']={}
        for command,key,value in policies:
            ack=exchange(rx,command,key,3)
            if ack.get(key)!=value:raise RuntimeError('RX policy mismatch: '+command)
            result['policies'][key]=value
        result['phase']='four_channel_scan';save()
        def start(channels):
            s=seq();ack=exchange_ready(rx,f'scanstart 4 32 {s} {result["dwell_us"]} 0 10 125','listening',3,s,cached=True)
            if any(ack.get(k)!=v for k,v in dict(channels=4,sf=10,bw_khz=125,preamble=32,
                         dwell_us=result['dwell_us'],settle_us=0,step_mhz=1,spi_mhz=8,bulk=True,rollback=0,trace=False).items()):
                raise RuntimeError('Scan setup mismatch')
            return ack
        def probe(ch,pause_ms):
            s=seq();armed=exchange(rx,f'scanexpect {ch} {s}','listening',2,s,cached=True)
            if armed.get('channel')!=ch or not armed.get('scanning'):raise RuntimeError('Scan arm mismatch')
            time.sleep(pause_ms/1000)
            sent,received=send_receive(ch,s,12)
            strict,valid=reception_validity(received,ch,False)
            trial=dict(sequence=s,channel=ch,armed=armed,sent=sent,received=received,valid=valid,strict_valid=strict)
            attempted=sum(counts)-400
            if attempted%10==0:print(json.dumps({'phase':'scan','attempted':attempted,'last_channel':ch,'last_valid':valid}),flush=True)
            return trial
        def finish():
            s=seq();status=exchange(rx,f'scanstatus {s}','result',3,s,cached=True)
            level=result['levels'][0]
            level['status']=status;save() # Preserve raw telemetry even if validation fails.
            check_scan(status,level['attempted'],level['received'])
            return status
        capacity_levels(start,probe,finish,save,samples=100,maximum=4,seed=606125,
                        dwell_us=result['dwell_us'],settle_us=0,continue_on_miss=True)
        result['sender_final']=[exchange(p,'info','board',3) for p in senders]
        if any(info.get('packet_count')!=200 or not info.get('prepared') or info.get('failed')
               for info in result['sender_final']):raise RuntimeError('TX counts/lifetime mismatch')
        result.update(complete=True,phase='complete',finished_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
        print(json.dumps({'complete':True,'rates':result['levels'][0]['error_rates'],
                          'switch':result['levels'][0]['status']['switch']}),flush=True)
    except Exception as error:
        result.update(complete=False,phase='fixture_error',error=str(error));raise
    finally:
        for p in ([rx] if rx else [])+senders:
            try:p.write(b'reboot\n');p.flush()
            except Exception as error:result.setdefault('cleanup_errors',[]).append(str(error))
            finally:p.close()
        save()

if __name__=='__main__':main()
