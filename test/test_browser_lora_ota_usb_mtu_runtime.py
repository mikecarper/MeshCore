#!/usr/bin/env python3
"""Keep actual browser OTA packets within the USB Companion's receive limit."""
import os
import unittest

from test_browser_lora_ota_runtime import BROWSER
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_workflow_runtime import legacy_setup_harness


def replace_once(script, before, after):
    if script.count(before) != 1:
        raise AssertionError('Expected one USB-MTU harness anchor: ' + before)
    return script.replace(before, after, 1)


def usb_mtu_harness(*, capable):
    script = legacy_setup_harness()
    # Keep real radio pacing for the larger image; allow a busy CI browser
    # enough time for all data and proof frames without shortening airtime.
    script = replace_once(script, 'const deadline=Date.now()+45000;', 'const deadline=Date.now()+75000;')
    script = replace_once(script, 'let inbox=[],',
        "let droppedUsbFrames=0,rawPayloadSizes=[],received=new Uint8Array(2357),fragments=new Set(),proofBlocks=new Set();let inbox=[],")
    script = replace_once(script,
        'const f=bytes.slice(3);this.frames.push(Array.from(f));let out;',
        """const f=bytes.slice(3);this.frames.push(Array.from(f));let out;
      if(f[0]===65)rawPayloadSizes.push(f.length);
      // Match the firmware receive parser: an oversized command is discarded
      // before dispatch, and consequently supplies no USB acknowledgement.
      if(f.length>USB_LIMIT){droppedUsbFrames++;return;}""".replace('USB_LIMIT', '188' if capable else '176'))
    script = replace_once(script,
        "else if(text==='get usb.raw.max')value='> 188';",
        "else if(text==='get usb.raw.max')value=" + repr('> 188' if capable else 'Unknown command') + ';')
    # Exercise a full 188-byte CMD_SEND_RAW_DATA command, not just a tiny
    # synthetic payload: real seeder replies carry 171 bytes per v2 fragment.
    script = replace_once(script,
        'expected=new Uint8Array(100);const v=new DataView(expected.buffer),at=44;',
        "expected=new Uint8Array(2357);const v=new DataView(expected.buffer),at=2301;for(let i=0;i<at;i++)expected[i]=(i*13)&255;")
    script = replace_once(script, 'self body=44 image=100 base_hash=', 'self body=2301 image=2357 base_hash=')
    script = replace_once(script,
        'this.push(request(6,0,0,1,0));',
        'this.push(request(6,0,0,255,159,1,0,255,159,2,0,255,159));')
    script = replace_once(script,
        'hex(pkg.blob.slice(205,209))',
        'hex(pkg.blob.slice(205,205+pkg.blockCount*4))')
    script = replace_once(script,
        "if(rf[2]===7){if(hex(rf.slice(3,7))!==mid)throw Error('wrong package');const data=rf.slice(11);if(hex(data)!==hex(expected))throw Error('wrong payload');otaData=true;}",
        """if(rf[2]===7){
          if(hex(rf.slice(3,7))!==mid)throw Error('wrong package');
          const index=rf[7]|rf[8]<<8,offset=rf[9]|rf[10]<<8;
          if(!(offset&0x8000)||offset&0x4000||index>=pkg.blockCount)throw Error('wrong v2 fragment geometry');
          const fragment=(offset>>10)&15,data=rf.slice(15),at=index*1024+fragment*171;
          if(!data.length||data.length>171||hex(data)!==hex(expected.slice(at,at+data.length)))throw Error('wrong v2 payload');
          const block=expected.slice(index*1024,Math.min((index+1)*1024,expected.length));
          const stream=new Uint8Array(await crypto.subtle.digest('SHA-256',block)).slice(0,4);
          if(hex(rf.slice(11,15))!==hex(stream))throw Error('wrong v2 stream hash');
          received.set(data,at);fragments.add(index+':'+fragment);
          const count=Math.ceil(expected.length/1024)*6-6+Math.ceil((expected.length%1024)/171);
          otaData=fragments.size===count&&hex(received)===hex(expected);
        }""")
    script = replace_once(script,
        "if(rf[2]===9){if(hex(rf.slice(3,7))!==mid||hex(rf.slice(7))!=='000000')throw Error('wrong proof');proof=true;}",
        """if(rf[2]===9){
          const index=rf[7]|rf[8]<<8;if(hex(rf.slice(3,7))!==mid||index>=pkg.blockCount)throw Error('wrong proof identity');
          let hash=new Uint8Array(await crypto.subtle.digest('SHA-256',expected.slice(index*1024,Math.min((index+1)*1024,expected.length)))).slice(0,4);
          let node=index,width=pkg.blockCount,at=10;
          for(;width>1;node=Math.floor(node/2),width=Math.ceil(width/2))if((node^1)<width){
            const other=rf.slice(at,at+4),pair=new Uint8Array(8);if(other.length!==4)throw Error('short proof');at+=4;
            pair.set(node&1?other:hash);pair.set(node&1?hash:other,4);
            hash=new Uint8Array(await crypto.subtle.digest('SHA-256',pair)).slice(0,4);
          }
          if(at!==rf.length||rf[9]!==((rf.length-10)/4)||hex(hash)!==mid)throw Error('wrong Merkle proof');
          proofBlocks.add(index);proof=proofBlocks.size===pkg.blockCount;
        }""")
    script = replace_once(script,
        'timeline,sourceRxps,targetRxps,targetHops,sourceTemp,targetTemp,frequency:',
        'droppedUsbFrames,rawPayloadSizes,receivedBytes:otaData?received.length:0,reconnectShown:!el(\'reconnect\').hidden,timeline,sourceRxps,targetRxps,targetHops,sourceTemp,targetTemp,frequency:')
    if not capable:
        action = """el('inspect').click();await wait(()=>!el('inspect').disabled);if(el('transfer').disabled||!el('resume').disabled||!el('install').disabled)throw Error('validated legacy OTA preflight did not enable transfer');"""
        stopped = """el('inspect').click();await wait(()=>el('workflow-status').dataset.state==='error');
    await wait(()=>!el('source-update-help').hidden);
    if(!el('update').disabled||!el('transfer').disabled)throw Error('Oversized raw OTA packets remain enabled');
    await report({droppedUsbFrames,rawPayloadSizes,receivedBytes:0,reconnectShown:!el('reconnect').hidden,
      installed,cmds,frames:port.frames,timeline,status:el('status').textContent,
      notice:el('workflow-status').textContent,updateHelp:el('source-update-help').textContent,
      updateHref:el('source-update-link').href});return;"""
        script = replace_once(script, action, stopped)
    return script


