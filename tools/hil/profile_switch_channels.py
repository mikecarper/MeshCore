"""SF5..10, 125/250 kHz scan: stop-first capacity or fixed-sample error rate.

XIAO scanner / Indicator reference recommended. Fixed 32-symbol preamble,
4.8-symbol nominal visits, 16-byte synthetic packets at -9 dBm. This measures
100% of a finite sample, not a proof of a zero packet-error rate. Explicit
--continue-on-miss finishes the four-channel sample without RF retries.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import random
import secrets
import time

from profile_switch import exchange, exchange_ready, read_packet_response
from profile_switch_packets import open_port


def channel_dwell_us(sf, symbols, bw_khz=125):
    if sf not in range(5, 11) or bw_khz not in (125, 250) or not math.isfinite(symbols) or not 4 <= symbols <= 32:
        raise ValueError("SF 5..10, BW 125/250; dwell finite, 4..32 symbols required")
    return math.ceil(symbols * (1 << sf) * (1000 / bw_khz))


def reception_validity(received, channel, accept_offchannel=False):
    strict = (received.get("valid") is True and received.get("len") == 16
              and received.get("channel") == channel)
    payload = (received.get("payload_valid") is True and received.get("len") == 16
               and received.get("payload_channel") == channel and received.get("expected_channel") == channel)
    return strict, strict or (accept_offchannel and payload)


def packet_error_summary(trials, channels):
    """Keep strict attribution errors separate from missing/invalid payloads."""
    def summarize(selected):
        n=len(selected)
        strict=payload=timeouts=0
        for trial in selected:
            s,p=reception_validity(trial['received'],trial['channel'],True)
            strict+=s;payload+=p;timeouts+=trial['received'].get('timeout') is True
        return dict(attempted=n,strict_received=strict,payload_received=payload,
                    offchannel_received=payload-strict,timeouts=timeouts,
                    invalid_payloads=n-payload-timeouts,strict_errors=n-strict,delivery_errors=n-payload,
                    strict_error_percent=100*(n-strict)/n if n else None,
                    delivery_error_percent=100*(n-payload)/n if n else None)
    return dict(overall=summarize(trials),per_channel=[
        dict(channel=ch,**summarize([t for t in trials if t['channel']==ch])) for ch in range(channels)])


def capacity_levels(start, probe, finish, checkpoint, samples=100, maximum=64, seed=606125, dwell_us=2458, settle_us=0, continue_on_miss=False):
    """Stop-first capacity by default; explicit fixed four-channel sample may continue."""
    if continue_on_miss and maximum!=4:
        raise ValueError('Fixed-sample error-rate mode requires exactly four channels')
    rng = random.Random(seed)
    result = {"levels": [], "complete": False, "last_perfect_channels": None}
    for channels in range(4, maximum + 1):
        config = start(channels)
        level = {"channels": channels, "config": config, "attempted": 0, "received": 0,
                 "per_channel": [{"attempted": 0, "received": 0} for _ in range(channels)],
                 "trials": [], "complete": False}
        result["levels"].append(level)
        checkpoint(result)
        for sample_round in range(samples):
            order = list(range(channels))
            rng.shuffle(order)
            for channel in order:
                # Vary arrivals across about two nominal round trips; do not
                # select a known receive phase or tune the listener to TX.
                pause_ms = rng.uniform(0, 2 * channels * ((dwell_us + settle_us) / 1000 + 0.7))
                trial = probe(channel, pause_ms)
                trial.update(channel=channel, sample_round=sample_round + 1, pause_ms=pause_ms)
                level["trials"].append(trial)
                level["attempted"] += 1
                level["per_channel"][channel]["attempted"] += 1
                if trial["valid"]:
                    level["received"] += 1
                    level["per_channel"][channel]["received"] += 1
                elif not continue_on_miss:
                    level["status"] = finish()
                    result.update(complete=True, stopped="first_rf_miss", first_failed_channels=channels,
                                  failed_sequence=trial["sequence"])
                    checkpoint(result)
                    return result
                checkpoint(result)
        level["status"] = finish()
        level["complete"] = True
        if level['received']==level['attempted']:
            result["last_perfect_channels"] = channels
        if continue_on_miss:
            level['error_rates']=packet_error_summary(level['trials'],channels)
        checkpoint(result)
    result.update(complete=True, stopped="fixed_sample_complete" if continue_on_miss else "channel_limit_without_miss")
    checkpoint(result)
    return result


def collect_trace(port, sequence):
    offset, events, metadata = 0, [], None
    while True:
        page = exchange(port, f"scantrace {sequence} {offset}", "result", 3, sequence, cached=True)
        if (page.get("trace") != sequence or page.get("offset") != offset
                or page.get("next") != offset + len(page.get("events", []))
                or not offset <= page.get("next", -1) <= page.get("total", -1)):
            raise RuntimeError("Mismatched diagnostic trace page")
        if metadata is None:
            metadata = {k: v for k, v in page.items() if k not in ("events", "offset", "next")}
        elif page["total"] != metadata["total"] or page["origin_us"] != metadata["origin_us"]:
            raise RuntimeError("Diagnostic trace changed during retrieval")
        events.extend(page["events"])
        offset = page["next"]
        if offset == page["total"]:
            return dict(metadata, events=events)
        if not page["events"]:
            raise RuntimeError("Diagnostic trace made no progress")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receiver", required=True)
    parser.add_argument("--sender", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--max-channels", type=int, default=64)
    parser.add_argument("--preamble", type=int, default=32)
    parser.add_argument("--seed", type=int, default=606125)
    parser.add_argument("--dwell-symbols", type=float, default=4.8)
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--sf", type=int, choices=range(5, 11), default=6)
    parser.add_argument("--bw-khz", type=int, choices=(125, 250), default=125)
    parser.add_argument("--settle-us", type=int, default=0)
    parser.add_argument("--modulation-cache", choices=("on", "off"), default="on")
    parser.add_argument("--frequency-repeat", choices=("on", "off"), default="off")
    parser.add_argument("--rollback", type=int, choices=range(16), default=0)
    parser.add_argument("--retune-passes",type=int,choices=(1,2),default=1)
    parser.add_argument('--first-pass-detour',action='store_true')
    parser.add_argument('--first-pass-offset-hz',type=int,choices=(10,100),default=10)
    parser.add_argument('--continue-on-miss',action='store_true',help='Finish a fixed four-channel sample despite RF misses; never retry packets')
    parser.add_argument("--tcxo-us", type=int, choices=(1600, 6000))
    parser.add_argument("--channel-step-khz", type=int, choices=(250, 1000), default=250)
    parser.add_argument("--accept-offchannel", action="store_true",
                        help="Separate delivery diagnostic: accept intact expected payloads on another RX channel")
    parser.add_argument("--expect-rx-gain", choices=("normal", "boosted"), default="normal")
    args = parser.parse_args()
    if (args.receiver == args.sender or args.output.exists() or not 1 <= args.samples <= 1000
            or not 4 <= args.max_channels <= 64 or not 12 <= args.preamble <= 256
            or not math.isfinite(args.dwell_symbols) or not 4 <= args.dwell_symbols <= 32
            or not 0 <= args.settle_us <= 24000 or (args.channel_step_khz==1000 and args.max_channels!=4)
            or (args.first_pass_detour and args.retune_passes!=2)
            or (args.first_pass_offset_hz!=10 and not args.first_pass_detour)
            or (args.continue_on_miss and args.max_channels!=4)
            or (args.retune_passes==2 and (args.sf!=10 or args.bw_khz!=125 or args.max_channels!=4
                or args.preamble!=32 or args.rollback!=0 or args.modulation_cache!='off'))):
        parser.error("different ports, fresh output, samples 1..1000, channels 4..64, preamble 12..256 required")
    dwell_us = channel_dwell_us(args.sf, args.dwell_symbols, args.bw_khz)
    result = {"schema": 5, "experiment": f"sf{args.sf}_{args.bw_khz}_channel_capacity", "complete": False,
              "receiver": args.receiver, "sender": args.sender, "sf": args.sf, "bw_khz": args.bw_khz,
              "cr": 5, "preamble_symbols": args.preamble, "dwell_symbols": args.dwell_symbols, "dwell_us": dwell_us,
              "modulation_cache": args.modulation_cache == "on",
              "frequency_repeat": args.frequency_repeat == "on",
              "rollback": args.rollback,
              "retune_passes": args.retune_passes,
              "first_pass_detour":args.first_pass_detour,
              "requested_first_pass_offset_hz":args.first_pass_offset_hz if args.first_pass_detour else 0,
              "requested_tcxo_us": args.tcxo_us,
              "settle_us": args.settle_us,
              "expected_rx_gain_reg": 0x94 if args.expect_rx_gain == "normal" else 0x96,
              "trace_enabled": args.trace, "irq_poll_us": 64 if args.trace else 0,
              "samples_per_channel": args.samples, "packet_bytes": 16, "power_dbm": -9,
              "base_mhz": 909.5, "channel_step_mhz": args.channel_step_khz/1000, "seed": args.seed,
              "scope": "Lab N-channel scheduler over production retunes; finite short-range sample, not guaranteed PER"}
    result["accept_offchannel"] = args.accept_offchannel
    result['continue_on_miss']=args.continue_on_miss
    if args.continue_on_miss:
        result['experiment']=f'sf{args.sf}_{args.bw_khz}_fixed_sample_error_rate'
        result['expectation_timeout_ms']=10000
    if args.accept_offchannel:
        result["experiment"] = f"sf{args.sf}_{args.bw_khz}_payload_delivery_diagnostic"
        result["scope"] = "Expected intact payload delivery regardless of RX channel; NOT strict same-channel capacity"
    rx = tx = None
    sequence = secrets.randbelow(0x3fffffff) + 1

    def next_sequence():
        nonlocal sequence
        sequence += 1
        return sequence

    def save(progress=None):
        if progress:
            result.update(progress)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    def start(channels):
        seq = next_sequence()
        reply = exchange_ready(rx, f"scanstart {channels} {args.preamble} {seq} {dwell_us} {int(args.trace)} {args.sf} {args.bw_khz}", "listening", 1, seq, cached=True)
        if (reply.get("channels") != channels or reply.get("sf") != args.sf or reply.get("bw_khz") != args.bw_khz
                or reply.get("preamble") != args.preamble or reply.get("dwell_us") != dwell_us
                or reply.get("trace") != args.trace or reply.get("settle_us",0) != args.settle_us):
            raise RuntimeError("Scanner configuration mismatch")
        if reply.get("rollback", 0) != args.rollback:
            raise RuntimeError("Scanner rollback configuration mismatch")
        if reply.get("step_mhz") != args.channel_step_khz/1000:
            raise RuntimeError("Scanner spacing mismatch")
        print(f"Testing SF{args.sf}/{args.bw_khz}, {channels} channels; {args.samples} packets/channel, {args.preamble}-symbol preamble, {args.dwell_symbols:g}-symbol visits", flush=True)
        return reply

    def probe(channel, pause_ms):
        seq = next_sequence()
        armed_start = time.monotonic_ns()
        armed = exchange(rx, f"scanexpect {channel} {seq}", "listening", 1, seq, cached=True)
        armed_end = time.monotonic_ns()
        if armed.get("channel") != channel or not armed.get("scanning"):
            raise RuntimeError("Expectation mismatch")
        # USB acknowledgement retrieval may take time, but expectation arming
        # itself does not change the scanner's frequency or visit clock.
        time.sleep(pause_ms / 1000)
        with ThreadPoolExecutor(max_workers=1) as pool:
            incoming = pool.submit(read_packet_response, rx, "received", seq, 12)
            sent_start = time.monotonic_ns()
            sent = exchange(tx, f"scantx {channel} {seq} 16 {args.preamble} {args.sf} {args.bw_khz}", "sent", 3, seq, cached=True)
            sent_end = time.monotonic_ns()
            received = incoming.result()
        if (sent.get("rc") != 0 or sent.get("channel") != channel or sent.get("len") != 16
                or sent.get("preamble") != args.preamble or sent.get("sf") != args.sf or sent.get("bw_khz") != args.bw_khz):
            raise RuntimeError(f"Reference TX failed/mismatched; not a capacity result: {sent}")
        if received.get("device_errors") or (received.get("timeout") and received.get("rx_mode") != 1):
            raise RuntimeError(f"Receiver hardware failure; not a capacity result: {received}")
        if result["sender_info"].get("channel_step_select") == 1:
            if sent.get("freq_khz") != 909500+args.channel_step_khz*channel or sent.get("channel_step_khz") != args.channel_step_khz:
                raise RuntimeError("Transmitter frequency/spacing mismatch")
        strict, valid = reception_validity(received, channel, args.accept_offchannel)
        if args.accept_offchannel and not received.get("timeout") and received.get("accepted") != valid:
            raise RuntimeError("Host/receiver payload acceptance mismatch")
        trial = dict(sequence=seq, valid=valid, strict_valid=strict, armed=armed, sent=sent, received=received,
                     host_times_ns=dict(arm_start=armed_start, arm_end=armed_end,
                                        tx_start=sent_start, tx_end=sent_end))
        if args.trace:
            trial["trace"] = collect_trace(rx, seq)
        print(f"Probe {seq}, channel {channel}: {'received' if valid else 'MISSED'}", flush=True)
        return trial

    def finish():
        seq = next_sequence()
        status = exchange(rx, f"scanstatus {seq}", "result", 1, seq, cached=True)
        if any(status[k] for k in ("failures", "rx_mode_errors", "cache_errors")):
            raise RuntimeError(f"Scanner failed; not a capacity result: {status}")
        if status.get("rx_gain_reg") != result["expected_rx_gain_reg"]:
            raise RuntimeError("Receiver gain changed during scan")
        if status.get("settle_us",0) != args.settle_us:
            raise RuntimeError("Settling-delay policy mismatch")
        if status.get("frequency_repeat", False) != result["frequency_repeat"]:
            raise RuntimeError("Frequency-repeat policy mismatch")
        if status.get('retune_passes',1)!=args.retune_passes or status.get('second_pass_blocked',0):
            raise RuntimeError('Full retune repetition policy/guard mismatch')
        if status.get('first_pass_detour',False)!=args.first_pass_detour:
            raise RuntimeError('First-pass detour policy mismatch')
        if args.first_pass_detour and (status['detour']['n']!=status['switch']['n'] or status['detour']['bad']):
            raise RuntimeError('Actual detour/corrected settings did not match every hop')
        if status["rf_commands"] != status["switch"]["n"] * args.retune_passes * (2 if result["frequency_repeat"] else 1):
            raise RuntimeError("Actual 0x86 count does not match the requested per-hop policy")
        if args.retune_passes==2 and (status['modulation_commands']!=2*status['switch']['n']
                or status['optimized_rx_resumes']!=2*status['switch']['n']
                or status['first_pass']['n']!=status['switch']['n']
                or status['second_pass']['n']!=status['switch']['n']):
            raise RuntimeError('Both full retune passes were not physically exercised')
        if args.settle_us and (status["settling"]["n"] != status["switch"]["n"]
                              or status["settling"]["min_us"] < args.settle_us):
            raise RuntimeError("Requested settling delay was not applied to every hop")
        if status.get("rollback", 0) != args.rollback or not status["switch"]["n"]:
            raise RuntimeError("Scanner did not exercise requested retunes")
        if status.get("accept_offchannel",False) != args.accept_offchannel:
            raise RuntimeError("Receive acceptance policy mismatch")
        if status.get('continue_on_miss',False)!=args.continue_on_miss:
            raise RuntimeError('Receive continuation policy mismatch')
        if status.get("mixed_profiles",False):
            raise RuntimeError("Mixed modulation is not a uniform-channel capacity result")
        if args.tcxo_us is not None and status.get("tcxo_us") != args.tcxo_us:
            raise RuntimeError("TCXO delay does not match rollback request")
        if bool(status["optimized_rx_resumes"]) != (args.rollback & 9 == 0):
            raise RuntimeError("Actual fast-RX use disagrees with rollback policy")
        print(f"{status['channels']} channels: {status['received']} received, {status['missed']} missed; "
              f"switch mean {status['switch']['mean_us']:.1f} us; idle dwell mean {status['idle_dwell']['mean_us']:.1f} us", flush=True)
        return status

    try:
        rx = open_port(args.receiver)
        tx = open_port(args.sender)
        for port in (rx, tx):
            info = exchange(port, "info", "bench", 3)
            result["receiver_info" if port is rx else "sender_info"] = info
            if port is rx and info.get("rx_gain_reg") != result["expected_rx_gain_reg"]:
                raise RuntimeError("Receiver gain does not match requested control")
            receiver_missing = (port is rx and (info.get("channel_sweep") != 1
                                or info.get("modulation_cache_ab") != 1
                                or (args.trace and info.get("channel_trace", 0) < 1)))
            sender_missing = (port is tx and info.get("channel_tx") != 1 and info.get("channel_sweep") != 1)
            if (receiver_missing or sender_missing or info.get("channel_sf_select") != 1
                    or info.get("channel_bw_select") != 1):
                raise RuntimeError("Firmware does not support the channel-capacity test")
            if info.get("channel_step_select") == 1:
                if exchange(port,f"scanstep {args.channel_step_khz}","channel_step_khz",2)["channel_step_khz"] != args.channel_step_khz:
                    raise RuntimeError("Channel spacing acknowledgement mismatch")
            elif args.channel_step_khz != 250:
                raise RuntimeError("Both endpoints must support channel spacing selection")
        if result["receiver_info"].get("frequency_repeat_ab") == 1:
            policy = result["frequency_repeat"]
            if exchange(rx, f"scanfreqrepeat {int(policy)}", "frequency_repeat", 2)["frequency_repeat"] != policy:
                raise RuntimeError("Frequency-repeat acknowledgement mismatch")
        elif result["frequency_repeat"]:
            raise RuntimeError("Receiver firmware lacks frequency-repeat capability")
        if args.retune_passes==2:
            capability=exchange(rx,'retuneinfo','full_retune_repeat',2)
            if capability.get('full_retune_repeat')!=1 or capability.get('guarded') is not True:
                raise RuntimeError('Receiver lacks guarded full retune repetition')
            if exchange(rx,'scanretunepasses 2','retune_passes',2)['retune_passes']!=2:
                raise RuntimeError('Full retune repetition acknowledgement mismatch')
        if args.first_pass_detour:
            if exchange(rx,f'scandetouroffset {args.first_pass_offset_hz}','requested_offset_hz',2)['requested_offset_hz']!=args.first_pass_offset_hz:
                raise RuntimeError('First-pass offset acknowledgement mismatch')
            capability=exchange(rx,'detourinfo','first_pass_detour',2)
            steps=105 if args.first_pass_offset_hz==100 else 10
            if any(capability.get(k)!=v for k,v in dict(first_pass_detour=1,requested_offset_khz=args.first_pass_offset_hz/1000,rf_offset_steps=steps,offset_hz=steps*32000000/33554432,first_cr=6,final_cr=5).items()):
                raise RuntimeError('First-pass detour capability mismatch')
            result['detour_configuration']=capability
            if exchange(rx,'scanfirstdetour 1','first_pass_detour',2)['first_pass_detour'] is not True:
                raise RuntimeError('First-pass detour acknowledgement mismatch')
        if result["receiver_info"].get("payload_delivery_ab") == 1:
            if exchange(rx,f"scanacceptoffchannel {int(args.accept_offchannel)}","accept_offchannel",2)["accept_offchannel"] != args.accept_offchannel:
                raise RuntimeError("Acceptance policy acknowledgement mismatch")
        elif args.accept_offchannel:
            raise RuntimeError("Receiver lacks explicitly separate payload-delivery diagnostic")
        if args.continue_on_miss:
            capability=exchange(rx,'continueinfo','fixed_sample_scan',2)
            if capability!=dict(fixed_sample_scan=1,timeout_ms=10000,retune_on_miss=False):
                raise RuntimeError('Unexpected fixed-sample continuation capability')
            if exchange(rx,'scancontinue 1','continue_on_miss',2)['continue_on_miss'] is not True:
                raise RuntimeError('Fixed-sample continuation acknowledgement mismatch')
        policy = args.modulation_cache == "on"
        if args.tcxo_us is not None:
            if exchange(rx, f"scantcxo {args.tcxo_us}", "tcxo_us", 2)["tcxo_us"] != args.tcxo_us:
                raise RuntimeError("TCXO timing acknowledgement mismatch")
        if args.rollback:
            if result["receiver_info"].get("rollback_ab") != 1:
                raise RuntimeError("Receiver lacks rollback controls")
        if result["receiver_info"].get("rollback_ab") == 1:
            if exchange(rx, f"scanrollback {args.rollback}", "rollback", 2)["rollback"] != args.rollback:
                raise RuntimeError("Rollback acknowledgement mismatch")
        if exchange(rx, f"scanmodcache {int(policy)}", "modulation_cache", 2)["modulation_cache"] != policy:
            raise RuntimeError("Modulation-cache policy mismatch")
        if exchange(rx, f"scansettle {args.settle_us}", "settle_us", 2)["settle_us"] != args.settle_us:
            raise RuntimeError("Settling-delay acknowledgement mismatch")
        capacity_levels(start, probe, finish, save, args.samples, args.max_channels, args.seed, dwell_us, args.settle_us,args.continue_on_miss)
        if args.continue_on_miss:
            level=result['levels'][0];status=level['status']
            if status['received']!=level['received'] or status['missed']!=level['attempted']-level['received']:
                raise RuntimeError('Host/device packet counts disagree')
        print(f"Stopped: {result['stopped']}; last perfect channel count: {result['last_perfect_channels']}", flush=True)
    except Exception as error:
        result.update(complete=False, stopped="fixture_error", error=str(error))
        raise
    finally:
        if rx:
            try:
                rx.write(b"scanstop\n")
                rx.flush()
            except Exception:
                pass
        for port in (rx, tx):
            if port:
                port.close()
        save()


if __name__ == "__main__":
    main()
