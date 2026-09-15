"""Finite two-fixed-TX SF7/62.5 + SF8/500 preamble-32 experiment.

100 stationary controls/profile then three 100/profile scans. No RF retry or
reset on a miss. All result replies are sequence matched; USB replay reads
cached results only. Firmware/identity/permission errors stop the experiment.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import secrets
import subprocess
import time
import serial
from serial.tools import list_ports
from profile_four_tx_fixture import TXS,RX,resolve
from profile_switch import configure_session,exchange,read_packet_response

TARGETS=(TXS[0],TXS[3])
PLAN=({'sf':7,'bw_khz':62.5,'freq_khz':909500},
      {'sf':8,'bw_khz':500,'freq_khz':910500})
PHASES=((0,0),(0,2048),(4000,0))
ROOT=Path(__file__).resolve().parent

def require(row,expected,label):
    if any(row.get(k)!=v for k,v in expected.items()):
        raise RuntimeError(label+': '+str(row))

def open_radio(target,pair=True):
    port=serial.Serial();port.port=resolve(target);port.baudrate=115200
    port.timeout=.2;port.write_timeout=2;configure_session(port);port.open()
    try:
        port.reset_input_buffer()
        info=exchange(port,'info','board',5)
        require(info,dict(board=target['board'],bench='production-profile-switch-v8',ready=True),'identity')
        if target is RX:require(info,dict(rx_gain_reg=0x94),'RX gain')
        else:require(info,dict(sd_fwid=target['fwid'],app_base=target['app_base'],fixed_tx=1,
                               failed=False,autonomous_tx=False),'TX layout')
        if pair:
            plan=exchange(port,'pairinfo','pair_plan',3)
            require(plan,dict(pair_plan=1,preamble=32,switch_budget_us=600,floor_chirps=4.1,
                               profiles=list(PLAN)),'pair image')
        return port,info
    except BaseException:port.close();raise

def wait_enumeration(target):
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        ports=[p for p in list_ports.comports() if p.serial_number==target['serial']
               and p.location==target['topology']+':1.0']
        if len(ports)==1:return
        if len(ports)>1:raise RuntimeError('Ambiguous USB identity')
        time.sleep(.2)
    raise RuntimeError('USB did not re-enumerate')

def reboot(port,target):
    port.write(b'reboot\n');port.flush();time.sleep(.6);port.close()
    time.sleep(2);wait_enumeration(target)
    return open_radio(target)

def check_sent(row,ch,count):
    require(row,dict(PLAN[ch],channel=ch,len=16,preamble=32,rc=0,power_dbm=-9,
                    rf_commands=0,modulation_commands=0,packet_count=count,fixed_tx=True,failed=False),'TX changed/failed')

def check_scan(status,loop,extra,trials):
    n=status['switch']['n'];good=sum(t['valid'] for t in trials)
    require(status,dict(channels=2,active=True,pair_profiles=True,pair_loop_us=loop,pair_slow_extra_us=extra,
              pair_dwell_us=[8397+extra,23171-loop-extra],rx_gain_reg=0x94,retune_passes=1,settle_us=0,
              first_pass_detour=False,frequency_repeat=False,mixed_profiles=False,rollback=0,
              modulation_cache=True,continue_on_miss=True,accept_offchannel=False,
              received=good,strict_received=good,offchannel_received=0,missed=len(trials)-good,
              failures=0,rx_mode_errors=0,cache_errors=0,rx_errors=0,second_pass_blocked=0,
              rf_commands=n,modulation_commands=n,optimized_rx_resumes=n),'scan policy/counts')
    if not n or status['with_modulation']['n']!=n or status['without_modulation']['n']!=0:
        raise RuntimeError('Every SF/BW-changing hop must write modulation once')

def summarize(trials):
    return [dict(channel=ch,attempted=len(rows),received=sum(r['valid'] for r in rows),
                 timeouts=sum(r['received'].get('timeout') is True for r in rows))
            for ch in (0,1) if (rows:=[r for r in trials if r['channel']==ch])]

def main(phases=PHASES):
    if phases not in (PHASES,((300,1024),)):
        raise ValueError('Unreviewed pair experiment')
    path=ROOT/'results.json'
    if path.exists():raise RuntimeError('Capture already exists')
    if not json.loads((ROOT/'deployment.json').read_text()).get('complete'):
        raise RuntimeError('Deployment not verified')
    result=dict(experiment='pair_sf7_62p5_sf8_500_preamble32',complete=False,phase='starting',
        started_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),profiles=PLAN,
        preamble=32,nominal_switch_us=600,normal_rx_gain=True,samples_per_profile=100,
        scan_timeout_ms=1000,rf_retries=0,seed=327628,requested_phases=phases,baseline=[],scans=[])
    counts=[0,0];sequence=secrets.randbelow(0x1fffffff)+1
    rx=None;senders=[]
    def seq():
        nonlocal sequence
        sequence+=1;return sequence
    def save():path.write_text(json.dumps(result,indent=2)+'\n')
    def send(ch,s,timeout):
        with ThreadPoolExecutor(max_workers=1) as pool:
            incoming=pool.submit(read_packet_response,rx,'received',s,timeout)
            sent=exchange(senders[ch],f'pairtx {ch} {s}','sent',3,s,cached=True)
            counts[ch]+=1;check_sent(sent,ch,counts[ch])
            received=incoming.result()
        if received.get('device_errors') or (received.get('timeout') and received.get('rx_mode')!=1):
            raise RuntimeError('RX hardware failure')
        valid=received.get('valid') is True and received.get('len')==16 and received.get('channel')==ch
        if valid:
            expected=int(PLAN[ch]['freq_khz']*(1<<25)/32000)
            if abs(received.get('rf_word_at_read',0)-expected)>4:raise RuntimeError('Wrong RF word')
        return dict(sequence=s,channel=ch,sent=sent,received=received,valid=valid)
    try:
        save()
        for ch,target in enumerate(TARGETS):
            p,info=open_radio(target);senders.append(p)
            require(info,dict(prepared=False,packet_count=0),'non-fresh TX')
            prep=exchange(p,f'pairprepare {ch}','prepared',5)
            require(prep,dict(PLAN[ch],prepared=True,channel=ch,rc=0,preamble=32),'TX prepare')
        rx,info=open_radio(RX);result['receiver']=info
        for ch in (0,1):
            if ch:rx,info=reboot(rx,RX)
            s=seq();setup=exchange(rx,f'basepair {ch} {s}','listening',5,s,cached=True)
            require(setup,dict(PLAN[ch],stationary=True,full_init=True,channel=ch,preamble=32,rx_gain_reg=0x94),'baseline setup')
            stage=dict(channel=ch,setup=setup,trials=[],complete=False)
            result['baseline'].append(stage);result['phase']=f'baseline_{ch}';save()
            rng=random.Random(327628+ch)
            for sample in range(100):
                s=seq();exchange(rx,f'baseexpect {s}','listening',3,s,cached=True)
                pause=rng.uniform(.01,.18);time.sleep(pause)
                row=send(ch,s,5);row['pause_s']=pause;stage['trials'].append(row);save()
                if (sample+1)%10==0:print(json.dumps({'phase':result['phase'],'rates':summarize(stage['trials'])}),flush=True)
            s=seq();stage['status']=exchange(rx,f'basestatus {s}','result',3,s,cached=True)
            good=sum(t['valid'] for t in stage['trials'])
            require(stage['status'],dict(received=good,missed=100-good,rf_commands=0,failures=0,
                          rx_gain_reg=0x94,rx_mode=1,device_errors=0),'baseline counts')
            stage.update(complete=True,received=good);save()
        if any(b['received']!=100 for b in result['baseline']):
            result.update(phase='baseline_failed',stopped='Controls not 100/100; no scan inference');save();return
        for loop,extra in phases:
            rx,info=reboot(rx,RX)
            for cmd,key,value in [('scancontinue 1','continue_on_miss',True),
                                  ('scanmodcache 1','modulation_cache',True)]:
                require(exchange(rx,cmd,key,3),{key:value},'scan policy')
            s=seq();setup=exchange(rx,f'pairstart {s} {loop} {extra}','listening',5,s,cached=True)
            require(setup,dict(channels=2,preamble=32,dwell_us=8397+extra,settle_us=0,
                               spi_mhz=8,bulk=True,trace=False,rollback=0),'pair start')
            stage=dict(loop_us=loop,slow_extra_us=extra,dwell_us=[8397+extra,23171-loop-extra],
                       setup=setup,trials=[],complete=False)
            result['scans'].append(stage);result['phase']=f'scan_loop{loop}_extra{extra}';save()
            time.sleep(5)
            s=seq();stage['timing_before']=exchange(rx,f'scanstatus {s}','result',3,s,cached=True)
            check_scan(stage['timing_before'],loop,extra,[]);save()
            rng=random.Random(327628);order=[0,1]*100;rng.shuffle(order)
            for number,ch in enumerate(order,1):
                s=seq();armed=exchange(rx,f'scanexpect {ch} {s}','listening',3,s,cached=True)
                require(armed,dict(channel=ch,scanning=True),'scan arm')
                pause=rng.uniform(.01,.18);time.sleep(pause)
                row=send(ch,s,3);row['pause_s']=pause
                if row['valid']:
                    require(row['received'],dict(sf_at_read=PLAN[ch]['sf'],bw_khz_at_read=PLAN[ch]['bw_khz']),'RX modulation')
                stage['trials'].append(row);stage['rates']=summarize(stage['trials']);save()
                if number%10==0:print(json.dumps({'phase':result['phase'],'rates':stage['rates']}),flush=True)
            s=seq();stage['status']=exchange(rx,f'scanstatus {s}','result',3,s,cached=True);save()
            check_scan(stage['status'],loop,extra,stage['trials'])
            stage['complete']=True;save()
        result['sender_final']=[exchange(p,'info','board',3) for p in senders]
        for info in result['sender_final']:require(info,dict(packet_count=100*(1+len(phases)),prepared=True,failed=False),'final TX counts')
        result.update(complete=True,phase='complete',finished_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()));save()
    except BaseException as error:
        result.update(error=repr(error),phase='error');save();raise
    finally:
        cleanup=[]
        for target,p in [*zip(TARGETS,senders),(RX,rx)]:
            if p is None:continue
            try:
                restored,info=reboot(p,target)
                try:
                    row={'board':target['board'],'info':info}
                    if target is RX:
                        row['status']=exchange(restored,'scanstatus 1','result',3,1,cached=True)
                        require(row['status'],dict(active=False,channels=0),'RX idle')
                    else:require(info,dict(prepared=False,packet_count=0,failed=False),'TX idle')
                    row['idle']=True;cleanup.append(row)
                finally:restored.close()
            except BaseException as error:
                if p.is_open:p.close()
                cleanup.append({'board':target['board'],'error':repr(error)})
        result['cleanup']=cleanup;save()
        if len(cleanup)!=3 or not all(r.get('idle') for r in cleanup):
            raise RuntimeError('Radio cleanup needs attention')

if __name__=='__main__':main()
