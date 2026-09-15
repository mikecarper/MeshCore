"""Package already-built bench artifacts locally, with exact DFU/image gates."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile
from intelhex import IntelHex
from profile_four_tx_fixture import TXS,RX

ROOT=Path(__file__).resolve().parents[2]
DEST=ROOT/'out'/'profile-four-tx-20260914'
DEST.mkdir(parents=True,exist_ok=True)
contents={}
manifest={'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
          'scope':'Dirty-worktree HIL artifacts; four physical fixed-channel TXs and one fast single-pass RX',
          'targets':TXS,'receiver':RX,'files':{}}

def add(name,data):
    if name in contents: raise ValueError('Duplicate artifact')
    contents[name]=data
    manifest['files'][name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}

for t in TXS:
    build=ROOT/'.pio/build'/t['env']
    payload=(build/'firmware.zip').read_bytes()
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        m=json.loads(z.read('manifest.json'))['manifest']
        if set(m)!={'application','dfu_version'}: raise ValueError('Not application-only DFU')
        app=m['application']
        if app['init_packet_data']['softdevice_req']!=[t['fwid']]: raise ValueError('Wrong SoftDevice requirement')
        binary=z.read(app['bin_file'])
        if len(binary)>0xED000-t['app_base']: raise ValueError('Image exceeds safe application range')
    image=IntelHex(str(build/'firmware.hex'))
    addresses=image.addresses()
    if min(addresses)!=t['app_base'] or max(addresses)>=0xED000: raise ValueError('HEX writes outside application')
    if bytes(image.tobinarray())!=binary:raise ValueError('DFU binary disagrees with linked application')
    add(t['env']+'.zip',payload)
    manifest['files'][t['env']+'.zip'].update(app_base=min(addresses),image_end=max(addresses)+1,fwid=t['fwid'])
for name in ('firmware.bin','partitions.bin','bootloader.bin'):
    add('rx-'+name,(ROOT/'.pio/build'/RX['env']/name).read_bytes())
add('rx-boot_app0.bin',(Path.home()/'.platformio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin').read_bytes())
for name in ('profile_four_tx_fixture.py','profile_four_tx_deploy.py','profile_four_tx.py',
             'profile_switch.py','profile_switch_channels.py','profile_switch_packets.py',
             'profile_switch_sweep.py'):
    add(name,(ROOT/'tools/hil'/name).read_bytes())
for name in ('profile_fixed_tx.cpp','profile_fixed_tx.ini','profile_switch.cpp','profile_switch_channels.h',
             'ProfileChannelTrace.h','ProfileFrequencyOffset.h'):
    add(name,(ROOT/'tools/hil'/name).read_bytes())
with zipfile.ZipFile(DEST/'bundle.zip','x',zipfile.ZIP_DEFLATED) as z:
    for name,data in contents.items():z.writestr(name,data)
    z.writestr('manifest.json',json.dumps(manifest,indent=2))
(DEST/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({'bundle':str(DEST/'bundle.zip'),'sha256':hashlib.sha256((DEST/'bundle.zip').read_bytes()).hexdigest(),
                  'files':manifest['files']},indent=2))
