import json
from serial.tools import list_ports
print(json.dumps([dict(port=p.device,serial=p.serial_number,vid=p.vid,pid=p.pid,product=p.product,location=p.location)
                  for p in list_ports.comports()]),flush=True)
