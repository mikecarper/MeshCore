/* Contact choices for the USB Companion updater. */
(function (root, factory) {
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.LoraOtaContacts=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  function choices(contacts, query='') {
    const text=String(query).trim().toLowerCase(), key=text.replace(/[\s:]/g,'');
    return (Array.isArray(contacts)?contacts:[]).filter(c=>c && [2,3,4].includes(c.type) &&
      /^[a-f0-9]{64}$/i.test(c.publicKey||'') &&
      (!text || String(c.name||'').toLowerCase().includes(text) || c.publicKey.toLowerCase().startsWith(key)))
      .slice().sort((a,b)=>(Number(!!b.favorite)-Number(!!a.favorite)) ||
        (a.type-b.type) || ((Number.isInteger(a.hops)?a.hops:Infinity)-(Number.isInteger(b.hops)?b.hops:Infinity)) ||
        String(a.name||'').localeCompare(String(b.name||''),undefined,{sensitivity:'base'}) ||
        a.publicKey.localeCompare(b.publicKey));
  }
  function label(contact) {
    const type={2:'Repeater',3:'Room',4:'Sensor'}[contact.type]||'Node';
    const distance=Number.isInteger(contact.hops) ? contact.hops===0?'direct':contact.hops+' hop'+(contact.hops===1?'':'s') : 'path unknown';
    return (contact.favorite?'★ ':'')+(contact.name||contact.publicKey.slice(0,12))+' — '+type+', '+distance+' ('+contact.publicKey.slice(0,12).toUpperCase()+')';
  }
  return {choices,label};
});
