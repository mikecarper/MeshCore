"""Full SF10/125 retune twice: ABBA RX-only timing, then strict packet test.

If strict failure is only a correct payload on another RX channel, run a
separately labeled payload diagnostic. Never relabel that as a strict pass.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from profile_switch_rollback import timing_window
from profile_switch_dwell import reboot_boards


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receiver',required=True)
    parser.add_argument('--sender',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--first-pass-detour',action='store_true')
    args=parser.parse_args()
    paths={k:args.output.with_name(args.output.stem+'_'+k+'.json') for k in ('strict','payload')}
    if args.receiver==args.sender or args.output.exists() or any(p.exists() for p in paths.values()):
        parser.error('Different ports and fresh output prefix required')
    result=dict(experiment='sf10_125_full_retune_twice',complete=False,receiver=args.receiver,
                sender=args.sender,channels=4,channel_step_khz=1000,sf=10,bw_khz=125,
                preamble=32,dwell_symbols=5.1,settle_us=0,power_dbm=-9,rx_gain=0x94,
                frequency_writes_per_pass=2,modulation_writes_per_pass=1,retune_passes=2,
                timing=[],captures=[])
    result['first_pass_detour']=args.first_pass_detour
    if args.first_pass_detour:
        result.update(experiment='sf10_125_first_pass_detour',requested_first_pass_offset_khz=0.01,
                      rf_offset_steps=10,first_pass_offset_hz=9.5367431640625,first_pass_cr=6,final_cr=5)
    def save(): args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    def collect(kind):
        reboot_boards((args.receiver,args.sender))
        command=[sys.executable,str(Path(__file__).with_name('profile_switch_channels.py')),
                 '--receiver',args.receiver,'--sender',args.sender,'--output',str(paths[kind]),
                 '--sf','10','--bw-khz','125','--samples','100','--max-channels','4',
                 '--preamble','32','--dwell-symbols','5.1','--channel-step-khz','1000',
                 '--settle-us','0','--frequency-repeat','on','--modulation-cache','off','--retune-passes','2']
        if kind=='payload': command.append('--accept-offchannel')
        if args.first_pass_detour: command.append('--first-pass-detour')
        completed=subprocess.run(command,check=False)
        result['captures'].append(dict(kind=kind,path=paths[kind].name,exit_code=completed.returncode));save()
        if completed.returncode: raise RuntimeError('Collector fixture failure; no RF result')
        capture=json.loads(paths[kind].read_text())
        if not capture['complete']: raise RuntimeError('Incomplete collector')
        level=capture['levels'][0]
        if (level['status']['received']!=level['received']
                or level['status']['missed']!=level['attempted']-level['received']):
            raise RuntimeError('Host/device receive counts disagree')
        return capture
    try:
        save();reboot_boards((args.receiver,args.sender))
        plans=((2,False),(2,True),(2,True),(2,False)) if args.first_pass_detour else ((1,False),(2,False),(2,False),(1,False))
        for passes,detour in plans:
            case=dict(rollback=0,cache=False,repeat=True,retune_passes=passes,channel_step_khz=1000,first_pass_detour=detour)
            control=timing_window(args.receiver,case,0)
            result['timing'].append(dict(retune_passes=passes,first_pass_detour=detour,**control));save()
            print(f"{passes} retune pass(es), detour={detour}: {control['status']['switch']['mean_us']:.3f} us per hop",flush=True)
        strict=collect('strict')
        result['strict_stopped']=strict['stopped']
        if strict['stopped']=='first_rf_miss':
            failed=strict['levels'][0]['trials'][-1]['received']
            if failed.get('payload_valid') is True and not failed.get('valid'):
                diagnostic=collect('payload');result['payload_stopped']=diagnostic['stopped']
        result.update(complete=True,stopped='bounded_tests_complete')
    except Exception as error:
        result.update(complete=False,stopped='fixture_error',error=str(error));raise
    finally:
        try: reboot_boards((args.receiver,args.sender))
        except Exception as error: result['cleanup_error']=str(error)
        save()


if __name__=='__main__': main()
