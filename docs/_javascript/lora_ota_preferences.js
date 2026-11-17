/* Local, non-secret form preferences for the browser LoRa updater. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.LoraOtaPreferences=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const KEY='meshcore-browser-lora-ota-preferences-v1';
  const selections={connection:['usb','wifi','bluetooth'],source:['auto','companion','repeater'],
    'auto-objective':['steps','data'],'auto-depth':['standard','deep']};
  const toggles=['acl','quiet-forwarding','auto-search'];
  const roles=[...Object.keys(selections),'wifi-url','key','relays','candidates','snr','age',...toggles];
  const radioFields=['radio-frequency','radio-bandwidth','radio-sf','radio-cr',
    'radio-preamble','radio2-preamble','radio-minutes'];
  const radioLimits={'radio-frequency':[150,2500], 'radio-bandwidth':[7.8,500],
    'radio-sf':[5,12], 'radio-cr':[4,8], 'radio-preamble':[1,65535],
    'radio2-preamble':[1,65535], 'radio-minutes':[1,10080]};
  function validRadio(values){
    if(!values||typeof values!=='object'||Array.isArray(values))return false;
    return radioFields.every(role=>{
      const text=values[role];if(typeof text!=='string'||!text.trim())return false;
      const n=Number(text),[min,max]=radioLimits[role];
      if(!Number.isFinite(n)||n<min||n>max)return false;
      if(['radio-sf','radio-cr','radio-preamble','radio2-preamble','radio-minutes'].includes(role)&&!Number.isInteger(n))return false;
      return role!=='radio-bandwidth'||['7.8','10.4','15.6','20.8','31.25','41.7','62.5','125','250','500'].includes(text);
    });
  }
  function privateAddress(value){
    if(typeof value!=='string')return '';
    const parts=value.trim().split('.');
    if(parts.length!==4||parts.some(p=>!/^(?:0|[1-9]\d{0,2})$/.test(p)||+p>255))return '';
    const [a,b,,d]=parts.map(Number);
    return d>0&&d<255&&(a===10||(a===172&&b>=16&&b<=31)||(a===192&&b===168))?parts.join('.'):'';
  }
  function validKey(value){return typeof value==='string'&&/^[0-9a-f]{64}$/i.test(value)?value.toLowerCase():'';}
  function keyList(value,limit){
    if(typeof value!=='string'||value.length>limit*68)return '';
    const keys=value.trim()?value.trim().split(/[\s,]+/):[];
    if(keys.length>limit||keys.some(k=>!validKey(k)))return '';
    const normalized=keys.map(validKey);
    return new Set(normalized).size===normalized.length?normalized.join('\n'):'';
  }
  function validNumber(value,min,max,integer=false){
    if(typeof value!=='string'||!value.trim())return '';
    const n=Number(value);
    return Number.isFinite(n)&&n>=min&&n<=max&&(!integer||Number.isInteger(n))?value:'';
  }
  function install(ui,{ignoreSavedConnection=false,storage,getTargetKey}={}){
    const element=role=>ui.querySelector('[data-role="'+role+'"]');
    let manualRadio=false;
    const read=()=>{
      try { const data=JSON.parse((storage||globalThis.localStorage).getItem(KEY)||'null');
        return data&&typeof data==='object'&&!Array.isArray(data)?data:null;
      } catch(_) { return null; }
    };
    function restore(){
      const data=read();if(!data)return;
      for(const [role,allowed] of Object.entries(selections)){
        if(role==='connection'&&ignoreSavedConnection)continue;
        const field=element(role);if(field&&allowed.includes(data[role]))field.value=data[role];
      }
      for(const role of toggles){const field=element(role);if(field&&typeof data[role]==='boolean')field.checked=data[role];}
      const address=privateAddress(data['wifi-url']), key=validKey(data.key);
      if(address&&element('wifi-url'))element('wifi-url').value=address;
      if(key&&element('key'))element('key').value=key;
      for(const [role,limit] of [['relays',8],['candidates',128]]){
        const value=keyList(data[role],limit);if(value&&element(role))element(role).value=value;
      }
      for(const [role,min,max] of [['snr',-200,200],['age',0.001,168]]){
        const value=validNumber(data[role],min,max);if(value&&element(role))element(role).value=value;
      }
    }
    // Region options are added by the updater after initial form restoration.
    // Restoring values never changes either radio until the user prepares it.
    function restoreRadio(){
      const data=read();if(!data)return false;
      const region=element('radio-region');
      if(region&&typeof data['radio-region']==='string'&&Array.from(region.options||[]).some(o=>o.value===data['radio-region']))
        region.value=data['radio-region'];
      const secondary=validNumber(data['radio2-preamble'],1,65535,true);
      if(secondary&&element('radio2-preamble'))element('radio2-preamble').value=secondary;
      if(data.radioManual!==true||!validRadio(data.radio))return false;
      for(const role of radioFields){const field=element(role);if(!field)return false;}
      for(const role of radioFields)element(role).value=data.radio[role];
      manualRadio=true;return true;
    }
    function save({targetKey}={}){
      try{
        const data={};
        for(const [role,allowed] of Object.entries(selections)){
          const value=element(role)?.value;if(allowed.includes(value))data[role]=value;
        }
        for(const role of toggles){const field=element(role);if(field)data[role]=!!field.checked;}
        data['wifi-url']=privateAddress(element('wifi-url')?.value);
        data.key=validKey(targetKey===undefined
          ?(typeof getTargetKey==='function'?getTargetKey():element('key')?.value):targetKey);
        for(const [role,limit] of [['relays',8],['candidates',128]])data[role]=keyList(element(role)?.value,limit);
        for(const [role,min,max] of [['snr',-200,200],['age',0.001,168]])data[role]=validNumber(element(role)?.value,min,max);
        data['radio2-preamble']=validNumber(element('radio2-preamble')?.value,1,65535,true);
        const region=element('radio-region');
        if(region&&Array.from(region.options||[]).some(o=>o.value===region.value))data['radio-region']=region.value;
        const radio=Object.fromEntries(radioFields.map(role=>[role,element(role)?.value]));
        if(manualRadio&&validRadio(radio)){data.radioManual=true;data.radio=radio;}
        (storage||globalThis.localStorage).setItem(KEY,JSON.stringify(data));
      }catch(_) { /* Storage may be unavailable; the updater still works. */ }
    }
    restore();
    for(const role of roles){const field=element(role);if(!field)continue;
      field.addEventListener('change',()=>save(role==='key'?{targetKey:field.value}:undefined));
      if(['key','wifi-url','relays','candidates','snr','age'].includes(role))field.addEventListener('input',()=>save(role==='key'?{targetKey:field.value}:undefined));
    }
    for(const role of radioFields){const field=element(role);if(!field)continue;
      for(const event of ['input','change'])field.addEventListener(event,()=>{manualRadio=true;save();});
    }
    element('radio-region')?.addEventListener('change',()=>save());
    element('radio-reset')?.addEventListener('click',()=>{manualRadio=false;save();});
    return {save,restore,restoreRadio};
  }
  return {install,KEY};
});
