"""Same-image RX-only one/two frequency-write ABBA timing; no RF TX."""
import argparse
import json
from pathlib import Path
import secrets
import time

from profile_switch import exchange
from profile_switch_packets import open_port
from profile_switch_dwell import reboot_boards
from profile_switch_channels import channel_dwell_us


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',required=True)
    parser.add_argument('--sf',type=int,choices=range(5,11),default=10)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():
        parser.error('fresh output required')
    result=dict(experiment='one_two_frequency_write_rx_only_abba',port=args.port,sf=args.sf,
                bw_khz=125,channels=4,preamble_symbols=32,dwell_symbols=5.1,
                settle_us=0,modulation_cache=True,expected_rx_gain_reg=0x94,
                seconds_per_trial=6,order=[False,True,True,False],complete=False,trials=[])
    seq=secrets.randbelow(0x3fffffff)
    port=None
    def save():
        args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    try:
        for repeated in result['order']:
            reboot_boards((args.port,))
            port=open_port(args.port)
            info=exchange(port,'info','bench',3)
            if info.get('frequency_repeat_ab')!=1 or info.get('rx_gain_reg')!=0x94:
                raise RuntimeError('Wrong HIL capability/gain')
            if exchange(port,f'scanfreqrepeat {int(repeated)}','frequency_repeat',2)['frequency_repeat']!=repeated:
                raise RuntimeError('Frequency policy mismatch')
            if not exchange(port,'scanmodcache 1','modulation_cache',2)['modulation_cache']:
                raise RuntimeError('Cache policy mismatch')
            dwell=channel_dwell_us(args.sf,5.1)
            seq+=1
            config=exchange(port,f'scanstart 4 32 {seq} {dwell} 0 {args.sf} 125','listening',3,seq,cached=True)
            time.sleep(6)
            seq+=1
            status=exchange(port,f'scanstatus {seq}','result',3,seq,cached=True)
            result['trials'].append(dict(repeated=repeated,info=info,config=config,status=status))
            save()
            n=status['switch']['n']
            if n<64 or any(status[k] for k in ('failures','rx_mode_errors','cache_errors')):
                raise RuntimeError('Insufficient hops or hardware/configuration error')
            if (status['frequency_repeat']!=repeated or status['rx_gain_reg']!=0x94
                    or status['rf_commands']!=n*(2 if repeated else 1)
                    or status['settle_us']!=0 or status['modulation_commands']!=0):
                raise RuntimeError('Measured command/gain/delay policy mismatch')
            print(f"repeat={repeated}: {n} hops, mean {status['switch']['mean_us']:.3f} us, "
                  f"0x86={status['rf_commands']}, 0x8B={status['modulation_commands']}",flush=True)
            exchange(port,'scanstop','stopped',2)
            port.close();port=None
        result['complete']=True
    except Exception as error:
        result['error']=str(error)
        raise
    finally:
        if port:
            port.close()
        try:
            reboot_boards((args.port,))
        except Exception as error:
            result['cleanup_error']=str(error)
        save()


if __name__=='__main__':
    main()
