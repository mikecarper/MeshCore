"""Exact five-radio fixture. Never enumerate duplicate CDC interfaces as boards."""
TXS = (
    dict(channel=0, serial='9AB3B64C641BA927', topology='1-1.3.1', board='RAK4631',
         env='profile_fixed_rak_tx', fwid=0xB6, app_base=0x26000, boot_vid=0x239A, boot_pid=0x0029),
    dict(channel=1, serial='651F8E496197F882', topology='1-1.2.3', board='Heltec T096',
         env='profile_fixed_t096_tx', fwid=0xB6, app_base=0x26000, boot_vid=0x239A, boot_pid=0x0071),
    dict(channel=2, serial='9352162A72082314', topology='1-1.2.2', board='Heltec MeshTower V2',
         env='profile_fixed_tower_tx', fwid=0xB6, app_base=0x26000, boot_vid=0x239A, boot_pid=0x0071),
    dict(channel=3, serial='34A9141999729D5D', topology='1-1.2.1', board='Seeed T1000-E LR1110',
         env='profile_fixed_t1000_tx', fwid=0x123, app_base=0x27000, boot_vid=0x2886, boot_pid=0x0057),
)
RX = dict(serial='44:1B:F6:69:CF:98', topology='1-1.3.3', board='Heltec V4', env='profile_four_tx_v4_rx')

def resolve(target, boot=False, idle=True):
    from serial.tools import list_ports
    from pathlib import Path
    import subprocess
    matches=[p for p in list_ports.comports() if p.serial_number==target['serial']
             and p.location==target['topology']+':1.0']
    if len(matches)!=1: raise RuntimeError('Exact primary USB interface missing/ambiguous: '+target['board'])
    p=matches[0]
    if boot and (p.vid,p.pid)!=(target['boot_vid'],target['boot_pid']):
        raise RuntimeError(f'Unexpected bootloader identity: {p}')
    if not boot and target is RX and (p.vid,p.pid)!=(0x303A,0x1001):
        raise RuntimeError('Wrong RX USB chip')
    if idle:
        status=subprocess.run(['fuser',p.device],capture_output=True,timeout=5)
        if status.returncode!=1: raise RuntimeError('Port occupied or ownership check failed: '+p.device)
    return p.device
