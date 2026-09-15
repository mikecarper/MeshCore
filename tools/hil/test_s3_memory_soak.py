import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('soak',Path(__file__).with_name('s3_memory_soak.py'))
soak=importlib.util.module_from_spec(spec)
spec.loader.exec_module(soak)

class TelemetryTests(unittest.TestCase):
    def test_diagnostics_without_key_override_need_no_private_header(self):
        # Preprocess a clean copy: the developer's ignored key header must not
        # satisfy an accidental unconditional include during this check.
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            header=root/'S3SoakDiagnostics.h'
            header.write_bytes(Path(__file__).with_name(header.name).read_bytes())
            for name in ('Arduino.h','WiFi.h','esp_heap_caps.h','helpers/ota/OtaContext.h'):
                dependency=root/name
                dependency.parent.mkdir(parents=True,exist_ok=True)
                dependency.write_text('')
            result=subprocess.run(
                [os.environ.get('CXX','g++'),'-E','-x','c++',
                 '-DMESH_SOAK_DIAGNOSTICS=1','-DESP32_PLATFORM=1',
                 '-I',str(root),str(header),'-o',os.devnull],
                capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def device(self, up, serial=False):
        device=soak.Device('test','/dev/test' if serial else 'localhost:5002','test')
        def command(value):
            if value=='get soak': return 'get soak\r\n  -> '+json.dumps({'up':up})+'\r\n'
            if value=='get mqtt.status': return 'unrelated private log\n  -> > msgs: on, 1: custom (ok), q:0\r\n'
            return 'get mqtt.stats\r\n  -> > Free=100 Max=80 q:0/50 Outbox=0 drops=0/0 | s1=3/0\r\n'
        device.command=command
        return device

    def test_diagnostics_exclude_unrelated_logs(self):
        result=self.device(120,True).sample()
        self.assertEqual(result['mqtt_status'],'msgs: on, 1: custom (ok), q:0')
        self.assertNotIn('mqtt_stats',result)
        self.assertFalse(result['uptime_decreased'])

    def test_reboot_is_reported(self):
        device=self.device(10);device.last_uptime=5000;device.last_sample_time=100
        with patch.object(soak.time,'monotonic',return_value=160): result=device.sample()
        self.assertTrue(result['uptime_decreased'])
        self.assertFalse(result['uptime_wrapped'])

    def test_millis_wrap_is_distinguished_from_reboot(self):
        device=self.device(52);device.last_uptime=4294960;device.last_sample_time=100
        with patch.object(soak.time,'monotonic',return_value=160): result=device.sample()
        self.assertFalse(result['uptime_decreased'])
        self.assertTrue(result['uptime_wrapped'])

    def test_unexpected_decrease_near_wrap_is_reported(self):
        device=self.device(10);device.last_uptime=4294960;device.last_sample_time=100
        with patch.object(soak.time,'monotonic',return_value=160): result=device.sample()
        self.assertTrue(result['uptime_decreased'])

    def test_missing_telemetry_is_not_a_healthy_sample(self):
        device=self.device(0);device.command=lambda _: 'Error: unsupported command\r\n'
        with self.assertRaises(RuntimeError): device.sample()

if __name__=='__main__': unittest.main()
