"""Package reviewed firmware and synthetic-test tools, with SHA-256 provenance."""
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import zipfile
from intelhex import IntelHex
from profile_four_tx_fixture import TXS,RX

root=Path(__file__).resolve().parents[2]
dest=root/'out/pair-sf8-32-20260914'
if dest.exists():raise RuntimeError('Bundle already exists')
dest.mkdir(parents=True)
contents={};manifest={'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
                     'scope':'Dirty-worktree HIL only; application updates for RAK4631/T1000-E/V4','files':{}}
def add(name,data):
    contents[name]=data;manifest['files'][name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
for target in (TXS[0],TXS[3]):
    build=root/'.pio/build'/target['env'];data=(build/'firmware.zip').read_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        m=json.loads(z.read('manifest.json'))['manifest']
        if set(m)!={'application','dfu_version'} or m['application']['init_packet_data']['softdevice_req']!=[target['fwid']]:
            raise RuntimeError('Wrong DFU type/layout')
        binary=z.read(m['application']['bin_file'])
    h=IntelHex(str(build/'firmware.hex'));addresses=h.addresses()
    if min(addresses)!=target['app_base'] or max(addresses)>=0xED000 or bytes(h.tobinarray())!=binary:
        raise RuntimeError('Application payload does not match address-constrained HEX')
    add(target['env']+'.zip',data)
    manifest['files'][target['env']+'.zip'].update(app_base=min(addresses),image_end=max(addresses)+1)
for name in ('firmware.bin','partitions.bin'):add('rx-'+name,(root/'.pio/build'/RX['env']/name).read_bytes())
for name in ('profile_pair.py','profile_pair_deploy.py','profile_pair_run.py','profile_switch.py','profile_four_tx_fixture.py',
             'ProfilePairPlan.h','ProfileSwitchUsb.h','profile_fixed_tx.cpp','profile_fixed_tx.ini',
             'profile_switch.cpp','profile_switch.ini','profile_switch_channels.h','profile_switch_experiments.h',
             'profile_stationary_baseline.h','ProfileChannelVisitClock.h','ProfileChannelTrace.h','ProfileFrequencyOffset.h'):
    add(name,(root/'tools/hil'/name).read_bytes())
for name in ('RadioProfiles.h','helpers/radiolib/CustomSX1262.h','helpers/radiolib/RadioLibWrappers.cpp',
             'helpers/radiolib/RadioLibWrappers.h','helpers/radiolib/SX1262ProfileSwitchState.h'):
    add('source_'+name.replace('/','_'),(root/'src'/name).read_bytes())
with zipfile.ZipFile(dest/'bundle.zip','x',zipfile.ZIP_DEFLATED) as z:
    for name,data in contents.items():z.writestr(name,data)
    z.writestr('manifest.json',json.dumps(manifest,indent=2))
(dest/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
data=(dest/'bundle.zip').read_bytes();digest=hashlib.sha256(data).digest()
for offset in range(0,len(data),32768):
    (dest/f'chunk-{offset//32768:03d}.bin').write_bytes(struct.pack('<II',offset,len(data))+digest+data[offset:offset+32768])
print(json.dumps({'bundle':str(dest/'bundle.zip'),'bytes':len(data),'sha256':digest.hex(),'chunks':(len(data)+32767)//32768}))
