/* Exclusive USB Binary Companion transport. Zero-hop only. */
(function(root,factory){
  const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.LoraOtaCompanion=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const enc=new TextEncoder(),dec=new TextDecoder(),sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const hex=b=>Array.from(b,x=>x.toString(16).padStart(2,'0')).join('');
  function unhex(s){if(!/^[a-f0-9]{64}$/i.test(s))throw Error('Enter the complete target public key');return Uint8Array.from(s.match(/../g),x=>parseInt(x,16));}
  function join(...chunks){const out=new Uint8Array(chunks.reduce((n,c)=>n+c.length,0));let at=0;for(const c of chunks){out.set(c,at);at+=c.length;}return out;}
  function u32(n){const b=new Uint8Array(4);new DataView(b.buffer).setUint32(0,n,true);return b;}
  function packet(payload){if(!payload.length||payload.length>300)throw Error('Invalid Companion frame size');return join(Uint8Array.of(60,payload.length&255,payload.length>>8),payload);}
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
      this.port=port;this.options=options;this.knownRepeater=String(options.knownRepeater||'').toLowerCase();if(this.knownRepeater)unhex(this.knownRepeater);
      this.aclOnly=false;this.supportsRoutedPath=false;this.sourceKey=null;this.reader=null;this.writer=null;this.running=false;
      this.failure=null;this.decoder=new Decoder();this.pending=null;this.loginWaiter=null;this.queue=Promise.resolve();this.admin=Promise.resolve();
      this.authenticated=false;this.guard=false;this.used=new Set();this.timestamp=0;this.serving=false;this.seeder=null;this.radioQueue=[];this.radioRunning=false;this.nextTx=0;this.txQueue=Promise.resolve();this.contactList=[];this.peerType=2;
      this.otaSupported=false;this.otaUnavailableReason='This Companion needs firmware with guarded zero-hop CLI and radio support';
    }
    async open(){
      try{
        await this.port.open({baudRate:115200,bufferSize:4096});this.reader=this.port.readable.getReader();this.writer=this.port.writable.getWriter();this.running=true;
        this.readTask=this.readLoop();await sleep(this.options.openSettle??750);
        const info=await this.command(Uint8Array.of(1,3,32,32,32,32,32,32,109,99,99,108,105),5);
        if(info.length<58)throw Error('Invalid Companion identity response');this.sourceKey=hex(info.slice(4,36));
        const v=new DataView(info.buffer,info.byteOffset,info.byteLength);
        this.radio=[v.getUint32(48,true)/1000,v.getUint32(52,true)/1000,info[56],info[57]];
        // Contact browsing is available even on stock/older Companions. An
        // explicit unsupported response is harmless; an I/O timeout is not.
        try {
          const status=await this.localCommand('get ota.direct');
          if (/^>?\s*ota\.direct v=2 active=[01] busy=[01]\s*$/i.test(status)) {
            const cap=await this.localCommand('get ota.direct.capabilities');
            this.otaSupported=/^>?\s*ota\.direct\.cap guarded_cli=1 guarded_radio=1\s*$/i.test(cap);
            if(this.otaSupported&&!/^>?\s*ota\.direct v=2 active=0 busy=0\s*$/i.test(status))
              this.otaUnavailableReason='The Companion OTA mailbox is already in use';
            else if(this.otaSupported)this.otaUnavailableReason='';
          }
        }catch(error){if(this.failure||error.message!=='Companion rejected the command')throw error;}
        return this;
      }catch(e){await this.close();throw e;}
    }
    async readLoop(){try{while(this.running){const {value,done}=await this.reader.read();if(done){if(this.running)throw Error('Companion disconnected');break;}for(const frame of this.decoder.feed(value))this.onFrame(frame);}}
      catch(e){if(this.running){this.failure=e;this.pending?.reject(e);this.loginWaiter?.reject(e);}}}
    onFrame(frame){
      if(frame[0]===0x88&&frame.length>5&&this.serving){if(this.radioQueue.length<64){this.radioQueue.push(frame.slice(3));this.pumpRadio();}return;}
      if((frame[0]===0x85||frame[0]===0x86)&&this.loginWaiter&&frame.length>=8&&hex(frame.slice(2,8))===this.knownRepeater.slice(0,12)){
        const waiter=this.loginWaiter;this.loginWaiter=null;
        if(frame[0]!==0x85||frame.length<13||!(frame[1]&1)||(frame[12]&7)!==3)waiter.reject(Error('Repeater rejected the admin login'));else waiter.resolve();return;
      }
      if(this.pending?.contacts){
        const p=this.pending;
        if(frame[0]===1){clearTimeout(p.timer);this.pending=null;p.reject(Error('Companion could not list contacts'));return;}
        if(frame[0]===2){if(p.started||frame.length!==5){clearTimeout(p.timer);this.pending=null;p.reject(Error('Invalid contact list'));return;}p.started=true;return;}
        if(frame[0]===3){
          if(!p.started||frame.length!==148||p.rows.length>=4096){clearTimeout(p.timer);this.pending=null;p.reject(Error('Invalid contact record'));return;}
          const type=frame[33],flags=frame[34],path=frame[35];
          if(path!==255&&(path>>6)===3){clearTimeout(p.timer);this.pending=null;p.reject(Error('Invalid contact path'));return;}
          const nameBytes=frame.slice(100,132),end=nameBytes.indexOf(0),key=hex(frame.slice(1,33));
          p.rows.push({key,publicKey:key,type,favorite:!!(flags&1),hops:path===255?null:path&63,name:dec.decode(end<0?nameBytes:nameBytes.slice(0,end))});return;
        }
        if(frame[0]===4){clearTimeout(p.timer);this.pending=null;if(!p.started||frame.length!==5){p.reject(Error('Incomplete contact list'));return;}
          p.rows.sort((a,b)=>Number(b.favorite)-Number(a.favorite)||a.type-b.type||(a.hops??Infinity)-(b.hops??Infinity)||a.name.localeCompare(b.name)||a.publicKey.localeCompare(b.publicKey));
          this.contactList=p.rows;p.resolve(p.rows.slice());return;}
      }
      if(this.pending&&(frame[0]===1||frame[0]===this.pending.code)){
        const pending=this.pending;this.pending=null;clearTimeout(pending.timer);
        if(frame[0]===1)pending.reject(Error('Companion rejected the command'));else pending.resolve(frame);
      }
    }
    command(payload,code){
      const run=this.queue.then(()=>new Promise((resolve,reject)=>{
        if(!this.running||this.failure)return reject(this.failure||Error('Companion is disconnected'));
        const timer=setTimeout(()=>{this.pending=null;this.failure=Error('Companion response timed out; reconnect before retrying');reject(this.failure);},this.options.commandTimeout??15000);
        this.pending={code,resolve,reject,timer};this.writer.write(packet(payload)).catch(e=>{clearTimeout(timer);this.pending=null;this.failure=e;reject(e);});
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
      const match=this.contactList.find(c=>c.publicKey===key);
      this.knownRepeater=key;this.peerType=match?.type||2;
    }
    async localCommand(text){
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
    async login(password){
      if(typeof password!=='string'||enc.encode(password).length>15||/[\r\n\0]/.test(password))throw Error('Admin password must fit in 15 bytes');
      if(!this.knownRepeater)throw Error('Select a target first');
      if(!this.otaSupported)throw Error(this.otaUnavailableReason);
      const status=await this.localCommand('get ota.direct');
      if(!/^>?\s*ota\.direct v=2 active=0 busy=0\s*$/i.test(status))throw Error('The Companion OTA mailbox is already in use');
      await this.checkClock();await this.guardMode(1,Uint8Array.of(this.peerType));this.guard=true;
      const response=new Promise((resolve,reject)=>{const timer=setTimeout(()=>{this.loginWaiter=null;reject(Error('Repeater login timed out'));},this.options.remoteTimeout??75000);this.loginWaiter={resolve:()=>{clearTimeout(timer);resolve();},reject:e=>{clearTimeout(timer);reject(e);}};});
      try{await this.command(join(Uint8Array.of(26),unhex(this.knownRepeater),enc.encode(password)),6);await response;}
      catch(e){this.loginWaiter?.reject(e);await response.catch(()=>{});throw e;}
      await this.guardMode(2);
      const deadline=Date.now()+(this.options.remoteTimeout??75000);while(Date.now()<deadline){
        const status=await this.localCommand('get ota.direct');if(/^>?\s*ota\.direct v=2 active=1 busy=0\s*$/i.test(status))break;
        if(!/^>?\s*ota\.direct v=2 active=1 busy=1\s*$/i.test(status))throw Error('Companion direct session changed');await sleep(100);
      }
      if(Date.now()>=deadline)throw Error('Return path timed out');
      const proof=await this.remoteReply('get public.key');
      if(proof.replace(/^>\s*/,'').trim().toLowerCase()!==this.knownRepeater)throw Error('Repeater did not prove its full public key');
      this.authenticated=true;
    }
    async remoteReply(text){
      if(!text||/[\r\n\0]/.test(text)||enc.encode(text).length>152)throw Error('Invalid remote command');
      if(this.used.size>=7921)throw Error('Reconnect before sending more commands');
      let correlation;do{const r=crypto.getRandomValues(new Uint8Array(2));correlation=String.fromCharCode(33+r[0]%90,33+r[1]%90);}
      while(correlation.includes('|')||this.used.has(correlation));this.used.add(correlation);
      const tag=correlation+'|';await this.guardMode(4,enc.encode(tag));
      this.timestamp=Math.max(Math.floor(Date.now()/1000),this.timestamp+1);
      await this.command(join(Uint8Array.of(2,1,0),u32(this.timestamp),unhex(this.knownRepeater).slice(0,6),enc.encode(tag+text)),6);
      const deadline=Date.now()+(this.options.remoteTimeout??75000);let result=[],total=null;
      while(Date.now()<deadline){
        const reply=await this.localCommand('get ota.direct.reply '+result.length);
        const m=reply.match(/^>?\s*ota\.reply req=([!-~]{2}) state=(idle|pending|ready) bytes=(\d+) off=(\d+) data=([a-f0-9]+|-)\s*$/i);
        if(!m||m[1]!==correlation||+m[3]>160||+m[4]!==result.length||m[2]==='idle')throw Error('Companion command mailbox changed');
        if(m[2]==='pending'){if(result.length||+m[3]||m[5]!=='-')throw Error('Invalid pending command mailbox');await sleep(100);continue;}
        const n=+m[3],data=m[5]==='-'?[]:(m[5].match(/../g)||[]).map(x=>parseInt(x,16));
        if((m[5]!=='-'&&m[5].length%2)||data.length>40||result.length+data.length>n||(!data.length&&result.length!==n)||(total!==null&&n!==total))throw Error('Invalid Companion reply page');
        total=n;result.push(...data);if(result.length===total)return dec.decode(Uint8Array.from(result));
      }throw Error('Remote command timed out; result is unknown');
    }
    validate(key){if(String(key).toLowerCase()!==this.knownRepeater)throw Error('Target differs from the selected repeater');}
    async verifyPeer(key,password=''){this.validate(key);return this.withAdmin(async()=>{if(!this.authenticated)await this.login(password);return this.knownRepeater;});}
    async remoteCommand(key,password,text){this.validate(key);return this.withAdmin(async()=>{if(!this.authenticated)await this.login(password);return this.remoteReply(text);});}
    async resetRemoteSession(key){this.validate(key);return this.withAdmin(async()=>{if(this.guard)await this.guardMode(3);this.guard=false;this.authenticated=false;});}
    async setPath(){throw Error('Companion OTA currently supports zero-hop only');}
    async serve(info,blob,compress){
      if(this.serving)throw Error('An OTA package is already being served');
      if(!globalThis.LoraOtaCompanionSource)throw Error('Companion seeder is unavailable');
      const t=await this.localCommand('get tempradio');if(!/\bactive\b/i.test(t))throw Error('Open a same-channel temporary radio first');
      const match=t.match(/\bpreamble=(\d+)\b/i);this.preamble=match?Number(match[1]):0;
      if(this.preamble<1||this.preamble>65535)throw Error('Cannot confirm the Companion radio preamble for safe transmit pacing');
      this.seeder=await globalThis.LoraOtaCompanionSource.Source.create(info,this.sourceKey,{compress});this.serving=true;
      this.advertise();
    }
    async advertise(){if(!this.serving)return;try{await this.sendRadio(this.seeder.advert());}catch(e){this.failure=e;}if(this.serving&&!this.failure)this.advertTimer=setTimeout(()=>this.advertise(),30000);}
    async pumpRadio(){if(this.radioRunning)return;this.radioRunning=true;try{while(this.serving&&this.radioQueue.length){const requests=await this.seeder.replies(this.radioQueue.shift());for(const r of requests){if(!this.serving)break;await this.sendRadio(r);}}}catch(e){this.failure=e;}finally{this.radioRunning=false;}}
    sendRadio(raw){
      if(!(raw instanceof Uint8Array)||raw.length<3||raw.length>186||raw[0]!==0x32||raw[1]!==0)throw Error('Invalid zero-hop OTA frame');
      const operation=this.txQueue.then(()=>this.sendRadioOrdered(raw));this.txQueue=operation.catch(()=>{});return operation;
    }
    async sendRadioOrdered(raw){
      const delay=this.nextTx-Date.now();if(delay>0)await sleep(delay);if(!this.serving)return;
      await this.command(join(Uint8Array.of(65,1),raw),0);
      const [,bw,sf,cr]=this.radio,symbol=2**sf/(bw*1000),de=symbol>=.016?1:0;
      const airtime=(this.preamble+4.25+8+Math.max(0,Math.ceil((8*raw.length-4*sf+44)/(4*(sf-2*de))))*cr)*symbol;
      this.nextTx=Date.now()+Math.ceil(Math.max(.25,2*airtime+.1)*1000);
    }
    async stopServing(){this.serving=false;clearTimeout(this.advertTimer);this.radioQueue=[];while(this.radioRunning)await sleep(10);await this.txQueue;this.seeder=null;}
    async close(){
      await this.stopServing();if(this.running&&!this.failure&&this.guard){try{await this.guardMode(3);}catch(_){}}
      this.running=false;if(this.pending){clearTimeout(this.pending.timer);this.pending.reject(Error('Connection closed'));this.pending=null;}
      if(this.reader){try{await this.reader.cancel();}catch(_){}try{await this.readTask;}catch(_){}try{this.reader.releaseLock();}catch(_){}}
      if(this.writer){try{await this.writer.close();}catch(_){}try{this.writer.releaseLock();}catch(_){}}
      try{await this.port.close();}catch(_){}this.sourceKey=null;
    }
  }
  return {Decoder,CompanionTransport};
});
