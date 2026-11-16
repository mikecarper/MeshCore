const test=require('node:test');
const assert=require('node:assert/strict');
const ble=require('../docs/_javascript/lora_ota_ble_port.js');
class Emitter{
  constructor(){this.listeners=new Map();}
  addEventListener(name,fn){this.listeners.set(name,fn);}
  removeEventListener(name,fn){if(this.listeners.get(name)===fn)this.listeners.delete(name);}
  emit(name){this.listeners.get(name)?.({target:this});}
}
function fixture({legacy=false,acknowledge=true}={}){
  const device=new Emitter(),tx=new Emitter();
  tx.notify=bytes=>{tx.value=new DataView(Uint8Array.from(bytes).buffer);tx.emit('characteristicvaluechanged');};
  const rx={writes:[],async writeValueWithResponse(v){
    const bytes=[...v];this.writes.push(bytes);
    if(bytes[0]===0x7f)tx.notify(legacy?[1,1]:bytes);
    else if(acknowledge)tx.notify([0x7e,0,bytes[2]+bytes.length-3]);
  }};
  tx.startNotifications=async()=>tx;
  const service={async getCharacteristic(id){assert.ok([ble.RX,ble.TX].includes(id));return id===ble.RX?rx:tx;}};
  device.gatt={connected:false,async connect(){this.connected=true;return {async getPrimaryService(id){assert.equal(id,ble.SERVICE);return service;}};},disconnect(){this.connected=false;device.emit('gattserverdisconnected');}};
  return {device,tx,rx};
}
test('requests the Nordic UART service from the browser chooser',async()=>{
  let options;const device={};
  assert.equal(await ble.requestDevice({requestDevice(o){options=o;return Promise.resolve(device);}}),device);
  assert.deepEqual(options,{filters:[{services:[ble.SERVICE]}]});
});
test('negotiates and sends a 188-byte frame as acknowledged 20-byte fragments',async()=>{
  const {device,tx,rx}=fixture(),port=ble.create(device);await port.open();assert.equal(port.bleOtaCapable,true);
  const reader=port.readable.getReader(),writer=port.writable.getWriter();
  const payload=Uint8Array.from({length:188},(_,i)=>i),frame=new Uint8Array(191);frame.set([60,188,0]);frame.set(payload,3);
  await writer.write(frame);assert.equal(rx.writes.length,13);let offset=0;
  for(const part of rx.writes.slice(1)){assert.ok(part.length<=20);assert.deepEqual(part.slice(0,3),[0x7e,188,offset]);offset+=part.length-3;}
  assert.equal(offset,188);assert.deepEqual(rx.writes.slice(1).flatMap(v=>v.slice(3)),[...payload]);
  const read=reader.read();tx.notify([0x7e,4,0,5,6]);tx.notify([0x7e,4,2,7,8]);
  assert.deepEqual([...((await read).value)],[62,4,0,5,6,7,8]);
  await reader.cancel();reader.releaseLock();writer.releaseLock();await port.close();
});
test('legacy firmware is rejected before any Companion command',async()=>{
  const {device,rx}=fixture({legacy:true}),port=ble.create(device,{timeout:50});
  await assert.rejects(port.open(),/updated Bluetooth OTA firmware/);assert.equal(port.bleOtaCapable,false);assert.equal(rx.writes.length,1);
});
test('unexpected disconnect rejects pending reads',async()=>{
  const {device}=fixture(),port=ble.create(device);await port.open();
  const reader=port.readable.getReader(),read=reader.read();device.emit('gattserverdisconnected');
  await assert.rejects(read,/disconnected/);reader.releaseLock();await port.close();
});
test('out-of-order fragments fail the connection',async()=>{
  const {device,tx}=fixture(),port=ble.create(device);await port.open();const reader=port.readable.getReader(),read=reader.read();
  tx.notify([0x7e,4,1,7]);await assert.rejects(read,/out of order/);reader.releaseLock();await port.close();
});
test('missing fragment acknowledgement stops and disconnects',async()=>{
  const {device,rx}=fixture({acknowledge:false}),port=ble.create(device,{timeout:20});await port.open();
  const writer=port.writable.getWriter();await assert.rejects(writer.write(Uint8Array.of(60,1,0,22)),/timed out/);
  assert.equal(rx.writes.length,2);assert.equal(port.bleOtaCapable,false);writer.releaseLock();await port.close();
});
test('malformed serial frame does not reach radio',async()=>{
  const {device,rx}=fixture(),port=ble.create(device);await port.open();const writer=port.writable.getWriter();
  await assert.rejects(writer.write(Uint8Array.of(60,4,0,1)),/Invalid/);assert.equal(rx.writes.length,1);
  writer.releaseLock();await port.close();
});
test('accepts larger notifications when the phone negotiates a larger MTU',async()=>{
  const {device,tx}=fixture(),port=ble.create(device);await port.open();const reader=port.readable.getReader(),read=reader.read();
  const payload=Uint8Array.from({length:148},(_,i)=>i);tx.notify([0x7e,148,0,...payload]);
  assert.deepEqual([...((await read).value)],[62,148,0,...payload]);await reader.cancel();reader.releaseLock();await port.close();
});
test('canceling pending Bluetooth connection releases a late connection',async()=>{
  const {device}=fixture();let finish;device.gatt.connect=()=>new Promise(resolve=>{finish=resolve;});
  const controller=new AbortController(),port=ble.create(device,{signal:controller.signal});
  const opening=port.open();controller.abort();await assert.rejects(opening,error=>error.name==='AbortError');
  device.gatt.connected=true;finish({});await new Promise(resolve=>setTimeout(resolve,0));assert.equal(device.gatt.connected,false);
});
test('a stuck Bluetooth write times out without leaving an active session',async()=>{
  const {device,rx}=fixture(),port=ble.create(device,{timeout:20});await port.open();
  rx.writeValueWithResponse=()=>new Promise(()=>{});
  const writer=port.writable.getWriter();await assert.rejects(writer.write(Uint8Array.of(60,1,0,22)),/timed out/);
  assert.equal(device.gatt.connected,false);writer.releaseLock();await port.close();
});
test('a stale connection completion does not disconnect a replacement session',async()=>{
  const {device}=fixture(),original=device.gatt.connect;let finish;device.gatt.connect=()=>new Promise(resolve=>{finish=resolve;});
  const controller=new AbortController(),old=ble.create(device,{signal:controller.signal});const pending=old.open();
  controller.abort();await assert.rejects(pending,error=>error.name==='AbortError');
  device.gatt.connect=original;const next=ble.create(device);await next.open();finish({});await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(device.gatt.connected,true);await next.close();
});
test('preserves a queued unsolicited push before the handshake echo',async()=>{
  const {device,tx,rx}=fixture();rx.writeValueWithResponse=async value=>{const bytes=[...value];rx.writes.push(bytes);tx.notify([0x83]);tx.notify(bytes);};
  const port=ble.create(device);await port.open();const reader=port.readable.getReader();
  assert.deepEqual([...(await reader.read()).value],[62,1,0,0x83]);await reader.cancel();reader.releaseLock();await port.close();
});
test('malformed pre-handshake push cannot enable the OTA capability',async()=>{
  const {device,tx,rx}=fixture();rx.writeValueWithResponse=async value=>{rx.writes.push([...value]);tx.notify([0x80]);};
  const port=ble.create(device);await assert.rejects(port.open(),/updated Bluetooth OTA firmware/);assert.equal(port.bleOtaCapable,false);
});
test('preserves unsolicited push while notifications are still starting',async()=>{
  const {device,tx}=fixture();tx.startNotifications=async()=>{tx.notify([0x83]);return tx;};
  const port=ble.create(device);await port.open();const reader=port.readable.getReader();
  assert.deepEqual([...(await reader.read()).value],[62,1,0,0x83]);await reader.cancel();reader.releaseLock();await port.close();
});
