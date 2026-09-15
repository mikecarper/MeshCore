"""Full SF10/125 four-channel error-rate comparison: 10 Hz then 100 Hz detour."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from profile_switch_dwell import reboot_boards


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receiver',required=True)
    parser.add_argument('--sender',required=True)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    paths={hz:args.output.with_name(args.output.stem+f'_{hz}hz.json') for hz in (10,100)}
    if args.receiver==args.sender or args.output.exists() or any(p.exists() for p in paths.values()):
        parser.error('Different ports and fresh output prefix required')
    result=dict(experiment='sf10_125_10_vs_100hz_full_sample',complete=False,
                started_utc=datetime.now(timezone.utc).isoformat(),receiver=args.receiver,sender=args.sender,
                channels=4,samples_per_channel=100,preamble=32,dwell_symbols=5.1,
                channel_step_khz=1000,settle_us=0,rx_gain=0x94,power_dbm=-9,
                order_hz=[10,100],expectation_timeout_ms=10000,runs=[])
    def save(): args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    try:
        save()
        for hz in result['order_hz']:
            reboot_boards((args.receiver,args.sender))
            run=dict(requested_offset_hz=hz,complete=False,path=paths[hz].name,
                     started_utc=datetime.now(timezone.utc).isoformat())
            result['runs'].append(run);save()
            command=[sys.executable,str(Path(__file__).with_name('profile_switch_channels.py')),
                     '--receiver',args.receiver,'--sender',args.sender,'--output',str(paths[hz]),
                     '--sf','10','--bw-khz','125','--samples','100','--max-channels','4',
                     '--preamble','32','--dwell-symbols','5.1','--channel-step-khz','1000',
                     '--settle-us','0','--frequency-repeat','on','--modulation-cache','off',
                     '--retune-passes','2','--first-pass-detour','--first-pass-offset-hz',str(hz),
                     '--continue-on-miss']
            completed=subprocess.run(command,check=False)
            run['exit_code']=completed.returncode;save()
            if completed.returncode: raise RuntimeError('Collector fixture failure; incomplete sample retained')
            capture=json.loads(paths[hz].read_text())
            if not capture['complete'] or capture['stopped']!='fixed_sample_complete' or len(capture['levels'])!=1:
                raise RuntimeError('Incomplete fixed sample')
            level=capture['levels'][0]
            if (not level['complete'] or level['attempted']!=400 or len(level['per_channel'])!=4
                    or any(ch['attempted']!=100 for ch in level['per_channel'])):
                raise RuntimeError('Not exactly 100 attempts per channel')
            run.update(complete=True,finished_utc=datetime.now(timezone.utc).isoformat(),
                       sha256=hashlib.sha256(paths[hz].read_bytes()).hexdigest(),
                       error_rates=level['error_rates'],status=level['status'],
                       detour_configuration=capture['detour_configuration'])
            print(f"{hz} Hz full sample: {json.dumps(run['error_rates']['overall'])}",flush=True);save()
        result.update(complete=True,stopped='both_400_packet_samples_complete')
    except Exception as error:
        result.update(complete=False,stopped='fixture_error',error=str(error));raise
    finally:
        try: reboot_boards((args.receiver,args.sender))
        except Exception as error: result['cleanup_error']=str(error)
        result['finished_utc']=datetime.now(timezone.utc).isoformat();save()


if __name__=='__main__': main()
