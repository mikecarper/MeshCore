"""Fixed-frequency off-channel controls: full initialization, no switching or RF retries."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import secrets

from profile_switch import exchange, read_packet_response
from profile_switch_packets import open_port
from profile_switch_dwell import reboot_boards
from profile_stationary_baseline import check_status
from profile_preamble_diagnostic import check_word

# (receiver channel, transmitter channel): interleave positive controls.
PAIRS = ((3,3), (2,3), (1,3), (0,3), (3,2), (2,2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receiver',required=True)
    parser.add_argument('--sender',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--channel-step-khz',type=int,choices=(250,1000),default=250)
    args=parser.parse_args()
    if args.receiver==args.sender or args.output.exists():
        parser.error('Different ports and fresh output required')
    result=dict(experiment='sf10_125_stationary_offset_controls',receiver=args.receiver,
                sender=args.sender,complete=False,sf=10,bw_khz=125,cr=5,preamble_symbols=32,
                power_dbm=-9,expected_rx_gain_reg=0x94,rounds=3,trials=[],
                channel_step_khz=args.channel_step_khz,
                scope='Off-channel valid means matching CRC-clean synthetic payload, not same-channel reception')
    seq=secrets.randbelow(0x3fffffff)+1
    rx=tx=None
    def save():
        args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    try:
        save()
        for repeat in range(1,4):
            for rx_channel,tx_channel in PAIRS:
                reboot_boards((args.receiver,args.sender))
                rx,tx=open_port(args.receiver),open_port(args.sender)
                info=exchange(rx,'info','bench',3)
                if info.get('stationary_offset')!=1 or info.get('rx_gain_reg')!=0x94:
                    raise RuntimeError('Wrong stationary offset firmware/gain')
                tx_info=exchange(tx,'info','bench',3)
                for port,capability in ((rx,info),(tx,tx_info)):
                    if capability.get('channel_step_select')==1:
                        if exchange(port,f'scanstep {args.channel_step_khz}','channel_step_khz',2)['channel_step_khz']!=args.channel_step_khz:
                            raise RuntimeError('Spacing acknowledgement mismatch')
                    elif args.channel_step_khz!=250:
                        raise RuntimeError('Both endpoints must support wider spacing')
                seq+=1
                config=exchange(rx,f'basestart {rx_channel} 10 {seq}','listening',3,seq,cached=True)
                check_word(config,909500+args.channel_step_khz*rx_channel)
                if not config.get('full_init') or not config.get('stationary') or config.get('channel')!=rx_channel:
                    raise RuntimeError('Stationary setup mismatch')
                seq+=1
                arm=exchange(rx,f'baseexpectch {tx_channel} {seq}','listening',2,seq,cached=True)
                if arm.get('channel')!=rx_channel or arm.get('expected_channel')!=tx_channel:
                    raise RuntimeError('Stationary offset expectation mismatch')
                with ThreadPoolExecutor(max_workers=1) as pool:
                    incoming=pool.submit(read_packet_response,rx,'received',seq,5)
                    sent=exchange(tx,f'scantx {tx_channel} {seq} 16 32 10 125','sent',3,seq,cached=True)
                    received=incoming.result()
                expected=dict(rc=0,channel=tx_channel,len=16,preamble=32,sf=10,bw_khz=125,power_dbm=-9)
                if tx_info.get('channel_step_select')==1:
                    expected.update(freq_khz=909500+args.channel_step_khz*tx_channel,
                                    channel_step_khz=args.channel_step_khz)
                if any(sent.get(k)!=v for k,v in expected.items()):
                    raise RuntimeError(f'TX failed/mismatched: {sent}')
                if received.get('channel')!=rx_channel or not received.get('stationary'):
                    raise RuntimeError('Stationary receive identity mismatch')
                valid=received.get('valid') is True
                if valid and (received.get('len')!=16 or received.get('payload_channel')!=tx_channel):
                    raise RuntimeError('Stationary payload identity mismatch')
                check_word(dict(rf_word=received.get('rf_word_at_read')),909500+args.channel_step_khz*rx_channel)
                seq+=1
                status=exchange(rx,f'basestatus {seq}','result',3,seq,cached=True)
                check_status(status,[dict(valid=valid)],909500+args.channel_step_khz*rx_channel)
                trial=dict(repeat=repeat,rx_channel=rx_channel,tx_channel=tx_channel,
                           offset_khz=(rx_channel-tx_channel)*args.channel_step_khz,info=info,tx_info=tx_info,config=config,
                           armed=arm,sent=sent,received=received,status=status,payload_received=valid)
                result['trials'].append(trial);save()
                print(f"Round {repeat} RX{rx_channel}/TX{tx_channel}: payload={valid}, IRQ=0x{received['seen_irq']:04x}, RSSI={received.get('rssi')}, SNR={received.get('snr')}",flush=True)
                exchange(rx,'basestop','stopped',2)
                rx.close();tx.close();rx=tx=None
                if rx_channel==tx_channel and not valid:
                    result['stopped']='on_channel_positive_control_failed';return
        result.update(complete=True,stopped='bounded_controls_complete')
    except Exception as error:
        result.update(complete=False,stopped='fixture_error',error=str(error));raise
    finally:
        for port in (rx,tx):
            if port: port.close()
        try:
            reboot_boards((args.receiver,args.sender))
        except Exception as error:
            result['cleanup_error']=str(error)
        save()


if __name__=='__main__':
    main()
