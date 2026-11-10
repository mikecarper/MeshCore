/* Exclusive USB Binary Companion transport. Zero-hop only. */
(function(root,factory){
  const node=typeof module==='object'&&module.exports;
  const api=factory(node?require('./lora_ota_port.js'):root.LoraOtaPort);if(node)module.exports=api;else root.LoraOtaCompanion=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(portHelper){
  'use strict';
  const enc=new TextEncoder(),dec=new TextDecoder(),sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const radioSetup=()=>typeof module==='object'&&module.exports?require('./lora_ota_radio_setup.js'):globalThis.LoraOtaRadioSetup;
  const hex=b=>Array.from(b,x=>x.toString(16).padStart(2,'0')).join('');
  function unhex(s){if(!/^[a-f0-9]{64}$/i.test(s))throw Error('Enter the complete target public key');return Uint8Array.from(s.match(/../g),x=>parseInt(x,16));}
  function deviceText(frame,offset,length){
    if(frame.length<offset+length)return null;
    const bytes=frame.subarray(offset,offset+length),end=bytes.indexOf(0);
    const value=dec.decode(end<0?bytes:bytes.subarray(0,end)).replace(/[\x00-\x1f\x7f]/g,'').trim();
    return value||null;
  }
  function join(...chunks){const out=new Uint8Array(chunks.reduce((n,c)=>n+c.length,0));let at=0;for(const c of chunks){out.set(c,at);at+=c.length;}return out;}
  function u32(n){const b=new Uint8Array(4);new DataView(b.buffer).setUint32(0,n,true);return b;}
  function packet(payload){if(!payload.length||payload.length>300)throw Error('Invalid Companion frame size');return join(Uint8Array.of(60,payload.length&255,payload.length>>8),payload);}
  const INBOX_KEY='meshcore-lora-ota-preserved-inbox-v1';
  const RADIO_PROBE_READS=new Set(['get public.key','get tempradio','get tempradio2','get radio2.status']);
  function loraTimeout(message){const error=Error(message);error.loraReplyTimeout=true;return error;}
  function radioProbeTimeout(radio,preamble){
    if(!Array.isArray(radio)||radio.length!==4)throw Error('Choose the radio settings for the link probe');
    const [frequency,bandwidth,sf,cr]=radio;
    radioSetup().commands({frequency,bandwidth,sf,cr,preamble,minutes:1});
    const symbol=2**sf/(bandwidth*1000),de=symbol>=.016?1:0;
    const airtime=(preamble+4.25+8+Math.max(0,Math.ceil((8*186-4*sf+44)/(4*(sf-2*de))))*cr)*symbol;
    return Math.max(1000,Math.ceil((2*airtime+.5)*1000));
  }
  const READ_ONLY=new Set(['get public.key','ota status','ota self','ver','get bootloader.ver','get usb.raw.max',
    'ota bootloader status','ota config','ota key list','get tempradio','get radio','get radio.rxps','ota installhash','ota qspi','ota stats','ota ls',
    'get radio2','get tempradio2','get radio2.cross','get radio2.status','get radioat','get tempradioat','get radioat2','get tempradioat2']);
  function usbOperation(payload){
    // Never include arbitrary command text, passwords, keys or packet bytes in
    // diagnostics. Only these fixed operation names and exact read-only verbs
    // are safe to copy from an error report.
    if(payload[0]===66){
      const command=dec.decode(payload.subarray(1));
      return READ_ONLY.has(command)?'reading local '+command:'running a local CLI command';
    }
    return ({1:'reading the Companion identity',2:'sending an admin command',
      5:'reading the Companion clock',10:'reading a queued message',22:'reading device information',
      26:'sending an admin login',52:'setting up the OTA session',65:'sending a raw OTA packet'})[payload[0]]||'waiting for a USB command reply';
  }
  // Legacy OTA commands are deliberately narrow; changing operations are
  // sent at most once and the target must still verify the staged image.
  function legacyOtaCommand(text){
    if(READ_ONLY.has(text)||/^ota pull [a-f0-9]{8} flash$/i.test(text)||
      /^ota installhash [a-f0-9]{64}$/i.test(text)||text==='ota install'||text==='ota cancel'||text==='set radio.rxps off'||
      /^ota config (?:(?:autofetch|autoinstall) off|hops 0)$/.test(text))return true;
    const temp=/^(?:tempradio |set tempradio2 )(\d+(?:\.\d+)?),(\d+(?:\.\d+)?),(\d+),(\d+),(?:rxtx,)?(\d+),(\d+)$/.exec(text);
    if(!temp)return false;
    try{
      const [frequency,bandwidth,sf,cr,minutes,preamble]=temp.slice(1).map(Number), profile=text.startsWith('set ')?1:0;
      const expected=radioSetup().commands({frequency,bandwidth,sf,cr,minutes,preamble},{targetProfile:profile}).target;
      return text===expected;
    }catch(_){return false;}
  }
  function sourceTemp(text){
    const s=String(text).trim().replace(/^>\s*/, '').replace(/^TempRadio active:\s*/i,'');
    const m=s.match(/^(\d+(?:\.\d+)?),(\d+(?:\.\d+)?),(\d+),(\d+)(?:,(\d+))?/);
    const p=/\bpreamble=(\d+)\b/i.exec(s);
    const preamble=Number(p?p[1]:m?.[5]);
    if(!m||!Number.isInteger(preamble)||preamble<1||preamble>65535)throw Error('Cannot confirm an active primary temporary radio and preamble');
    return {radio:m.slice(1,5).map(Number),preamble};
  }
  function inboxRecords(){
    const raw=localStorage.getItem(INBOX_KEY);if(raw===null)return [];
    const records=JSON.parse(raw);if(!Array.isArray(records)||records.length>256||records.some(r=>!r||typeof r!=='object'||!/^([a-f0-9]{64})$/i.test(r.sourceKey||'')||typeof r.at!=='string'||(r.pending?!/^[a-f0-9]{32}$/i.test(r.id||''):!/^([a-f0-9]{2}){1,300}$/i.test(r.frameHex||''))))throw Error('Saved inbox is invalid or full; export it before continuing');
    return records;
  }
  function decodedInbox(record){
    if(record.pending)return {at:record.at,sourceKey:record.sourceKey,kind:'interrupted',text:'A queued message may have been removed before it could be saved.'};
    if(!/^(?:[a-f0-9]{2}){1,300}$/i.test(record.frameHex||''))return {...record,kind:'unknown',text:'Unrecognized saved frame'};
    const f=Uint8Array.from(record.frameHex.match(/../g),x=>parseInt(x,16));
    const offset=(f[0]===16||f[0]===17)?4:1;
    if((f[0]===7||f[0]===16)&&f.length>=offset+12){
      return {...record,kind:'contact',sender:hex(f.slice(offset,offset+6)),timestamp:new DataView(f.buffer,f.byteOffset+offset+8,4).getUint32(0,true),text:dec.decode(f.slice(offset+12))};
    }
    if((f[0]===8||f[0]===17)&&f.length>=offset+7){
      return {...record,kind:'channel',channel:f[offset],timestamp:new DataView(f.buffer,f.byteOffset+offset+3,4).getUint32(0,true),text:dec.decode(f.slice(offset+7))};
    }
    return {...record,kind:'binary',text:'Binary or unrecognized message; raw frame retained.'};
  }
  function preservedMessages(){return inboxRecords().map(decodedInbox);}
  function preservedSnapshot(){const token=localStorage.getItem(INBOX_KEY)??'[]';return {token,messages:inboxRecords().map(decodedInbox)};}
  async function clearPreservedMessages(expectedToken,allowInterrupted=false){
    if(!globalThis.navigator?.locks?.request)throw Error('This browser cannot safely coordinate saved inbox messages');
    return navigator.locks.request(INBOX_KEY,{mode:'exclusive',ifAvailable:true},lock=>{
      if(!lock)throw Error('Saved inbox is in use by another tab; try again after it finishes');
      const rows=inboxRecords();if(typeof expectedToken!=='string'||(localStorage.getItem(INBOX_KEY)??'[]')!==expectedToken)throw Error('Saved messages changed since export; export them again before clearing');
      if(rows.some(r=>r.pending)&&!allowInterrupted)throw Error('Confirm the interrupted message warning before clearing');
      localStorage.removeItem(INBOX_KEY);return rows.length;
    });
  }
  class Decoder{
    constructor(){this.data=[];}
    feed(input){const frames=[];for(const b of input){
      if(!this.data.length&&b!==62)continue;
      this.data.push(b);
      if(this.data.length===3){const n=this.data[1]+256*this.data[2];if(!n||n>300){this.data=[];continue;}}
      if(this.data.length>=3&&this.data.length===3+this.data[1]+256*this.data[2]){frames.push(Uint8Array.from(this.data.slice(3)));this.data=[];}
    }return frames;}
  }
  class CompanionTransport{
    constructor(port,options={}){
      this.port=port;this.options=options;this.portOpened=false;this.knownRepeater=String(options.knownRepeater||'').toLowerCase();if(this.knownRepeater)unhex(this.knownRepeater);
      this.aclOnly=false;this.supportsRoutedPath=false;this.sourceKey=null;this.reader=null;this.writer=null;this.running=false;
      this.failure=null;this.decoder=new Decoder();this.pending=null;this.loginWaiter=null;this.queue=Promise.resolve();this.admin=Promise.resolve();
      this.authenticated=false;this.guard=false;this.investigationEnabled=false;this.investigationCancelled=false;this.investigationAuthenticated=false;this.investigationVerified=false;this.investigationProofPending=false;this.investigationPassword=null;this.investigationPermission='unknown';this.used=new Set();this.timestamp=0;this.serving=false;this.seeder=null;this.radioQueue=[];this.radioRunning=false;this.nextTx=0;this.txQueue=Promise.resolve();this.contactList=[];this.peerType=2;
      this.radioProbeAdmission=null;
      this.otaSupported=false;this.localCliSupported=false;this.otaMode='legacy';this.rawPacketSupport='unverified';
      this.usbRawFrameMax=176;this.radioProbeNextRequestAt=0;
      this.otaRadioProfile=0;this.otaTargetProfile=0;this.supportsOtaRadio2=false;
      this.sourceUpdateRequired=null;
      this.firmwareVersion=null;this.firmwareBuildDate=null;this.firmwareProtocol=null;this.firmwareVersionTruncated=false;
      this.otaUnavailableReason='This Companion needs firmware with guarded zero-hop CLI and radio support';
      // The browser serves packets itself; no firmware-side folder is used.
      this.browserRawSource=true;
    }
    async open(){
      try{
        await portHelper.openSerialPort(this.port,{signal:this.options.openSignal,onAttempt:this.options.onOpenAttempt,onSuccess:this.options.onOpenSuccess,onAcquired:()=>{this.portOpened=true;},onReleased:()=>{this.portOpened=false;},delayMs:this.options.openRetryDelayMs});
        this.portOpened=true;
        if(this.options.openSignal?.aborted)throw new DOMException('Connection canceled','AbortError');
        this.reader=this.port.readable.getReader();this.writer=this.port.writable.getWriter();this.running=true;
        this.readTask=this.readLoop();await sleep(this.options.openSettle??750);
        const info=await this.command(Uint8Array.of(1,3,32,32,32,32,32,32,109,99,99,108,105),5);
        if(info.length<58)throw Error('Invalid Companion identity response');this.sourceKey=hex(info.slice(4,36));
        const v=new DataView(info.buffer,info.byteOffset,info.byteLength);
        this.radio=[v.getUint32(48,true)/1000,v.getUint32(52,true)/1000,info[56],info[57]];
        // DEVICE_QUERY also declares the app protocol version; version 4
        // matches MeshCore Open and we decode both legacy and v3 inbox frames.
        try{
          const device=await this.commandAny(Uint8Array.of(22,4),[13],true);
          if(device[0]===13){
            if(device.length>=2)this.firmwareProtocol=device[1];
            this.firmwareBuildDate=deviceText(device,8,12);
            this.firmwareVersion=deviceText(device,60,20);
            this.firmwareVersionTruncated=!!this.firmwareVersion&&this.firmwareVersion.length>=19;
          }
        }catch(error){if(this.failure||error.message!=='Companion rejected the command')throw error;}
        // Contact browsing is available even on stock/older Companions. An
        // explicit unsupported response is harmless; an I/O timeout is not.
        try {
          // Older Companions use command 66 for a FEM read and may return OK,
          // not a CLI reply. Accept any response to this read-only probe.
          const frame=await this.commandAny(join(Uint8Array.of(66),enc.encode('get ota.direct')),[29],true);
          this.localCliSupported=frame[0]===29;
          const status=this.localCliSupported?dec.decode(frame.slice(1)).trim():'';
          if (/^>?\s*ota\.direct v=2 active=[01] busy=[01]\s*$/i.test(status)) {
            const cap=await this.localCommand('get ota.direct.capabilities');
            this.otaSupported=/^>?\s*ota\.direct\.cap guarded_cli=1 guarded_radio=1\s*$/i.test(cap);
            if(this.otaSupported&&!/^>?\s*ota\.direct v=2 active=0 busy=0\s*$/i.test(status))
              this.otaUnavailableReason='The Companion OTA mailbox is already in use';
            else if(this.otaSupported)this.otaUnavailableReason='';
          }
        }catch(error){if(this.failure||error.message!=='Companion rejected the command')throw error;}
        this.otaMode=this.otaSupported?'guarded':'legacy';
        if(this.localCliSupported){
          try{
            const reply=(await this.localCommand('ver')).replace(/^>\s*/,'').trim();
            const full=reply.match(/^(v?\d+(?:\.\d+){1,4}[^\s()]*)(?:\s|$)/i)?.[1];
            if(full){this.firmwareVersion=full;this.firmwareVersionTruncated=false;}
          }catch(error){if(this.failure)throw error;}
          const limit=String(await this.localCommand('get usb.raw.max')).trim().replace(/^>\s*/,'');
          if(/^\d+$/.test(limit)&&Number(limit)>=2&&Number(limit)<=300)this.usbRawFrameMax=Math.min(188,Number(limit));
          else if(!/^(?:(?:err(?:or)?\s*:?\s*)?(?:unknown|unsupported)\b)/i.test(limit))
            throw Error('Cannot confirm the Companion USB raw-packet limit');
        }
        return this;
      }catch(e){await this.close();throw e;}
    }
    async readLoop(){try{while(this.running){const {value,done}=await this.reader.read();if(done){if(this.running)throw Error('Companion disconnected');break;}for(const frame of this.decoder.feed(value))this.onFrame(frame);}}
      catch(e){if(this.running){this.failure=e;this.pending?.reject(e);this.loginWaiter?.reject(e);}}}
    onFrame(frame){
      if(frame[0]===0x88&&frame.length>5&&this.serving){if(this.radioQueue.length<64){this.radioQueue.push(frame.slice(3));this.pumpRadio();}return;}
      if((frame[0]===0x85||frame[0]===0x86)&&this.loginWaiter&&frame.length>=8&&hex(frame.slice(2,8))===this.knownRepeater.slice(0,12)){
        const waiter=this.loginWaiter;this.loginWaiter=null;
        if(frame[0]!==0x85)waiter.reject(Error('Repeater rejected the login'));
        else if(waiter.standard){this.investigationPermission=frame.length>=13?({0:'guest',1:'read-only',2:'read-write',3:'admin',4:'region manager',5:'filter manager'}[frame[12]&7]||'unknown'):'unknown';waiter.resolve();}
        else if(frame.length<13||!(frame[1]&1)||(frame[12]&7)!==3)waiter.reject(Error('Repeater rejected the admin login'));else waiter.resolve();return;
      }
      if(this.pending?.contacts){
        const p=this.pending;
        if(frame[0]===1){this.failContactList(p,'Companion could not list contacts');return;}
        if(frame[0]===2){
          if(frame.length!==5){this.failContactList(p,'Invalid contact list');return;}
          const count=new DataView(frame.buffer,frame.byteOffset,frame.byteLength).getUint32(1,true);
          if(count>4096){this.failContactList(p,'Contact list exceeds the supported size');return;}
          // A live table change restarts the firmware's snapshot. Discard the
          // partial snapshot, not the connection; retain the original deadline.
          p.started=true;p.rows=[];p.keys=new Set();return;
        }
        if(frame[0]===3){
          if(!p.started||frame.length!==148||p.rows.length>=4096){this.failContactList(p,'Invalid contact record');return;}
          const type=frame[33],flags=frame[34],path=frame[35];
          if(path!==255&&(path>>6)===3){this.failContactList(p,'Invalid contact path');return;}
          const nameBytes=frame.slice(100,132),end=nameBytes.indexOf(0),key=hex(frame.slice(1,33));
          if(p.keys.has(key)){this.failContactList(p,'Duplicate contact in snapshot');return;}p.keys.add(key);
          p.rows.push({key,publicKey:key,type,favorite:!!(flags&1),hops:path===255?null:path&63,name:dec.decode(end<0?nameBytes:nameBytes.slice(0,end))});return;
        }
        if(frame[0]===4){clearTimeout(p.timer);this.pending=null;if(!p.started||frame.length!==5){this.failContactList(p,'Incomplete contact list');return;}
          p.rows.sort((a,b)=>Number(b.favorite)-Number(a.favorite)||a.type-b.type||(a.hops??Infinity)-(b.hops??Infinity)||a.name.localeCompare(b.name)||a.publicKey.localeCompare(b.publicKey));
          this.contactList=p.rows;p.resolve(p.rows.slice());return;}
      }
      if(this.pending&&(frame[0]===1||frame[0]===this.pending.code||this.pending.codes?.includes(frame[0])||(this.pending.anyResponse&&frame[0]<128))){
        const pending=this.pending;this.pending=null;clearTimeout(pending.timer);
        if(frame[0]===1){const error=Error('Companion rejected the command');if(frame.length>=2)error.companionErrorCode=frame[1];pending.reject(error);}else pending.resolve(frame);
      }
    }
    failContactList(p,message){
      clearTimeout(p.timer);this.pending=null;
      // The remaining stream has no request ID. Never let those records be
      // interpreted as the reply to a following inbox read or CLI command.
      this.failure=Error(message+'; reconnect the USB source');p.reject(this.failure);
    }
    command(payload,code){return this.commandAny(payload,[code]);}
    commandAny(payload,codes,anyResponse=false,timeoutMs=this.options.commandTimeout??15000){
      const operation=usbOperation(payload);
      const run=this.queue.then(()=>new Promise((resolve,reject)=>{
        if(!this.running||this.failure)return reject(this.failure||Error('Companion is disconnected'));
        const timer=setTimeout(()=>{this.pending=null;this.failure=Error('Companion response timed out while '+operation+'; reconnect before retrying');reject(this.failure);},timeoutMs);
        this.pending={codes,anyResponse,resolve,reject,timer};this.writer.write(packet(payload)).catch(e=>{clearTimeout(timer);this.pending=null;this.failure=e;reject(e);});
      }));this.queue=run.catch(()=>{});return run;
    }
    contacts(){
      const run=this.queue.then(()=>new Promise((resolve,reject)=>{
        if(!this.running||this.failure)return reject(this.failure||Error('Companion is disconnected'));
        const timer=setTimeout(()=>{this.pending=null;this.failure=Error('Contact list timed out; reconnect before retrying');reject(this.failure);},this.options.contactsTimeout??60000);
        this.pending={contacts:true,started:false,rows:[],resolve,reject,timer};
        this.writer.write(packet(Uint8Array.of(4))).catch(e=>{clearTimeout(timer);this.pending=null;this.failure=e;reject(e);});
      }));this.queue=run.catch(()=>{});return run;
    }
    selectTarget(key){
      key=String(key).toLowerCase();unhex(key);
      if(this.guard||this.authenticated||this.serving)throw Error('Close the active OTA session before changing target');
      if(this.investigationEnabled||this.investigationAuthenticated)throw Error('Disconnect before changing the investigation target');
      const match=this.contactList.find(c=>c.publicKey===key);
      this.knownRepeater=key;this.peerType=match?.type||2;this.radioProbeAdmission=null;
    }
    async localCommand(text){
      if(!this.localCliSupported)throw Error('This Companion cannot report local CLI settings over USB');
      if(typeof text!=='string'||!text.length||/[\r\n\0]/.test(text)||enc.encode(text).length>180)throw Error('Invalid Companion command');
      const reply=dec.decode((await this.command(join(Uint8Array.of(66),enc.encode(text)),29)).slice(1)).trim();
      if(text==='get radio'){
        const m=reply.match(/^>?\s*(\d+(?:\.\d+)?),(\d+(?:\.\d+)?),(\d+),(\d+)\s*$/);
        if(!m)throw Error('Cannot read current Companion radio settings');
        this.radio=m.slice(1).map(Number);return reply;
      }
      // Companion reports these exact messages only when its radio driver has
      // no RXPS implementation. Unknown settings and other errors stay errors.
      if(text==='get radio.rxps'&&/^Error: RX power saving (?:is unsupported on this radio|unsupported on radio)$/.test(reply))return '> off,unsupported';
      return reply;
    }
    async savedSourceRadio(){
      // CMD_APP_START cancels current serial operations on newer firmware.
      // For legacy units without a local CLI, use only the saved tuple already
      // obtained when opening the connection; it is not a live radio read.
      if(!this.radio||this.radio.length!==4||this.radio.some(v=>!Number.isFinite(v)||v<=0))throw Error('Cannot read the Companion saved radio settings');
      return this.radio.slice();
    }
    withAdmin(fn){const p=this.admin.then(fn);this.admin=p.catch(()=>{});return p;}
    async guardMode(mode,extra=new Uint8Array()){
      await this.command(join(Uint8Array.of(52,mode),unhex(this.knownRepeater),extra),0);
    }
    async checkClock(){
      const read=async()=>{const f=await this.command(Uint8Array.of(5),9);if(f.length!==5)throw Error('Cannot verify Companion clock');return new DataView(f.buffer,f.byteOffset,f.byteLength).getUint32(1,true);};
      const current=await read(),now=Math.floor(Date.now()/1000);
      if(current>now+600||current+59<now)throw Error('Companion clock is out of sync. Correct its clock before retrying.');
      this.timestamp=Math.max(this.timestamp,current);
    }
    assertStandardTarget(){
      if(!this.knownRepeater||!this.contactList.some(c=>c.publicKey===this.knownRepeater))throw Error('Select a stored contact before continuing');
      if(this.contactList.some(c=>c.publicKey!==this.knownRepeater&&c.publicKey.slice(0,12)===this.knownRepeater.slice(0,12)))throw Error('Contacts share this key prefix; cannot safely select the target');
    }
    investigationProgress(stage,command='',timeoutMs=0,message=''){
      const attempt=this.investigationAttempt||0;
      try{this.options.onInvestigationProgress?.({stage,command,timeoutMs,message,attempt,maxAttempts:attempt?this.investigationMaxAttempts:0});}catch(_){}
    }
    investigationReplyTimeout(){
      if(this.options.investigationTimeout!=null)return this.options.investigationTimeout;
      const contact=this.contactList.find(contact=>contact.publicKey===this.knownRepeater);
      return contact?.hops===0?7000:20000;
    }
    async inspectInvestigationClock(){
      this.investigationProgress('checking-clock');
      try{
        const frame=await this.command(Uint8Array.of(5),9);
        if(frame.length!==5)throw Error('Invalid clock response');
        const clock=new DataView(frame.buffer,frame.byteOffset,frame.byteLength).getUint32(1,true);
        const delta=clock-Math.floor(Date.now()/1000);
        if(delta>600||delta < -59){
          const difference=Math.abs(delta);
          this.investigationProgress('clock-warning','',0,'The Companion clock differs from this computer by about '+difference+' seconds ('+(delta<0?'behind':'ahead')+'). A wrong clock can cause the target to ignore commands. Check both clocks.');
        }
      }catch(error){
        if(this.failure)throw error;
        this.investigationProgress('clock-warning','',0,'Could not verify the Companion clock. A wrong clock can cause the target to ignore commands.');
      }
    }
    assertInvestigationRunning(){
      if(this.investigationCancelled)throw Error('Investigation canceled. Disconnect and reconnect before trying again.');
    }
    async retryInvestigation(text,run){
      if(!READ_ONLY.has(text))throw Error('Only approved read-only investigation commands are allowed');
      // Until the identity check receives a response, keep probing bounded.
      // Thereafter the target has replied, so tolerate a weaker RF link.
      const maxAttempts=this.investigationAuthenticated?11:7;
      this.investigationMaxAttempts=maxAttempts;
      try{
        for(let attempt=1;attempt<=maxAttempts;attempt++){
          this.assertInvestigationRunning();this.investigationAttempt=attempt;
          try{const reply=await run();this.assertInvestigationRunning();return reply;}
          catch(error){
            this.assertInvestigationRunning();
            if(!error?.loraReplyTimeout||this.failure)throw error;
            if(attempt===maxAttempts){error.attempts=attempt;throw error;}
            this.investigationProgress('retrying-command',text);
          }
        }
      }finally{this.investigationAttempt=0;this.investigationMaxAttempts=0;}
    }
    cancelInvestigation(){
      if(!this.investigationEnabled)return;
      this.investigationCancelled=true;
      this.failure=Error('Investigation canceled. Reconnect the USB source before continuing.');
      // Let an in-flight inbox read finish and save its removed message.
      // Other work must wait for a fresh connection and identity check.
      if(this.pending?.contacts){
        const pending=this.pending;this.pending=null;clearTimeout(pending.timer);
        pending.reject(this.failure);
      }
      if(this.loginWaiter){const waiter=this.loginWaiter;this.loginWaiter=null;waiter.reject(Error('Investigation canceled. Disconnect and reconnect before trying again.'));}
    }
    enableStandardInvestigation(){
      if(this.otaSupported)throw Error('Standard inbox mode is only for older Companion firmware');
      this.assertInvestigationRunning();
      this.assertStandardTarget();
      // Every dequeue reserves storage under an origin-wide lock before it runs.
      if(!globalThis.navigator?.locks?.request)throw Error('This browser cannot safely coordinate saved inbox messages');
      inboxRecords();
      this.investigationEnabled=true;
    }
    getPreservedMessages(){return preservedMessages().filter(r=>r.sourceKey===this.sourceKey);}
    reserveInbox(){
      const rows=inboxRecords();if(rows.length>=256)throw Error('Saved inbox is full; export messages before continuing');
      const id=hex(crypto.getRandomValues(new Uint8Array(16)));
      rows.push({at:new Date().toISOString(),sourceKey:this.sourceKey,pending:true,id,reserve:'x'.repeat(2048)});
      try{localStorage.setItem(INBOX_KEY,JSON.stringify(rows));}catch(_){throw Error('Cannot reserve browser storage for queued messages');}
      return id;
    }
    finishInbox(id,frame,preserve){
      const rows=inboxRecords(),index=rows.findIndex(r=>r.pending&&r.id===id&&r.sourceKey===this.sourceKey);
      if(index<0)throw Error('Saved inbox reservation changed; stopping to protect messages');
      if(preserve)rows[index]={at:rows[index].at,sourceKey:this.sourceKey,frameHex:hex(frame)};else rows.splice(index,1);
      try{localStorage.setItem(INBOX_KEY,JSON.stringify(rows));}catch(_){throw Error('Could not save a removed inbox message; stop and export saved messages');}
      if(preserve){try{this.options.onPreserved?.(decodedInbox(rows[index]),rows.filter(r=>r.sourceKey===this.sourceKey&&!r.pending).length);}catch(_){}}
    }
    async standardReply(text,otaControl=false){
      if(!this.investigationEnabled||!(otaControl?legacyOtaCommand(text):READ_ONLY.has(text)))throw Error('Only approved OTA commands are allowed');
      if(!this.investigationAuthenticated&&!(text==='get public.key'&&this.investigationProofPending))throw Error('Verify the target before running investigation commands');
      if(otaControl&&!this.investigationVerified)throw Error('Verify the target full public key before updating');
      this.assertInvestigationRunning();
      this.investigationProgress('refreshing-contacts',text,this.options.contactsTimeout??60000);
      await this.contacts();this.assertInvestigationRunning();this.assertStandardTarget();
      if(this.used.size>=7921)throw Error('Reconnect before sending more commands');
      let correlation;do{const r=crypto.getRandomValues(new Uint8Array(2));correlation=String.fromCharCode(33+r[0]%90,33+r[1]%90);}
      while(correlation.includes('|')||this.used.has(correlation));this.used.add(correlation);
      const tag=correlation+'|';this.timestamp=Math.max(Math.floor(Date.now()/1000),this.timestamp+1);
      this.investigationProgress('sending-command',text,this.options.commandTimeout??15000);
      await this.command(join(Uint8Array.of(2,1,0),u32(this.timestamp),unhex(this.knownRepeater).slice(0,6),enc.encode(tag+text)),6);
      this.investigationProgress('command-sent',text);
      this.assertInvestigationRunning();
      const replyTimeout=READ_ONLY.has(text)?this.investigationReplyTimeout():(this.options.investigationTimeout??20000);
      const deadline=Date.now()+replyTimeout;
      this.investigationProgress('waiting-reply',text,replyTimeout);
      while(Date.now()<deadline){
        this.assertInvestigationRunning();
        const result=await navigator.locks.request(INBOX_KEY,{mode:'exclusive',ifAvailable:true},async lock=>{
          if(!lock)throw Error('Saved inbox is in use by another tab; try again after it finishes');
          // Pace destructive inbox reads on older/native-USB Companions. A
          // slow response stays attached to the same reservation and request;
          // never dequeue a second message to retry a missing first reply.
          await sleep(this.options.inboxPollDelay??20);
          this.assertInvestigationRunning();
          const reservation=this.reserveInbox();let frame;
          const waiting=setTimeout(()=>this.investigationProgress('waiting-inbox',text,this.options.inboxTimeout??30000),this.options.commandTimeout??15000);
          try{frame=await this.commandAny(Uint8Array.of(10),[],true,this.options.inboxTimeout??30000);}
          catch(e){throw Error((e?.message||e)+'; a queued message may have been removed before it could be saved');}
          finally{clearTimeout(waiting);}
          if(frame[0]===10){this.finishInbox(reservation,frame,false);return {idle:true};}
          const offset=frame[0]===16?4:frame[0]===7?1:-1;
          if(offset>=0&&frame.length>=offset+12&&hex(frame.slice(offset,offset+6))===this.knownRepeater.slice(0,12)&&frame[offset+7]===1){
            const reply=dec.decode(frame.slice(offset+12));
            if(reply.startsWith(tag)){this.finishInbox(reservation,frame,false);return {reply:reply.slice(tag.length)};}
          }
          this.finishInbox(reservation,frame,true);return {};
        });
        this.assertInvestigationRunning();
        if(result.reply!==undefined){this.investigationProgress('command-result',text);return result.reply;}
        if(result.idle)await sleep(500);
      }
      this.investigationProgress('command-timeout',text);
      const error=Error('Remote command timed out; result is unknown');error.loraReplyTimeout=true;throw error;
    }
    async investigateVerifyPeer(key,password=''){
      this.validate(key);return this.withAdmin(async()=>{
        if(this.otaSupported){
          this.investigationEnabled=true;this.assertInvestigationRunning();
          if(this.investigationAuthenticated&&password!==this.investigationPassword)throw Error('Disconnect and reconnect before changing the admin password');
          if(!this.authenticated)await this.login(password,true);
          this.investigationPassword=password;this.investigationAuthenticated=true;this.investigationVerified=true;
          return this.knownRepeater;
        }
        if(!this.investigationEnabled)throw Error('Enable standard investigation first');
        this.assertInvestigationRunning();
        if(this.investigationAuthenticated){if(password!==this.investigationPassword)throw Error('Disconnect and reconnect before changing the admin password');return this.knownRepeater;}
        if(typeof password!=='string'||enc.encode(password).length>15||/[\r\n\0]/.test(password))throw Error('Admin password must fit in 15 bytes');
        await this.inspectInvestigationClock();this.assertInvestigationRunning();
        this.investigationProgress('refreshing-contacts',password ? 'login' : 'ACL',this.options.contactsTimeout??60000);
        await this.contacts();this.assertInvestigationRunning();this.assertStandardTarget();
        const loginTimeout=this.investigationReplyTimeout();
        const loginLabel=password?'login':'passwordless login';
        const response=new Promise((resolve,reject)=>{const timer=setTimeout(()=>{this.loginWaiter=null;reject(loraTimeout('Repeater login timed out'));},loginTimeout);this.loginWaiter={standard:true,resolve:()=>{clearTimeout(timer);resolve();},reject:e=>{clearTimeout(timer);reject(e);}};});
        response.catch(()=>{}); // Cancellation can precede the Companion's send acknowledgement.
        this.investigationProgress('sending-login',loginLabel,this.options.commandTimeout??15000);
        try{await this.command(join(Uint8Array.of(26),unhex(this.knownRepeater),enc.encode(password)),6);this.assertInvestigationRunning();this.investigationProgress('waiting-login',loginLabel,loginTimeout);await response;this.assertInvestigationRunning();}
        catch(e){
          this.loginWaiter?.reject(e);await response.catch(()=>{});this.assertInvestigationRunning();
          if(password||this.failure||!['Repeater login timed out','Repeater rejected the login'].includes(e?.message))throw e;
          // Blank login can refresh the reply path, but direct ACL access can
          // still work when that login is unsupported or its reply was lost.
          this.investigationPermission='unknown';
          this.investigationProgress('login-fallback','',0,'No confirmed passwordless login; trying existing ACL access.');
        }
        this.investigationProgress('identity','get public.key',this.investigationReplyTimeout());
        this.investigationProofPending=true;let proof;
        try{proof=await this.retryInvestigation('get public.key',()=>this.standardReply('get public.key'));}finally{this.investigationProofPending=false;}
        const normalized=proof.replace(/^>\s*/,'').trim();
        const unavailable=/^(?:unknown command\b|unsupported\b|error:\s*(?:unknown (?:setting|command)|unsupported)\b|err(?:or)?\s*[:-]?\s*(?:unknown|unsupported)\b)/i.test(normalized)
          || /^(?:(?:err|error)\s*:?\s*)?(?:permission denied|access denied|not authori[sz]ed)\.?$/i.test(normalized);
        if(normalized.toLowerCase()!==this.knownRepeater&&!unavailable)throw Error('Target did not prove its full public key');
        this.investigationVerified=!unavailable;
        this.investigationPassword=password;this.investigationAuthenticated=true;return this.knownRepeater;
      });
    }
    async investigateCommand(key,password,text){this.validate(key);if(!this.investigationAuthenticated)throw Error('Verify the target before running investigation commands');if(password!==this.investigationPassword)throw Error('Disconnect and reconnect before changing the admin password');return this.withAdmin(()=>this.retryInvestigation(text,()=>this.otaSupported?this.remoteReply(text):this.standardReply(text)));}
    async login(password,investigation=false){
      if(typeof password!=='string'||enc.encode(password).length>15||/[\r\n\0]/.test(password))throw Error('Admin password must fit in 15 bytes');
      if(!this.knownRepeater)throw Error('Select a target first');
      if(!this.otaSupported)throw Error(this.otaUnavailableReason);
      const status=await this.localCommand('get ota.direct');
      if(!/^>?\s*ota\.direct v=2 active=0 busy=0\s*$/i.test(status))throw Error('The Companion OTA mailbox is already in use');
      await this.checkClock();await this.guardMode(1,Uint8Array.of(this.peerType));this.guard=true;
      if(password||investigation){
        const loginTimeout=investigation?this.investigationReplyTimeout():(this.options.remoteTimeout??75000);
        const loginLabel=password?'login':'passwordless login';
        const response=new Promise((resolve,reject)=>{const timer=setTimeout(()=>{this.loginWaiter=null;reject(loraTimeout('Repeater login timed out'));},loginTimeout);this.loginWaiter={resolve:()=>{clearTimeout(timer);resolve();},reject:e=>{clearTimeout(timer);reject(e);}};});
        response.catch(()=>{});
        this.investigationProgress('sending-login',loginLabel,this.options.commandTimeout??15000);
        try{await this.command(join(Uint8Array.of(26),unhex(this.knownRepeater),enc.encode(password)),6);if(investigation)this.assertInvestigationRunning();this.investigationProgress('waiting-login',loginLabel,loginTimeout);await response;if(investigation)this.assertInvestigationRunning();}
        catch(e){
          this.loginWaiter?.reject(e);await response.catch(()=>{});if(investigation)this.assertInvestigationRunning();
          if(!investigation||password||this.failure||!['Repeater login timed out','Repeater rejected the login','Repeater rejected the admin login'].includes(e?.message))throw e;
          this.investigationProgress('login-fallback','',0,'No confirmed passwordless login; trying existing ACL access.');
        }
      }else{
        // The target may already grant this Companion access in its ACL.
        // Arm a direct return path and require the same full-key proof below.
        this.investigationProgress('checking-acl','ACL',this.options.remoteTimeout??75000);
      }
      if(investigation)this.assertInvestigationRunning();await this.guardMode(2);
      const deadline=Date.now()+(this.options.remoteTimeout??75000);while(Date.now()<deadline){
        if(investigation)this.assertInvestigationRunning();
        const status=await this.localCommand('get ota.direct');if(/^>?\s*ota\.direct v=2 active=1 busy=0\s*$/i.test(status))break;
        if(!/^>?\s*ota\.direct v=2 active=1 busy=1\s*$/i.test(status))throw Error('Companion direct session changed');await sleep(100);
      }
      if(Date.now()>=deadline)throw loraTimeout('Return path timed out');
      const proof=investigation?await this.retryInvestigation('get public.key',()=>this.remoteReply('get public.key')):await this.remoteReply('get public.key');
      if(proof.replace(/^>\s*/,'').trim().toLowerCase()!==this.knownRepeater)throw Error('Repeater did not prove its full public key');
      this.authenticated=true;
    }
    async remoteReply(text){
      if(!text||/[\r\n\0]/.test(text)||enc.encode(text).length>152)throw Error('Invalid remote command');
      if(this.investigationAttempt)this.assertInvestigationRunning();
      if(this.used.size>=7921)throw Error('Reconnect before sending more commands');
      let correlation;do{const r=crypto.getRandomValues(new Uint8Array(2));correlation=String.fromCharCode(33+r[0]%90,33+r[1]%90);}
      while(correlation.includes('|')||this.used.has(correlation));this.used.add(correlation);
      const tag=correlation+'|';await this.guardMode(4,enc.encode(tag));
      if(this.investigationAttempt)this.assertInvestigationRunning();
      this.timestamp=Math.max(Math.floor(Date.now()/1000),this.timestamp+1);
      if(READ_ONLY.has(text))this.investigationProgress('sending-command',text,this.options.commandTimeout??15000);
      await this.command(join(Uint8Array.of(2,1,0),u32(this.timestamp),unhex(this.knownRepeater).slice(0,6),enc.encode(tag+text)),6);
      const replyTimeout=this.investigationAttempt?this.investigationReplyTimeout():(this.options.remoteTimeout??75000);
      if(READ_ONLY.has(text)){
        this.investigationProgress('command-sent',text);
        this.investigationProgress('waiting-reply',text,replyTimeout);
      }
      const deadline=Date.now()+replyTimeout;let result=[],total=null;
      while(Date.now()<deadline){
        if(this.investigationAttempt)this.assertInvestigationRunning();
        const reply=await this.localCommand('get ota.direct.reply '+result.length);
        if(this.investigationAttempt)this.assertInvestigationRunning();
        const m=reply.match(/^>?\s*ota\.reply req=([!-~]{2}) state=(idle|pending|ready) bytes=(\d+) off=(\d+) data=([a-f0-9]+|-)\s*$/i);
        if(!m||m[1]!==correlation||+m[3]>160||+m[4]!==result.length||m[2]==='idle')throw Error('Companion command mailbox changed');
        if(m[2]==='pending'){if(result.length||+m[3]||m[5]!=='-')throw Error('Invalid pending command mailbox');await sleep(100);continue;}
        const n=+m[3],data=m[5]==='-'?[]:(m[5].match(/../g)||[]).map(x=>parseInt(x,16));
        if((m[5]!=='-'&&m[5].length%2)||data.length>40||result.length+data.length>n||(!data.length&&result.length!==n)||(total!==null&&n!==total))throw Error('Invalid Companion reply page');
        total=n;result.push(...data);if(result.length===total){if(READ_ONLY.has(text))this.investigationProgress('command-result',text);return dec.decode(Uint8Array.from(result));}
      }if(READ_ONLY.has(text))this.investigationProgress('command-timeout',text);const error=Error('Remote command timed out; result is unknown');error.loraReplyTimeout=true;throw error;
    }
    validate(key){if(String(key).toLowerCase()!==this.knownRepeater)throw Error('Target differs from the selected repeater');}
    async verifyPeer(key,password=''){
      this.validate(key);
      if(!this.otaSupported){
        if(!this.investigationEnabled)throw Error('Confirm the older Companion inbox warning and investigate the target first');
        await this.investigateVerifyPeer(key,password);
        if(!this.investigationVerified)throw Error('Verify the target full public key before updating');
        return this.knownRepeater;
      }
      return this.withAdmin(async()=>{if(!this.authenticated)await this.login(password);this.radioProbeAdmission={key:this.knownRepeater,password};return this.knownRepeater;});
    }
    async probePeerRadio(key,password,profile,{radio,preamble,readback='get tempradio',maxDurationMs}={}){
      this.validate(key);
      if(!this.otaSupported||!this.supportsOtaRadio2||this.serving||![0,1].includes(profile))
        throw Error('Radio link probes need an idle guarded Companion with selectable OTA radios');
      if(typeof readback!=='function'&&!RADIO_PROBE_READS.has(readback))throw Error('Only temporary-radio readbacks are allowed in a link probe');
      const timeout=radioProbeTimeout(radio,preamble);
      if(maxDurationMs!==undefined&&(!Number.isFinite(maxDurationMs)||maxDurationMs<0))throw Error('Invalid radio probe duration');
      return this.withAdmin(async()=>{
        const admission=this.radioProbeAdmission;
        if(!admission||admission.key!==this.knownRepeater||admission.password!==password)
          throw Error('Verify this target and password before probing another radio channel');
        if(this.serving)throw Error('Stop the transfer before probing another radio channel');
        const options=this.options,deadline=Date.now()+(maxDurationMs??timeout*5);
        this.options={...options,remoteTimeout:timeout};
        const remaining=()=>{
          const left=deadline-Date.now();
          if(left<=0)throw loraTimeout('Radio link probe timed out');
          return left;
        };
        const closeGuard=async()=>{
          if(this.guard&&!this.failure)await this.guardMode(3);
          this.guard=false;this.authenticated=false;
        };
        const waitIdle=async active=>{
          const until=Math.min(deadline,Date.now()+timeout);
          while(Date.now()<until){
            const status=String(await this.localCommand('get ota.direct')).trim().replace(/^>\s*/,'');
            const match=/^ota\.direct v=2 active=([01]) busy=([01])$/.exec(status);
            if(!match||Number(match[1])!==active)throw Error('Companion direct session changed during the radio probe');
            if(match[2]==='0')return;
            await sleep(Math.min(100,Math.max(0,until-Date.now())));
          }
          throw loraTimeout('Return path timed out during the radio probe');
        };
        const read=async command=>{
          if(!RADIO_PROBE_READS.has(command))throw Error('Only temporary-radio readbacks are allowed in a link probe');
          // Request timestamps are replay counters measured in whole seconds.
          // Do not make rapid channel probes push that counter into the future.
          const delay=this.radioProbeNextRequestAt-Date.now();
          if(delay>0){if(delay>=remaining())throw loraTimeout('Radio link probe timed out');await sleep(delay);}
          this.radioProbeNextRequestAt=Date.now()+1000;
          this.options.remoteTimeout=Math.min(timeout,remaining());
          const reply=await this.remoteReply(command);
          const text=String(reply).trim().replace(/^>\s*/,'');
          if(/^(?:err(?:or)?\b|unknown\b|unsupported\b)/i.test(text))throw Error('Target rejected the radio probe: '+text);
          return reply;
        };
        try{
          await closeGuard();remaining();await waitIdle(0);
          if(remaining()<timeout)throw loraTimeout('Not enough time remains for a radio link probe');
          await this.selectOtaRadio(profile);
          await this.guardMode(1,Uint8Array.of(this.peerType));this.guard=true;
          await this.guardMode(2);await waitIdle(1);
          // The existing admin session was admitted on the old channel. Each
          // candidate channel still needs a fresh full-key proof, but no new
          // password login, contact dump or eleven-attempt investigation.
          const proof=String(await read('get public.key')).trim().replace(/^>\s*/,'');
          if(proof.toLowerCase()!==this.knownRepeater)throw Error('Repeater did not prove its full public key');
          const reply=typeof readback==='function'?await readback(read):await read(readback);
          this.authenticated=true;
          return {reachable:true,reply};
        }catch(error){
          await closeGuard();
          if(error?.loraReplyTimeout&&!this.failure)return {reachable:false};
          throw error;
        }finally{this.options=options;}
      });
    }
    async requestRemoteRadio(key,password,text,{radio,preamble,maxDurationMs}={}){
      this.validate(key);
      if(!this.otaSupported||!this.supportsOtaRadio2||this.serving||
          !/^(?:tempradio |set tempradio2 )/.test(text)||!legacyOtaCommand(text))
        throw Error('Only a guarded temporary-radio request is allowed');
      const timeout=Math.max(2500,radioProbeTimeout(radio,preamble));
      if(maxDurationMs!==undefined&&(!Number.isFinite(maxDurationMs)||maxDurationMs<0))throw Error('Invalid radio request duration');
      return this.withAdmin(async()=>{
        const admission=this.radioProbeAdmission;
        if(!admission||admission.key!==this.knownRepeater||admission.password!==password||
            !this.guard||!this.authenticated||this.otaRadioProfile!==0||this.serving)
          throw Error('Prove the target on the primary radio before requesting temporary settings');
        if(maxDurationMs!==undefined&&maxDurationMs<timeout)throw loraTimeout('Not enough time remains for a temporary-radio request');
        const options=this.options;
        this.options={...options,remoteTimeout:Math.min(timeout,maxDurationMs??timeout)};
        try{
          // One setter only. A lost RF acknowledgement is resolved by probing
          // both channels; it never authorizes a replay or a new login here.
          const reply=await this.remoteReply(text),result=String(reply).trim().replace(/^>\s*/,'');
          if(/^(?:err(?:or)?\b|unknown\b|unsupported\b)/i.test(result))throw Error('Target rejected temporary radio settings: '+result);
          return reply;
        }finally{this.options=options;}
      });
    }
    async remoteCommand(key,password,text){
      this.validate(key);
      if(!this.otaSupported){
        if(!this.investigationAuthenticated||!this.investigationVerified)throw Error('Verify the target full public key before updating');
        if(password!==this.investigationPassword)throw Error('Disconnect and reconnect before changing the admin password');
        return this.withAdmin(()=>READ_ONLY.has(text)
          ?this.retryInvestigation(text,()=>this.standardReply(text,true))
          :this.standardReply(text,true));
      }
      return this.withAdmin(async()=>{if(!this.authenticated)await this.login(password);return this.remoteReply(text);});
    }
    async resetRemoteSession(key,password=''){
      this.validate(key);
      if(!this.otaSupported){this.investigationAuthenticated=false;this.investigationVerified=false;return this.verifyPeer(key,password);}
      return this.withAdmin(async()=>{if(this.guard)await this.guardMode(3);this.guard=false;this.authenticated=false;});
    }
    async closeRadioSession(){
      if(this.serving)throw Error('Stop the transfer before changing the OTA channel');
      return this.withAdmin(async()=>{
        if(this.guard)await this.guardMode(3);
        this.guard=false;this.authenticated=false;
      });
    }
    async probeOtaRadio(){
      if(!this.otaSupported)return false;
      const reply=(await this.localCommand('get ota.direct.radio')).replace(/^>\s*/,'');
      const match=/^ota\.direct\.radio v=1 profile=(radio|radio2)$/.exec(reply);
      if(match){this.otaRadioProfile=match[1]==='radio2'?1:0;this.supportsOtaRadio2=true;return true;}
      if(/^(?:unknown\b|unsupported\b|err(?:or)?\s*:?\s*(?:unknown|unsupported)\b)/i.test(reply)){this.supportsOtaRadio2=false;this.otaRadioProfile=0;return false;}
      throw Error('Cannot confirm the Companion OTA radio selection');
    }
    async selectOtaRadio(profile){
      if(profile!==0&&profile!==1)throw Error('Invalid OTA radio selection');
      if(!this.supportsOtaRadio2){if(profile===0){this.otaRadioProfile=0;return;}throw Error('Update the USB Companion to use temporary radio2 for OTA');}
      if(this.serving||this.guard||this.authenticated)throw Error('Close the OTA session before selecting its radio');
      const name=profile?'radio2':'radio';
      const reply=await this.localCommand('set ota.direct.radio '+name);
      if(reply!=='OK - ota.direct.radio '+name)throw Error('Companion did not confirm OTA '+name+' selection');
      if((await this.localCommand('get ota.direct.radio')).replace(/^>\s*/,'')!=='ota.direct.radio v=1 profile='+name)
        throw Error('Companion OTA radio selection could not be verified');
      this.otaRadioProfile=profile;
    }
    async applyTemporaryRadio(command){
      if(!this.localCliSupported||this.serving||!/^tempradio [\d.,]+$|^set tempradio2 [\d.,]+,rxtx,[\d.,]+$/.test(command)||
          (!this.otaSupported&&!command.startsWith('tempradio ')))
        throw Error('Cannot prepare this Companion temporary radio automatically');
      return this.withAdmin(async()=>{
        // Compatibility sources have a verified USB CLI but no guard API.
        // Their primary channel is checked against the saved tuple by setup
        // and again before every raw-packet transmission.
        if(!this.otaSupported)return this.localCommand(command);
        // Arming locally sends no RF. It prevents shared-inbox eviction while
        // the firmware reserves its OTA context for the temporary window.
        if(!this.guard){await this.guardMode(1,Uint8Array.of(this.peerType));this.guard=true;}
        try{return await this.localCommand(command);}
        finally{await this.guardMode(3);this.guard=false;this.authenticated=false;}
      });
    }
    async setPath(){throw Error('Companion OTA currently supports zero-hop only');}
    requireOtaPacketCapacity(){
      if(this.usbRawFrameMax>=188)return;
      const error=Error('The USB Companion accepts only '+this.usbRawFrameMax+'-byte commands; LoRa OTA needs 188 bytes. Update the USB Companion firmware first. The remote device has not been changed.');
      error.sourceUpdateRequired='companion';error.sourcePacketCapacity=true;throw error;
    }
    async serve(info,blob,compress){
      if(this.serving)throw Error('An OTA package is already being served');
      this.requireOtaPacketCapacity();
      if(this.rawPacketSupport==='unsupported'){const error=Error('This Companion cannot send raw OTA packets');error.sourceUpdateRequired='companion';throw error;}
      if(!globalThis.LoraOtaCompanionSource)throw Error('Companion seeder is unavailable');
      const t=this.localCliSupported?(this.otaRadioProfile===1
        ?await radioSetup().readTemporary(c=>this.localCommand(c),1)
        :sourceTemp(await this.localCommand('get tempradio'))):
        {radio:await this.savedSourceRadio(),preamble:32};
      this.preamble=t.preamble;this.sourceTempRadio=t;
      if(this.localCliSupported)await this.localCommand('get radio');
      this.sourceSavedRadio=await this.savedSourceRadio();
      await this.checkSourceRadio();
      this.seeder=await globalThis.LoraOtaCompanionSource.Source.create(info,this.sourceKey,{compress});this.serving=true;
      this.advertise();
    }
    async advertise(){if(!this.serving)return;try{await this.sendRadio(this.seeder.advert());}catch(e){this.failure=e;}if(this.serving&&!this.failure)this.advertTimer=setTimeout(()=>this.advertise(),30000);}
    async checkSourceRadio(){
      if(!this.localCliSupported){
        const radio=await this.savedSourceRadio();
        if(!this.sourceTempRadio||radio.some((v,i)=>Math.abs(v-this.sourceTempRadio.radio[i])>0.001))throw Error('Companion saved radio settings changed; OTA sending stopped');
        return;
      }
      const temp=this.otaRadioProfile===1
        ?await radioSetup().readTemporary(c=>this.localCommand(c),1)
        :sourceTemp(await this.localCommand('get tempradio'));
      if(this.otaRadioProfile===1){
        if(temp.state!=='active')throw Error('Companion temporary radio2 is no longer active; OTA sending stopped');
        const primary=String(await this.localCommand('get tempradio')).trim().replace(/^>\s*/,'');
        if(!/^(?:off|inactive|TempRadio inactive)$/i.test(primary))throw Error('Companion primary temporary radio changed while OTA uses radio2');
      }
      await this.localCommand('get radio');
      const equal=(a,b)=>a.length===b.length&&a.every((v,i)=>Number.isFinite(v)&&Math.abs(v-b[i])<=0.001);
      if(!this.sourceTempRadio||!equal(temp.radio,this.sourceTempRadio.radio)||
          temp.preamble!==this.sourceTempRadio.preamble||!equal(this.radio,this.sourceSavedRadio)||
          (!this.otaSupported&&!equal(this.radio,temp.radio)))
        throw Error('Companion radio or temporary profile changed; OTA sending stopped');
    }
    async pumpRadio(){if(this.radioRunning)return;this.radioRunning=true;try{while(this.serving&&this.radioQueue.length){const requests=await this.seeder.replies(this.radioQueue.shift());for(const r of requests){if(!this.serving)break;await this.sendRadio(r);}}}catch(e){this.failure=e;}finally{this.radioRunning=false;}}
    sendRadio(raw){
      if(!(raw instanceof Uint8Array)||raw.length<3||raw.length>186||raw[0]!==0x32||raw[1]!==0)throw Error('Invalid zero-hop OTA frame');
      const operation=this.txQueue.then(()=>this.sendRadioOrdered(raw));this.txQueue=operation.catch(()=>{});return operation;
    }
    async sendRadioOrdered(raw){
      const delay=this.nextTx-Date.now();if(delay>0)await sleep(delay);if(!this.serving)return;
      if(raw.length+2>this.usbRawFrameMax){const error=Error('The OTA packet exceeds the Companion USB command limit. Update the USB Companion firmware first.');error.sourceUpdateRequired='companion';error.sourcePacketCapacity=true;throw error;}
      if(!this.otaSupported)await this.checkSourceRadio();
      if(!this.serving)return;
      try{await this.command(join(Uint8Array.of(65,1),raw),0);}
      catch(error){if(error.companionErrorCode===1){this.rawPacketSupport='unsupported';this.sourceUpdateRequired='companion';error.sourceUpdateRequired='companion';error.message='Companion firmware does not support raw OTA packets';}throw error;}
      this.rawPacketSupport='accepted'; // queued by the Companion, not proof of RF transmission
      const [,bw,sf,cr]=this.sourceTempRadio?.radio||this.radio,symbol=2**sf/(bw*1000),de=symbol>=.016?1:0;
      const airtime=(this.preamble+4.25+8+Math.max(0,Math.ceil((8*raw.length-4*sf+44)/(4*(sf-2*de))))*cr)*symbol;
      this.nextTx=Date.now()+Math.ceil(Math.max(.25,2*airtime+.1)*1000);
    }
    async stopServing(){this.serving=false;clearTimeout(this.advertTimer);this.radioQueue=[];while(this.radioRunning)await sleep(10);await this.txQueue;this.seeder=null;}
    async close(){
      await this.stopServing();
      if(this.running&&!this.failure){
        try{
          await this.closeRadioSession();
          if(this.supportsOtaRadio2&&this.otaRadioProfile!==0)await this.selectOtaRadio(0);
        }catch(_){} // USB teardown also clears the firmware's RAM-only selection.
      }
      this.running=false;if(this.pending){clearTimeout(this.pending.timer);this.pending.reject(Error('Connection closed'));this.pending=null;}
      if(this.reader){try{await this.reader.cancel();}catch(_){}try{await this.readTask;}catch(_){}try{this.reader.releaseLock();}catch(_){}}
      if(this.writer){try{await this.writer.close();}catch(_){}try{this.writer.releaseLock();}catch(_){}}
      if(this.portOpened){try{await this.port.close();this.portOpened=false;}catch(_){}}this.reader=this.writer=null;this.investigationPassword=null;this.radioProbeAdmission=null;this.sourceKey=null;
    }
  }
  return {Decoder,CompanionTransport,preservedMessages,preservedSnapshot,clearPreservedMessages};
});
