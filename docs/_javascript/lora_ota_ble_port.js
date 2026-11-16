/* Serial-like adapter for the Companion's negotiated Bluetooth OTA protocol. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.LoraOtaBlePort=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const SERVICE='6e400001-b5a3-f393-e0a9-e50e24dcca9e';
  const RX='6e400002-b5a3-f393-e0a9-e50e24dcca9e';
  const TX='6e400003-b5a3-f393-e0a9-e50e24dcca9e';
  const HELLO=Uint8Array.of(0x7f,0x4f,0x54,0x41,1),FRAGMENT=0x7e;
  const MAX_FRAME=188, MAX_BUFFER=1024*1024, FRAGMENT_DATA=17, TIMEOUT=5000;
  const deviceOwners=new WeakMap();
  function validPreHandshakePush(data){
    const code=data[0],n=data.length;
    if(code<0x80||code>0x92||n>MAX_FRAME)return false;
    if(code===0x83||code===0x90)return n===1;
    if(code===0x80||code===0x81)return n===33;
    if(code===0x8a)return n===148;
    return n>=2;
  }
  function requestDevice(bluetooth=globalThis.navigator?.bluetooth){
    if(!bluetooth||typeof bluetooth.requestDevice!=='function')throw new Error('Bluetooth is unavailable in this browser.');
    return bluetooth.requestDevice({filters:[{services:[SERVICE]}]});
  }
  class BlePort{
    constructor(device,{timeout=TIMEOUT,openTimeout=60000,signal}={}){
      if(!device?.gatt||typeof device.gatt.connect!=='function')throw new TypeError('Choose a Bluetooth Companion.');
      if(!Number.isFinite(timeout)||timeout<=0||!Number.isFinite(openTimeout)||openTimeout<=0)throw new RangeError('Invalid Bluetooth timeout.');
      this.device=device;this.timeout=timeout;this.openTimeout=openTimeout;this.signal=signal;this.transportKind='bluetooth';this.bleOtaCapable=false;this.connected=false;
      this.readable=null;this.writable=null;this.cleanup=null;this.opening=false;
    }
    setOpenSignal(signal){if(this.opening||this.connected)throw new Error('Bluetooth connection is already open.');this.signal=signal;}
    async open(){
      if(this.opening||this.cleanup)throw new Error('Bluetooth connection is already open.');
      const device=this.device,timeout=this.timeout,signal=this.signal;
      if(deviceOwners.has(device))throw new Error('This Bluetooth Companion is already in use.');
      deviceOwners.set(device,this);this.opening=true;
      let readController,writeController,tx,rx,terminated=false,expectedClose=false;
      let handshake=null,ack=null,incoming=null,rejectOpen,rejectDisconnected,negotiating=true;
      const aborted=new Promise((_,reject)=>{rejectOpen=reject;});aborted.catch(()=>{});
      const disconnected=new Promise((_,reject)=>{rejectDisconnected=reject;});disconnected.catch(()=>{});
      const fail=message=>{
        if(terminated)return;
        terminated=true;this.connected=false;this.bleOtaCapable=false;
        device.removeEventListener?.('gattserverdisconnected',onDisconnect);
        signal?.removeEventListener('abort',onAbort);
        tx?.removeEventListener?.('characteristicvaluechanged',onValue);
        const error=Error(message);rejectDisconnected(error);
        if(handshake){clearTimeout(handshake.timer);handshake.reject(error);handshake=null;}
        if(ack){clearTimeout(ack.timer);ack.reject(error);ack=null;}
        if(expectedClose){try{readController?.close();}catch(_){}}
        else {try{readController?.error(error);}catch(_){}try{writeController?.error(error);}catch(_){}}
        try{if(deviceOwners.get(device)===this && device.gatt.connected)device.gatt.disconnect();}catch(_){};
        if(deviceOwners.get(device)===this)deviceOwners.delete(device);
      };
      const onDisconnect=()=>fail('Bluetooth Companion disconnected.');
      const onAbort=()=>{rejectOpen(new DOMException('Connection canceled','AbortError'));fail('Bluetooth connection canceled.');};
      const receiveFrame=data=>{
        const len=data.length;
        if(!readController||readController.desiredSize<len+3){fail('Bluetooth Companion sent too much data.');return;}
        const framed=new Uint8Array(len+3);framed.set([62,len&255,len>>8]);framed.set(data,3);
        try{readController.enqueue(framed);}catch(_){fail('Bluetooth receive failed.');}
      };
      const onValue=event=>{
        if(terminated)return;
        const value=event.target?.value;
        if(!value||!value.byteLength||value.byteLength>MAX_FRAME+3){fail('Bluetooth Companion sent an invalid fragment.');return;}
        const data=new Uint8Array(value.buffer,value.byteOffset,value.byteLength);
        if(negotiating&&validPreHandshakePush(data)){receiveFrame(data);return;}
        if(negotiating&&!handshake){fail('Bluetooth Companion sent an invalid pre-handshake frame.');return;}
        if(handshake){
          const match=data.length===HELLO.length&&HELLO.every((b,i)=>data[i]===b);
          const pending=handshake;handshake=null;clearTimeout(pending.timer);
          if(match){negotiating=false;pending.resolve();}else pending.reject(Error('This Companion needs updated Bluetooth OTA firmware.'));
          return;
        }
        if(data[0]!==FRAGMENT||data.length<3){fail('Bluetooth Companion sent an invalid fragment.');return;}
        const total=data[1],offset=data[2];
        if(total===0){
          if(data.length!==3||!ack||offset!==ack.offset){fail('Bluetooth Companion sent an unexpected fragment acknowledgement.');return;}
          const pending=ack;ack=null;clearTimeout(pending.timer);pending.resolve();return;
        }
        const count=data.length-3;
        if(total>MAX_FRAME||!count||offset+count>total){fail('Bluetooth Companion sent an invalid fragment.');return;}
        if(offset===0){if(incoming){fail('Bluetooth Companion interrupted a frame.');return;}incoming={total,bytes:new Uint8Array(total),offset:0};}
        if(!incoming||incoming.total!==total||incoming.offset!==offset){fail('Bluetooth Companion sent fragments out of order.');return;}
        incoming.bytes.set(data.subarray(3),offset);incoming.offset+=count;
        if(incoming.offset===total){const complete=incoming.bytes;incoming=null;receiveFrame(complete);}
      };
      const close=()=>{expectedClose=true;fail('Bluetooth connection closed.');};
      const send=async bytes=>{
        if(terminated||!device.gatt.connected)throw new Error('Bluetooth Companion disconnected.');
        let operation;
        if(typeof rx.writeValueWithResponse==='function')operation=rx.writeValueWithResponse(bytes);
        else if(typeof rx.writeValue==='function')operation=rx.writeValue(bytes);
        else throw new Error('Bluetooth writes are unavailable.');
        let timer;
        try{await Promise.race([operation,disconnected,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Bluetooth write timed out.')),timeout);})]);}
        finally{clearTimeout(timer);}
      };
      const waitForAck=offset=>new Promise((resolve,reject)=>{
        const timer=setTimeout(()=>{if(ack){ack=null;reject(Error('Bluetooth fragment acknowledgement timed out.'));}},timeout);
        ack={offset,resolve,reject,timer};
      });
      this.cleanup=close;
      this.readable=new ReadableStream({start(c){readController=c;},cancel(){close();}}, {highWaterMark:MAX_BUFFER,size:b=>b.byteLength});
      this.writable=new WritableStream({
        start(c){writeController=c;},
        async write(chunk){
          if(!(chunk instanceof Uint8Array)||chunk.length<4||chunk[0]!==60)throw new Error('Invalid Bluetooth Companion frame.');
          const len=chunk[1]+256*chunk[2];
          if(!len||len>MAX_FRAME||len+3!==chunk.length)throw new Error('Invalid Bluetooth Companion frame.');
          try{
            for(let offset=0;offset<len;){
              const count=Math.min(FRAGMENT_DATA,len-offset),part=new Uint8Array(3+count);
              part.set([FRAGMENT,len,offset]);part.set(chunk.subarray(3+offset,3+offset+count),3);
              const acknowledged=waitForAck(offset+count);acknowledged.catch(()=>{});
              try{await send(part);}catch(error){if(ack){clearTimeout(ack.timer);ack=null;}acknowledged.catch(()=>{});throw error;}
              await acknowledged;offset+=count;
            }
          }catch(error){fail('Bluetooth write failed.');throw error;}
        },close(){close();},abort(){close();}
      });
      try{
        if(signal?.aborted)throw new DOMException('Connection canceled','AbortError');
        signal?.addEventListener('abort',onAbort,{once:true});
        device.addEventListener('gattserverdisconnected',onDisconnect);
        const connection=device.gatt.connect();
        connection.then(()=>{if(terminated&&!deviceOwners.has(device)&&device.gatt.connected)device.gatt.disconnect();},()=>{});
        const waitOpen=async promise=>{let timer;try{return await Promise.race([promise,aborted,disconnected,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Bluetooth connection timed out.')),this.openTimeout);})]);}finally{clearTimeout(timer);}};
        const server=await waitOpen(connection);
        if(terminated)throw new Error('Bluetooth connection closed.');
        const service=await waitOpen(server.getPrimaryService(SERVICE));
        rx=await waitOpen(service.getCharacteristic(RX));tx=await waitOpen(service.getCharacteristic(TX));
        tx.addEventListener('characteristicvaluechanged',onValue);
        await waitOpen(tx.startNotifications());
        if(terminated)throw new Error('Bluetooth connection closed.');
        const accepted=new Promise((resolve,reject)=>{
          const timer=setTimeout(()=>{if(handshake){handshake=null;reject(Error('This Companion needs updated Bluetooth OTA firmware.'));}},timeout);
          handshake={resolve,reject,timer};
        });
        accepted.catch(()=>{});
        try{await send(HELLO);}catch(error){if(handshake){clearTimeout(handshake.timer);handshake=null;}accepted.catch(()=>{});throw error;}
        await accepted;
        if(terminated)throw new Error('Bluetooth connection closed.');
        negotiating=false;this.bleOtaCapable=true;this.connected=true;signal?.removeEventListener('abort',onAbort);
      }catch(error){expectedClose=true;fail('Bluetooth connection failed.');this.cleanup=null;this.readable=this.writable=null;throw error;}
      finally{this.opening=false;}
    }
    async close(){this.cleanup?.();this.cleanup=null;this.connected=false;this.bleOtaCapable=false;this.readable=this.writable=null;}
  }
  return {create:(device,options)=>new BlePort(device,options),requestDevice,SERVICE,RX,TX};
});
