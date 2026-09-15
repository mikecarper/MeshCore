import ast
import copy
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parent))
from profile_four_tx import check_scan,check_sent,parse_args
from profile_four_tx_fixture import TXS,RX,resolve

class FourTransmitterTests(unittest.TestCase):
    def test_dwell_override_changes_only_requested_visit_time(self):
        default=parse_args([])
        self.assertEqual((default.dwell_symbols,default.dwell_us),(5.1,41780))
        longer=parse_args(['--dwell-symbols','6.1'])
        self.assertEqual((longer.dwell_symbols,longer.dwell_us),(6.1,49972))
        near_limit=parse_args(['--dwell-symbols','7.7'])
        self.assertEqual((near_limit.dwell_symbols,near_limit.dwell_us),(7.7,63079))
        for value in ('nan','inf','-1','3.9','32.1'):
            with self.subTest(value=value),patch('sys.stderr'),self.assertRaises(SystemExit):
                parse_args(['--dwell-symbols',value])

    def test_offline_analyzer_preserves_capture_and_rejects_partial_run(self):
        root=Path(__file__).resolve().parent
        source=root/'profile_four_tx_sf10_single_results.json'
        before=source.read_bytes()
        with tempfile.TemporaryDirectory(prefix='four-tx-analysis-') as folder:
            output=Path(folder)/'validated.json'
            command=[sys.executable,str(root/'profile_four_tx_analyze.py'),'--input',str(source),'--output',str(output)]
            completed=subprocess.run(command,capture_output=True,text=True,timeout=10)
            self.assertEqual(completed.returncode,0,completed.stderr)
            report=json.loads(output.read_text())
            self.assertEqual(report['rates']['overall']['strict_received'],392)
            self.assertEqual(report['dwell_us'],41780)
            self.assertFalse(report['original_collector_complete'])
            self.assertTrue(report['post_run_validation_passed'])
            self.assertNotEqual(subprocess.run(command,capture_output=True,timeout=10).returncode,0)
            partial=root/'profile_four_tx_sf10_6p1_results.json'
            partial_output=Path(folder)/'partial.json'
            failed=subprocess.run([sys.executable,str(root/'profile_four_tx_analyze.py'),
                '--input',str(partial),'--output',str(partial_output)],capture_output=True,text=True,timeout=10)
            self.assertNotEqual(failed.returncode,0)
            self.assertIn('Not a complete 400-packet capture',failed.stderr)
            self.assertFalse(partial_output.exists())
        self.assertEqual(source.read_bytes(),before)

    def test_five_distinct_physical_boards(self):
        self.assertEqual(len({t['serial'] for t in (*TXS,RX)}),5)
        self.assertEqual([t['channel'] for t in TXS],[0,1,2,3])
        self.assertNotIn('0B81C9C68D8D01B4',{t['serial'] for t in (*TXS,RX)})

    def test_duplicate_cdc_not_an_extra_board(self):
        t=TXS[1]
        ports=[SimpleNamespace(serial_number=t['serial'],location=t['topology']+':1.0',device='/dev/ttyACM2',vid=0x239A,pid=0x71),
               SimpleNamespace(serial_number=t['serial'],location=t['topology']+':1.2',device='/dev/ttyACM3',vid=0x239A,pid=0x71)]
        with patch('serial.tools.list_ports.comports',return_value=ports):
            self.assertEqual(resolve(t,boot=True,idle=False),'/dev/ttyACM2')
        ports[0].location='1-1.9:1.0'
        with patch('serial.tools.list_ports.comports',return_value=ports):
            with self.assertRaises(RuntimeError):resolve(t,boot=True,idle=False)

    def test_fixed_tx_rejects_retune_reset_and_wrong_channel(self):
        t=TXS[0]
        good=dict(channel=0,len=16,preamble=32,rc=0,power_dbm=-9,sf=10,bw_khz=125,freq_khz=909500,
                  channel_step_khz=1000,rf_commands=0,modulation_commands=0,fixed_tx=True,failed=False,packet_count=101)
        check_sent(good,t,101)
        for key,value in [('rf_commands',1),('modulation_commands',1),('channel',1),('packet_count',1),('rc',-5),('failed',True)]:
            bad=dict(good);bad[key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):check_sent(bad,t,101)

    def test_rx_requires_exact_single_pass_fast_counts(self):
        good=dict(switch={'n':99},with_modulation={'n':7},without_modulation={'n':92},channels=4,received=300,missed=100,failures=0,rx_mode_errors=0,cache_errors=0,
                  rx_gain_reg=0x94,retune_passes=1,first_pass_detour=False,frequency_repeat=False,
                  second_pass_blocked=0,rollback=0,settle_us=0,modulation_cache=True,modulation_commands=7,
                  rf_commands=99,optimized_rx_resumes=99,accept_offchannel=False,continue_on_miss=True,mixed_profiles=False)
        check_scan(good,400,300)
        pure=copy.deepcopy(good);pure.update(with_modulation={'n':0},without_modulation={'n':99},modulation_commands=0)
        check_scan(pure,400,300)
        for key,value in [('retune_passes',2),('rf_commands',198),('modulation_commands',99),('frequency_repeat',True),
                          ('optimized_rx_resumes',0),('settle_us',100),('continue_on_miss',False),('rx_gain_reg',0x96)]:
            bad=copy.deepcopy(good);bad[key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):check_scan(bad,400,300)
        bad=copy.deepcopy(good);bad['with_modulation']['n']=8
        with self.assertRaises(RuntimeError):check_scan(bad,400,300)

    def test_transmit_body_cannot_retune(self):
        source=(Path(__file__).with_name('profile_fixed_tx.cpp')).read_text()
        body=source.split('void transmit(',1)[1].split('void setup()',1)[0]
        body=re.sub(r'//[^\n]*','',body)
        self.assertNotRegex(body,r'\.(begin|std_init|setFrequency|setSpreadingFactor|setBandwidth|setCodingRate|setLoRaModulationParams)\(')
        self.assertIn('seq<=lastSequence',body)
        self.assertLess(body.index('lastSequence=seq'),body.index('chip.transmit('))
        self.assertIn('channel!=fixedChannel',body)

    def test_completed_capture_and_post_packet_cache_refresh(self):
        capture=json.loads(Path(__file__).with_name('profile_four_tx_sf10_single_results.json').read_text())
        # Preserve the original collector failure; verify its saved telemetry
        # independently rather than rewriting the raw run as a clean exit.
        self.assertFalse(capture['complete'])
        prefix='Actual RX command counts/policies/counts disagree: '
        self.assertTrue(capture['error'].startswith(prefix))
        status=ast.literal_eval(capture['error'][len(prefix):])
        scan=capture['levels'][0]['trials']
        self.assertEqual(len(scan),400)
        self.assertEqual([sum(t['valid'] for t in scan if t['channel']==ch) for ch in range(4)],
                         [96,98,100,98])
        check_scan(status,400,392)
        self.assertEqual(status['modulation_commands'],392)
        self.assertEqual(status['switch']['n'],7206)
        self.assertEqual(status['without_modulation']['n'],6814)
        baseline=[t for b in capture['baseline'] for t in b['trials']]
        self.assertEqual(len(baseline),400)
        self.assertTrue(all(t['valid'] for t in baseline))
        trials=baseline+scan
        self.assertEqual(len({t['sequence'] for t in trials}),800)
        for ch,target in enumerate(TXS):
            selected=[t for t in trials if t['channel']==ch]
            self.assertEqual(len(selected),200)
            for count,trial in enumerate(selected,1):
                check_sent(trial['sent'],target,count)
                self.assertEqual(trial['sent']['sent'],trial['sequence'])
                self.assertEqual(trial['received']['received'],trial['sequence'])
                self.assertFalse(trial['sent'].get('transport_recovered'))
                self.assertFalse(trial['received'].get('transport_recovered'))
                if trial['valid']:
                    self.assertEqual(trial['received']['channel'],ch)
                    self.assertEqual(trial['received']['payload_channel'],ch)
                    self.assertEqual(trial['received']['len'],16)
                else:
                    self.assertTrue(trial['received']['timeout'])
                    self.assertEqual(trial['received']['device_errors'],0)

    def test_completed_7p7_capture_and_preserved_cleanup_warnings(self):
        root=Path(__file__).resolve().parent
        capture=json.loads((root/'profile_four_tx_sf10_7p7_results.json').read_text())
        level=capture['levels'][0]
        self.assertTrue(capture['complete'])
        self.assertEqual(capture['phase'],'complete')
        self.assertEqual((capture['dwell_symbols'],capture['dwell_us']),(7.7,63079))
        self.assertEqual(level['config']['dwell_us'],63079)
        self.assertEqual(len(level['trials']),400)
        self.assertEqual([c['received'] for c in level['per_channel']],[100,99,97,99])
        check_scan(level['status'],400,395)
        self.assertEqual(level['status']['rf_commands'],5046)
        self.assertEqual(level['status']['modulation_commands'],395)
        self.assertEqual([i['packet_count'] for i in capture['sender_final']],[200]*4)
        cleanup=json.loads((root/'profile_four_tx_sf10_7p7_cleanup.json').read_text())
        self.assertFalse(cleanup['verified_idle']) # Do not erase reboot warnings.
        self.assertEqual(len(cleanup['errors']),3)
        self.assertEqual(len(cleanup['radios']),5)
        for row in cleanup['radios']:
            if row['board']=='Heltec V4':
                self.assertFalse(row['status']['active'])
                self.assertEqual(row['status']['channels'],0)
            else:
                self.assertFalse(row['info']['prepared'])
                self.assertEqual(row['info']['packet_count'],0)
                self.assertFalse(row['info']['autonomous_tx'])

if __name__=='__main__':unittest.main()
