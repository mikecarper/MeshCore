"""Run guarded radio2-only administration through a real browser and repeater serial framing."""
import unittest
from test_browser_lora_ota_nrf_runtime import run_harness

HARNESS = r'''<script>
window.addEventListener('load',async()=>{
 const enc=new TextEncoder(),dec=new TextDecoder(),source='a'.repeat(64),target='b'.repeat(64),commands=[];
 let jobResult='',localState='off',targetState='off',selected='radio2';
 const hex=bytes=>Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
 function setting(text,remote){
   if(text==='get radio2.only')return '> '+(remote?targetState:localState);
   const match=/^set radio2.only (on|off) ([A-F0-9]{16})$/.exec(text);
   if(!match)throw Error('Unexpected setting '+text);
   const value=match[1]==='on'?'on '+match[2]:'off';
   if(remote)targetState=value;else localState=value;
   return 'OK - radio2.only '+match[1];
 }
 class Port{
  constructor(){this.q=[];this.waiter=null;}
  push(bytes){if(this.waiter){let r=this.waiter;this.waiter=null;r({value:bytes,done:false});}else this.q.push(bytes);}
  async open(){this.readable={getReader:()=>({read:()=>this.q.length?Promise.resolve({value:this.q.shift(),done:false}):new Promise(r=>this.waiter=r),cancel:async()=>{this.waiter?.({done:true});this.waiter=null;},releaseLock(){}})};
   this.writable={getWriter:()=>({write:async bytes=>this.write(bytes),close:async()=>{},releaseLock(){}})};}
  async close(){}
  async write(bytes){
    const text=dec.decode(bytes).trim();commands.push(text);let reply;
    if(text==='get public.key')reply=source;
    else if(text==='ota.peer status')reply='OTApeer v=1 session=0 busy=0 peers=1';
    else if(text==='ota.peer capabilities')reply='OTApeer known_repeater=1 routed_path=1';
    else if(text==='ota.peer path')reply='OTApeer path=off';
    else if(text==='ota.peer radio')reply='OTApeer radio='+selected;
    else if(text.startsWith('ota.peer radio ')){selected=text.slice(15);reply='OTApeer radio='+selected;}
    else if(text==='ota.peer close')reply='OTApeer closed';
    else if(text==='get radio2.only'||text.startsWith('set radio2.only '))reply=setting(text,false);
    else if(text.startsWith('ota.peer cmd.acl '+target+' ')){
      const command=text.slice(('ota.peer cmd.acl '+target+' ').length);
      jobResult=command==='get public.key'?target:setting(command,true);reply='OTApeer job=01020304 state=pending';
    }else if(text.startsWith('ota.peer result')){
      const off=Number(text.split(' ')[2]||0),data=enc.encode(jobResult),part=data.slice(off,off+48);
      reply=`OTApeer job=01020304 state=ok bytes=${data.length} off=${off} data=${part.length?hex(part):'-'}`;
    }else throw Error('Unexpected CLI '+text);
    queueMicrotask(()=>this.push(enc.encode(text+'\r\n-> '+reply+'\r\n')));
  }
 }
 const report=async value=>fetch('/__result',{method:'POST',body:JSON.stringify(value)});
 try{
  const port=new Port(),link=new LoraOtaSerial.RepeaterTransport(port,{knownRepeater:target,aclOnly:true,openSettle:1,pollMs:1});
  await link.open();link.ownsPeer=true;link.supportsOtaRadio2=true;link.otaRadioProfile=1;link.otaTargetProfile=1;
  const helper=LoraOtaRadio2Only.create();
  await helper.activate(link,target,'',{sourceProfile:1,targetProfile:1});
  const during=[localState,targetState],record=helper.load();
  await helper.restore(link,target,'');const after=[localState,targetState],cleared=helper.load()===null;
  await link.close();await report({during,after,cleared,record,commands});
 }catch(error){await report({error:String(error),commands,localState,targetState});}
});
</script>'''

class BrowserRepeaterOnlyRuntime(unittest.TestCase):
 def test_acl_serial_burst_pins_and_restores_both_endpoints(self):
  result=run_harness(HARNESS,timeout=60)
  self.assertNotIn('error',result,result)
  self.assertTrue(all(s.startswith('on ') for s in result['during']))
  self.assertEqual(result['after'],['off','off']);self.assertTrue(result['cleared'])
  self.assertTrue(result['record']['sourceOwned']);self.assertTrue(result['record']['targetOwned'])
  self.assertTrue(any('cmd.acl' in c and 'set radio2.only on ' in c for c in result['commands']))

if __name__=='__main__':unittest.main()
