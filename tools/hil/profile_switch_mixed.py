"""Bounded equal-symbol-time mixed SF/BW validation, strict channel identity.

Ten fixed-frequency controls per profile, RX-only timing, then 100 packets per
channel at the measured near-ceiling settling delay. If that fails, test zero
added delay once; stop at the first 400/400 pass. No RF retransmissions.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import secrets
import time

from profile_switch import exchange, read_packet_response
from profile_switch_packets import open_port
from profile_switch_dwell import reboot_boards
from profile_switch_channels import capacity_levels, reception_validity
from profile_switch_rollback import settling_budget, PREAMBLE_US, DWELL_US
from profile_stationary_baseline import check_status
from profile_preamble_diagnostic import check_word

PROFILES = [dict(sf=9+i,bw_khz=62.5*(2**i)) for i in range(4)]


def check_plan(reply):
    if reply.get('mixed_profiles')!=1 or reply.get('symbol_us')!=8192 or reply.get('profiles')!=PROFILES:
        raise RuntimeError('Different or unsupported mixed modulation plan')


def check_scan(status, settle):
    n=status['switch']['n']
    if (not n or status.get('mixed_profiles') is not True
            or status.get('accept_offchannel') is not False or status.get('rollback')!=0
            or status.get('rx_gain_reg')!=0x94 or status.get('channel_step_khz')!=1000
            or status.get('settle_us')!=settle or status.get('frequency_repeat') is not True
            or status.get('modulation_cache') is not True
            or status['rf_commands']!=2*n or status['modulation_commands']!=n
            or status['optimized_rx_resumes']!=n
            or any(status[k] for k in ('failures','rx_mode_errors','cache_errors','rx_errors'))):
        raise RuntimeError('Mixed scan configuration/command counts/hardware mismatch')
    if settle and (status['settling']['n']!=n or status['settling']['min_us']<settle):
        raise RuntimeError('Settling not applied on every hop')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receiver',required=True)
    parser.add_argument('--sender',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.receiver==args.sender or args.output.exists():
        parser.error('Different ports and fresh output required')
    result=dict(experiment='mixed_sf_bw_equal_symbol_time',complete=False,receiver=args.receiver,
                sender=args.sender,profiles=PROFILES,frequencies_khz=[909500+1000*i for i in range(4)],
                symbol_us=8192,preamble_symbols=32,preamble_us=PREAMBLE_US,dwell_symbols=5.1,
                dwell_us=DWELL_US,power_dbm=-9,normal_rx_gain=0x94,frequency_repeat=True,
                samples_per_channel=100,baseline_samples_per_channel=10,trace_enabled=False,
                accept_offchannel=False,seed=606125,baselines=[],scans=[],timing_controls=[])
    sequence=secrets.randbelow(0x3fffffff)+1
    rx=tx=None

    def seq():
        nonlocal sequence
        sequence+=1
        return sequence

    def save():
        args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')

    def close():
        nonlocal rx,tx
        for port in (rx,tx):
            if port: port.close()
        rx=tx=None

    def connect():
        nonlocal rx,tx
        close();reboot_boards((args.receiver,args.sender))
        rx,tx=open_port(args.receiver),open_port(args.sender)
        for key,port in (('receiver',rx),('sender',tx)):
            info=exchange(port,'info','bench',3)
            result[key+'_info']=info
            if not info.get('ready') or (port is rx and info.get('rx_gain_reg')!=0x94):
                raise RuntimeError('Wrong mixed test fixture/gain')
            check_plan(exchange(port,'mixinfo','mixed_profiles',3))
            if exchange(port,'scanstep 1000','channel_step_khz',2)['channel_step_khz']!=1000:
                raise RuntimeError('Spacing mismatch')

    def transmit(channel,s,stationary=False):
        with ThreadPoolExecutor(max_workers=1) as pool:
            incoming=pool.submit(read_packet_response,rx,'received',s,5 if stationary else 12)
            sent=exchange(tx,f'mixtx {channel} {s}','sent',3,s,cached=True)
            received=incoming.result()
        expected=dict(PROFILES[channel],rc=0,channel=channel,len=16,preamble=32,
                      freq_khz=909500+1000*channel,channel_step_khz=1000,power_dbm=-9)
        if any(sent.get(k)!=v for k,v in expected.items()):
            raise RuntimeError(f'TX failed/mismatched, not an RF miss: {sent}')
        if received.get('device_errors') or (received.get('timeout') and received.get('rx_mode')!=1):
            raise RuntimeError('RX hardware failure, not an RF miss')
        if not received.get('timeout'):
            check_word(dict(rf_word=received.get('rf_word_at_read')),909500+1000*received['channel'])
            if not stationary:
                actual=PROFILES[received['channel']]
                if received.get('sf_at_read')!=actual['sf'] or received.get('bw_khz_at_read')!=actual['bw_khz']:
                    raise RuntimeError('RX modulation/cache at read mismatch')
        return sent,received

    def configure_scan(settle):
        connect()
        for command,key,value in (('scanmixed','mixed_profiles',True),('scanrollback','rollback',0),
                ('scanmodcache','modulation_cache',True),('scanfreqrepeat','frequency_repeat',True),
                ('scanacceptoffchannel','accept_offchannel',False),('scansettle','settle_us',settle)):
            if exchange(rx,f'{command} {int(value)}',key,2)[key]!=value:
                raise RuntimeError('Scanner policy acknowledgement mismatch')

    def start_scan(channels):
        s=seq()
        reply=exchange(rx,f'scanstart 4 32 {s} {DWELL_US} 0 10 125','listening',3,s,cached=True)
        if any(reply.get(k)!=v for k,v in dict(channels=4,dwell_us=DWELL_US,preamble=32,
                trace=False,step_mhz=1.0,sf=10,bw_khz=125).items()):
            raise RuntimeError('Mixed scanner timing anchor mismatch')
        return reply

    def status(settle):
        s=seq();value=exchange(rx,f'scanstatus {s}','result',2,s,cached=True)
        check_scan(value,settle)
        return value

    def timing(settle):
        configure_scan(settle);config=start_scan(4);time.sleep(6)
        value=status(settle)
        if value['switch']['n']<64:
            raise RuntimeError('Insufficient measured retunes')
        result['timing_controls'].append(dict(settle_us=settle,config=config,status=value));save()
        return value

    try:
        save()
        rng=random.Random(606125)
        for channel in range(4):
            connect();s=seq()
            setup=exchange(rx,f'basemixed {channel} {s}','listening',3,s,cached=True)
            expected=dict(PROFILES[channel],channel=channel,full_init=True,stationary=True,
                          freq_khz=909500+1000*channel,preamble=32,rx_gain_reg=0x94)
            check_word(setup,expected['freq_khz'])
            if any(setup.get(k)!=v for k,v in expected.items()):
                raise RuntimeError('Mixed stationary setup mismatch')
            baseline=dict(channel=channel,setup=setup,trials=[],complete=False)
            result['baselines'].append(baseline);save()
            for sample in range(10):
                s=seq();armed=exchange(rx,f'baseexpect {s}','listening',2,s,cached=True)
                if armed.get('channel')!=channel or not armed.get('stationary'):
                    raise RuntimeError('Baseline expectation mismatch')
                time.sleep(rng.uniform(0,.2));sent,received=transmit(channel,s,True)
                valid=received.get('valid') is True and received.get('len')==16 and received.get('channel')==channel
                baseline['trials'].append(dict(sequence=s,valid=valid,sent=sent,received=received));save()
            s=seq();baseline['status']=exchange(rx,f'basestatus {s}','result',2,s,cached=True)
            check_status(baseline['status'],baseline['trials'],expected['freq_khz'])
            baseline.update(complete=True,received=sum(t['valid'] for t in baseline['trials']))
            print(f"Mixed baseline channel {channel} {PROFILES[channel]}: {baseline['received']}/10",flush=True);save()
            if baseline['received']!=10:
                result.update(complete=True,stopped='baseline_failed');return
        control=timing(0)
        ceiling=settling_budget(control['switch']['max_us'])
        verified=timing(ceiling)
        if verified['idle_cycle']['n']<8 or verified['idle_cycle']['max_us']>=PREAMBLE_US:
            raise RuntimeError('Near-ceiling control does not fit measured preamble budget')
        for settle in dict.fromkeys((ceiling,0)):
            configure_scan(settle)
            scan=dict(settle_us=settle,complete=False)
            result['scans'].append(scan);save()
            def checkpoint(progress):
                scan.update(progress);save()
            def probe(channel,pause_ms):
                s=seq();armed=exchange(rx,f'scanexpect {channel} {s}','listening',2,s,cached=True)
                if armed.get('channel')!=channel or not armed.get('scanning'):
                    raise RuntimeError('Scan expectation mismatch')
                time.sleep(pause_ms/1000);sent,received=transmit(channel,s)
                strict,valid=reception_validity(received,channel)
                print(f'Mixed +{settle} us ch{channel}: {"received" if valid else "MISSED"}',flush=True)
                return dict(sequence=s,valid=valid,strict_valid=strict,sent=sent,received=received,armed=armed)
            capacity_levels(start_scan,probe,lambda:status(settle),checkpoint,100,4,606125,DWELL_US,settle)
            if scan['stopped']=='channel_limit_without_miss':
                result.update(complete=True,stopped='first_400_of_400_pass',passed_settle_us=settle);return
        result.update(complete=True,stopped='both_delay_controls_failed')
    except Exception as error:
        result.update(complete=False,stopped='fixture_error',error=str(error));raise
    finally:
        close()
        try: reboot_boards((args.receiver,args.sender))
        except Exception as error: result['cleanup_error']=str(error)
        save()


if __name__=='__main__': main()