class BrowserLoraOtaUsbMtuRuntime(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chromium required')
            self.skipTest('Chromium unavailable')

    def test_full_sized_manifest_and_v2_data_fit_the_advertised_188_byte_usb_limit(self):
        result = run_harness(usb_mtu_harness(capable=True), timeout=120)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['installed'], result)
        self.assertEqual(result['receivedBytes'], 2357, result)
        self.assertIn(187, result['rawPayloadSizes'], result)  # Full manifest fragment.
        self.assertIn(188, result['rawPayloadSizes'], result)  # Full v2 data fragment.
        self.assertLessEqual(max(result['rawPayloadSizes']), 188, result)
        self.assertEqual(result['droppedUsbFrames'], 0, result)
        self.assertFalse(result['reconnectShown'], result)

    def test_an_unknown_legacy_usb_limit_is_rejected_before_retuning_or_starting_a_download(self):
        result = run_harness(usb_mtu_harness(capable=False), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['installed'], result)
        self.assertEqual(result['rawPayloadSizes'], [], result)
        self.assertEqual(result['droppedUsbFrames'], 0, result)
        self.assertFalse(result['reconnectShown'], result)
        self.assertFalse(any(cmd.startswith(('ota pull ', 'tempradio ', 'set tempradio2 ', 'ota config ')) for cmd in result['cmds']), result)
        self.assertFalse(any(row[0] in ('local', 'remote') and row[1].startswith(('tempradio ', 'set tempradio2 ')) for row in result['timeline']), result)
        self.assertIn('Companion', result['updateHelp'], result)
        self.assertIn('role=companionFull', result['updateHref'], result)
        self.assertRegex(result['notice'], r'(?i)(?:USB|packet|firmware)')


if __name__ == '__main__':
    unittest.main()
