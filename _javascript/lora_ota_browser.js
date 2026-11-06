/* Direct browser LoRa updater. All package and radio traffic stays local. */
(function () {
  'use strict';
  const ui = document.querySelector('[data-lora-ota]');
  if (!ui) return;
  const $ = name => ui.querySelector('[data-role="' + name + '"]');
  let link, session, info, expectedSigner, activeTask, preparing, chainPrepared, chainConfig;
  let contacts=[], targetLocked=false, contactKey='';
  let catalogFile=null, catalogBaseFile=null, catalogDest=null, catalogVersion='', catalogInstallSupported=false, catalogCandidates=[], catalogGeneration=0;
  let preservedCount=0, preservedExportSnapshot=null, preservedExportToken=null, preservedExportInterrupted=false;
  const log = text => { $('status').textContent += String(text) + '\n'; $('status').scrollTop = $('status').scrollHeight; };
  const fail = error => log('Error: ' + (error?.message || error));
  const bytes = (file, limit = 64 * 1024 * 1024) => {
    if (!file || !Number.isSafeInteger(file.size) || file.size <= 0 || file.size > limit) throw Error('Firmware file size is unsupported');
    return file.arrayBuffer().then(b => new Uint8Array(b));
  };
  async function firmwareBytes(file, limit = 64 * 1024 * 1024) {
    if (file.name.toLowerCase().endsWith('.zip')) {
      const result = await MeshCoreLoraOtaZip.loadApplication(await bytes(file, MeshCoreLoraOtaZip.MAX_ARCHIVE));
      if (result.image.length > limit) throw Error('Firmware image size is unsupported');
      return result.image;
    }
    return bytes(file, limit);
  }
  function journal(state) { localStorage.setItem('meshcore-browser-lora-ota', JSON.stringify({...state, at: new Date().toISOString()})); }
  function readJournal() { try { return JSON.parse(localStorage.getItem('meshcore-browser-lora-ota')); } catch (_) { return null; } }
  const writeStepJournal = state => localStorage.setItem('meshcore-browser-lora-ota-chain-step', JSON.stringify(state));
  const readStepJournal = () => { try { return JSON.parse(localStorage.getItem('meshcore-browser-lora-ota-chain-step')); } catch (_) { return null; } };
  const writeChainJournal = state => localStorage.setItem('meshcore-browser-lora-ota-chain', JSON.stringify({...state, at:Date.now()}));
  const readChainJournal = () => { try { return JSON.parse(localStorage.getItem('meshcore-browser-lora-ota-chain')); } catch (_) { return null; } };
  const readChainMeta = () => { try { return JSON.parse(localStorage.getItem('meshcore-browser-lora-ota-chain-meta')); } catch (_) { return null; } };
  const editable = ['source', 'key', 'acl', 'relays', 'candidates', 'snr', 'age', 'password', 'file', 'base', 'image', 'signer',
    'chain-files', 'chain-attest', 'chain-objective', 'chain-preamble', 'chain-minutes'];
  function busy(active) {
    if(link && $('source').value==='companion' && (link.guard || link.authenticated || link.investigationEnabled || link.investigationAuthenticated || link.serving))targetLocked=true;
    for (const name of editable) $(name).disabled = active || (!!link && (name === 'source' || name === 'acl' || name === 'relays' || (name === 'key' && ($('source').value === 'repeater' || targetLocked))));
    $('contact-search').disabled = active || !link || $('source').value !== 'companion' || targetLocked;
    updateContactButtons();
    $('investigate').disabled = active || !link;
    $('legacy-investigate').disabled = active || !link;
    $('legacy-inbox-export').disabled = active || !preservedCount;
    $('legacy-inbox-clear').disabled = active || !preservedCount || !preservedExportSnapshot;
    $('firmware-role').disabled = active || !link;
    $('firmware-choice').disabled = active || !link;
    $('firmware-download').disabled = active || !link || !$('firmware-choice').value;
    for (const name of ['repeat-read', 'repeat-off', 'repeat-on']) $(name).disabled = active || !link;
    $('repeat-node').disabled = $('repeat-key').disabled = $('repeat-kind').disabled = active;
    const pending = readChainJournal();
    $('chain-plan').disabled = active || !link;
    $('chain-run').disabled = active || !link || !chainPrepared || !chainConfig || !!pending && pending.phase !== 'complete';
    $('chain-resume').disabled = active || !link || !chainPrepared || !chainConfig || !pending || pending.phase === 'complete';
  }
  function invalidate() { session = info = chainPrepared = chainConfig = null; $('transfer').disabled = true; $('resume').disabled = true;
    $('install').disabled = true; $('chain-run').disabled = true; $('chain-resume').disabled = true; }
  function refreshPreservedMessages() {
    if(typeof LoraOtaCompanion.preservedMessages!=='function')return;
    try {
      const saved=LoraOtaCompanion.preservedMessages();
      preservedCount=saved.length;
      if(preservedExportSnapshot && JSON.stringify(saved)!==preservedExportSnapshot) {
        preservedExportSnapshot=null;preservedExportToken=null;preservedExportInterrupted=false;
      }
      $('legacy-inbox').hidden=!saved.length;
      $('legacy-inbox-status').textContent=saved.length+' '+(saved.length===1?'message':'messages')+' saved in this browser.';
      $('legacy-inbox-messages').textContent=saved.map(message=>{
        const parts=[String(message.at||''),String(message.kind||'message')];
        if(message.sender)parts.push('from '+message.sender);
        if(message.channel!==undefined && message.channel!==null)parts.push('channel '+message.channel);
        return parts.filter(Boolean).join(' · ')+'\n'+String(message.text||message.warning||'[Raw message included in download]');
      }).join('\n\n');
      $('legacy-inbox-export').disabled=!preservedCount || !!activeTask;
      $('legacy-inbox-clear').disabled=!preservedCount || !preservedExportSnapshot || !!activeTask;
    } catch(error) {
      $('legacy-inbox').hidden=false;
      $('legacy-inbox-status').textContent='Could not read messages saved in this browser: '+(error?.message||error);
    }
  }
  $('legacy-inbox-export').addEventListener('click',()=>{
    try {
      const snapshot=LoraOtaCompanion.preservedSnapshot(), saved=snapshot.messages;
      const url=URL.createObjectURL(new Blob([JSON.stringify(saved,null,2)],{type:'application/json'}));
      const a=document.createElement('a');a.href=url;a.download='companion-saved-messages.json';document.body.appendChild(a);a.click();a.remove();
      setTimeout(()=>URL.revokeObjectURL(url),30000);
      preservedExportSnapshot=JSON.stringify(saved);
      preservedExportToken=snapshot.token;
      preservedExportInterrupted=saved.some(message=>message.kind==='interrupted');
      $('legacy-inbox-status').textContent='Download started. Confirm it is saved before clearing these browser copies.';
      $('legacy-inbox-clear').disabled=false;
    } catch(error) { $('legacy-inbox-status').textContent='Could not download saved messages: '+(error?.message||error); }
  });
  $('legacy-inbox-clear').addEventListener('click',async()=>{
    if(!preservedExportSnapshot || !confirm('Clear the saved browser copies, including interrupted or possibly lost message records? They cannot be restored to the Companion.'))return;
    try {
      await LoraOtaCompanion.clearPreservedMessages(preservedExportToken,preservedExportInterrupted);
      preservedExportSnapshot=null;preservedExportToken=null;preservedExportInterrupted=false;refreshPreservedMessages();
    } catch(error) { preservedExportSnapshot=null;preservedExportToken=null;preservedExportInterrupted=false;refreshPreservedMessages();$('legacy-inbox-status').textContent='Could not clear saved messages: '+(error?.message||error)+'. Download the current messages and try again.'; }
  });
  refreshPreservedMessages();
  function clearInvestigation() {
    $('investigation').hidden = true; $('investigation').textContent = '';
    $('legacy-warning').hidden = true;
    $('legacy-source-block').hidden = true;
    catalogGeneration++; catalogFile=null; catalogBaseFile=null; catalogDest=null; catalogVersion=''; catalogInstallSupported=false; catalogCandidates=[];
    $('firmware-role-wrap').hidden=true; $('firmware-role').value='';
    $('firmware-choice-wrap').hidden=true; $('firmware-choice').replaceChildren();
    $('firmware-download').hidden=true; $('firmware-download').disabled=true;
    $('firmware-catalog-status').textContent='Investigate the target to find matching firmware.';
    $('firmware-base-status').textContent='';
    ui.dispatchEvent(new CustomEvent('lora-ota-investigation-clear'));
  }
  for (const name of editable) $(name).addEventListener('change', invalidate);
  function updateContactButtons() {
    for(const button of $('contact').querySelectorAll('button')) {
      button.disabled=$('contact-search').disabled;
      button.setAttribute('aria-pressed', String(button.dataset.contactKey===contactKey));
    }
  }
  function renderContacts() {
    const list=$('contact'), current=contactKey, matches=LoraOtaContacts.choices(contacts,$('contact-search').value);
    list.replaceChildren();
    list.hidden=!matches.length;
    $('contact-status').textContent=!link || $('source').value!=='companion' ? 'Connect a Companion to load contacts.' :
      matches.length ? matches.length+' '+(matches.length===1?'device':'devices')+' found. Select a device.' : 'No matching contacts.';
    for(const contact of matches) {
      const button=document.createElement('button');
      button.type='button';button.dataset.contactKey=contact.publicKey.toLowerCase();button.textContent=LoraOtaContacts.label(contact);
      button.addEventListener('click',()=>{
        if($('contact-search').disabled)return;
        if(contactKey!==button.dataset.contactKey)clearInvestigation();
        contactKey=button.dataset.contactKey;$('key').value=contactKey;invalidate();updateContactButtons();
      });
      list.appendChild(button);
    }
    if(current && !matches.some(c=>c.publicKey.toLowerCase()===current)) { contactKey='';$('key').value='';clearInvestigation();invalidate(); }
    updateContactButtons();
  }
  $('contact-search').addEventListener('input',renderContacts);
  $('key').addEventListener('input',()=>{if(contactKey!==$('key').value.toLowerCase()){contactKey='';updateContactButtons();}clearInvestigation();invalidate();});
  $('password').addEventListener('input',clearInvestigation);
  $('file').addEventListener('change',()=>{
    catalogFile=null;
    if($('file').files[0])$('firmware-catalog-status').textContent='Using your selected firmware file; check it against the target before transferring.'+
      (catalogDest && !catalogInstallSupported ? ' The current target has not confirmed verified installation; this updater cannot install to it.' : '');
  });
  $('base').addEventListener('change',()=>{
    $('firmware-base-status').textContent=$('base').files[0] ? 'Using your selected installed firmware file.' :
      catalogBaseFile ? 'Using the downloaded installed firmware matching the reported target hash.' : '';
  });
  $('source').addEventListener('change',()=>{clearInvestigation();if(!link)$('acl').checked=$('source').value==='repeater';});
  $('acl').checked=false;
  function selectTarget() {
    const key=$('key').value.trim();
    if(!/^[0-9a-f]{64}$/i.test(key))throw Error('Choose a contact or enter the complete target public key.');
    if($('source').value==='companion'){
      if(!targetLocked)link.selectTarget(key);
      else if(link.knownRepeater!==key.toLowerCase())throw Error('Disconnect before choosing another target.');
    }
    return key;
  }
  async function compressor(raw) {
    const stream = new Blob([raw]).stream().pipeThrough(new CompressionStream('deflate-raw'));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }
  function supportedCompression() {
    try { new CompressionStream('deflate-raw'); return compressor; } catch (_) { return null; }
  }
  async function close() {
    if (session) session.stop();
    if (preparing) preparing.abort();
    if (activeTask) { log('Waiting for the current radio request to finish…'); await activeTask.catch(() => {}); }
    if (link) await link.close();
    link = session = chainPrepared = chainConfig = null;contacts=[];targetLocked=false;contactKey='';
    $('key').value='';$('password').value='';$('contact-search').value='';clearInvestigation();renderContacts();busy(false); $('connect').disabled = false; $('inspect').disabled = true; $('find-route').disabled = true;
    $('chain-plan').disabled = true; $('chain-run').disabled = true; $('chain-resume').disabled = true;
    $('transfer').disabled = true; $('resume').disabled = true; $('install').disabled = true;
  }
  async function connect() {
    $('connect').disabled = true; $('connect').textContent = 'Connecting…'; busy(true);
    $('contact-status').textContent = 'Choose a USB device…';
    try {
      if (!navigator.serial) throw Error('Web Serial is unavailable. Use a supported browser on a secure page.');
      const companion=$('source').value==='companion', key = $('key').value.trim();
      if (!companion && !/^[0-9a-f]{64}$/i.test(key)) throw Error('Enter the target’s complete public key.');
      const relays = $('relays').value.trim() ? $('relays').value.trim().split(/[\s,]+/) : [];
      if (companion && relays.length) throw Error('Companion updates currently require a zero-hop connection.');
      if (relays.length && !$('acl').checked) throw Error('Routed updates require existing ACL access on every relay and the target.');
      if (relays.some(r => !/^[0-9a-f]{64}$/i.test(r)) || relays.length > 8) throw Error('Enter up to eight full relay public keys.');
      const port = await navigator.serial.requestPort();
      $('contact-status').textContent = 'Connecting to radio…';
      if(companion)$('acl').checked=false;
      const direct = companion
        ? new LoraOtaCompanion.CompanionTransport(port, {remoteTimeout:75000, onPreserved:refreshPreservedMessages})
        : new LoraOtaSerial.RepeaterTransport(port, {knownRepeater:key, aclOnly:$('acl').checked, remoteTimeout:75000});
      link = relays.length ? new LoraOtaRoute.RoutedRepeaterTransport(direct, key, relays) : direct;
      await link.open();
      if(companion){
        $('contact-status').textContent = 'Loading contacts…';
        contacts=await link.contacts();renderContacts();
        log('Loaded '+LoraOtaContacts.choices(contacts).length+' eligible contacts.');
        if(link.otaUnavailableReason)log(link.otaUnavailableReason);
      } else $('contact-status').textContent = 'Connected. Enter the target public key.';
      busy(false); $('connect').disabled = true; $('inspect').disabled = false;
      $('chain-plan').disabled = false;
      $('find-route').disabled = !link.aclOnly || !link.supportsRoutedPath || !!link.relays;
      log('Connected to source ' + link.sourceKey + '.');
    } catch (error) {
      fail(error);
      if (link) { try { await link.close(); } catch (_) {} }
      link = session = null; $('connect').disabled = false; $('inspect').disabled = true;
      $('contact-status').textContent = error?.name === 'NotFoundError'
        ? 'USB selection canceled. Choose a USB device to try again.'
        : 'Connection failed: ' + (error?.message || error) + '. Try again.';
    } finally { $('connect').textContent = 'Connect USB source'; busy(false); }
  }
  async function loadCatalogCandidate(candidate, generation) {
    catalogFile=null; invalidate();
    $('firmware-catalog-status').textContent='Downloading and checking firmware…';
    const result=await LoraOtaCatalog.load(candidate,catalogDest);
    if(generation!==catalogGeneration || !link)throw Error('Target changed while firmware was downloading');
    if(!(result.bytes instanceof Uint8Array) || !result.bytes.length || !/\.bin$/i.test(result.name))throw Error('The downloaded firmware is invalid');
    catalogFile=new File([result.bytes],result.name,{type:'application/octet-stream'});
    $('file').value=''; invalidate();
    const deltaHelp=catalogDest.nrf && !catalogDest.external && !catalogDest.sd
      ? ($('base').files[0] || catalogBaseFile ? ' An installed image is selected for the required in-place differential.'
        : ' This target needs an in-place differential; also select the exact installed firmware below.') : '';
    const installHelp=catalogInstallSupported ? '' : ' The current target has not confirmed verified installation; this updater cannot install to it.';
    $('firmware-catalog-status').textContent='Downloaded '+result.name+'; hardware identity verified. Check firmware and target before transferring.'+deltaHelp+installHelp;
  }
  async function discoverCatalog(role) {
    const generation=catalogGeneration;
    if(!catalogDest || !role)return;
    if(catalogInstallSupported && catalogDest.nrf && !catalogDest.sd && (catalogDest.codecs & 4) && !$('base').files[0] && typeof LoraOtaCatalog.findInstalled==='function') {
      const baseHelp=catalogDest.external ? 'You can select it manually to make a differential.' : 'Select it manually for the required differential.';
      $('firmware-base-status').textContent='Looking for the installed firmware…';
      try {
        const base=await LoraOtaCatalog.findInstalled(catalogDest,role,catalogVersion);
        if(generation!==catalogGeneration || !link)return;
        if(base && base.bytes instanceof Uint8Array && /\.bin$/i.test(base.name)) {
          catalogBaseFile=new File([base.bytes],base.name,{type:'application/octet-stream'});
          $('firmware-base-status').textContent='Found installed firmware matching the reported target hash: '+base.name+'.';
        } else $('firmware-base-status').textContent='Exact installed firmware not found. '+baseHelp;
      } catch(error) {
        if(generation!==catalogGeneration || !link)return;
        $('firmware-base-status').textContent='Could not find the exact installed firmware: '+(error?.message||error)+'. '+baseHelp;
      }
    }
    $('firmware-catalog-status').textContent='Finding matching firmware…';
    const candidates=await LoraOtaCatalog.discover(catalogDest,role);
    if(generation!==catalogGeneration || !link)return;
    catalogCandidates=candidates;
    const select=$('firmware-choice'); select.replaceChildren();
    const prompt=document.createElement('option');prompt.value='';prompt.textContent='Choose firmware';select.appendChild(prompt);
    for(let i=0;i<candidates.length;i++){
      const option=document.createElement('option');option.value=String(i);option.textContent=candidates[i].name;select.appendChild(option);
    }
    $('firmware-choice-wrap').hidden=!candidates.length;
    $('firmware-download').hidden=!candidates.length;
    if(!candidates.length){$('firmware-catalog-status').textContent=(catalogInstallSupported ? '' : 'The target has not confirmed verified installation; this updater cannot install to it. ')+'No matching newer firmware found. You can choose a file manually.';return;}
    if(!catalogInstallSupported){
      $('firmware-catalog-status').textContent='The target has not confirmed verified installation; this updater cannot install to it. Matching downloads are listed for reference.';
      return;
    }
    const recommended=typeof LoraOtaCatalog.recommendVerified==='function'
      ? await LoraOtaCatalog.recommendVerified(candidates,catalogDest) : null;
    if(generation!==catalogGeneration || !link)return;
    if(recommended && !$('file').files[0]){
      const index=candidates.indexOf(recommended);
      if(index<0)throw Error('Invalid firmware recommendation');
      select.value=String(index);
      await loadCatalogCandidate(recommended,generation);
    }else{
      $('firmware-catalog-status').textContent=$('file').files[0] ? 'Using your selected firmware file. Matching downloads are also listed.' : 'Choose a matching firmware version to download.';
    }
  }
  async function investigate(allowStandard = false) {
    const standard = $('source').value==='companion' && link && link.otaSupported===false;
    if(standard && !allowStandard && /^[0-9a-f]{64}$/i.test($('key').value.trim())) {
      clearInvestigation(); invalidate(); $('legacy-warning').hidden=false;
      return;
    }
    clearInvestigation(); invalidate(); busy(true); $('inspect').disabled = true;
    const output = $('investigation'), replies = {};
    let target = '', complete = false, verified = false;
    output.hidden = false;
    output.textContent = 'Investigating target…\n';
    const add = text => { output.textContent += text + '\n'; };
    const checks = [
      ['OTA status', 'ota status'], ['OTA storage and image', 'ota self'],
      ['Firmware version', 'ver'], ['Bootloader version', 'get bootloader.ver'],
      ['Bootloader capabilities', 'ota bootloader status'], ['OTA settings', 'ota config'],
      ['Trusted OTA keys', 'ota key list'], ['Temporary radio', 'get tempradio'],
      ['Radio settings', 'get radio'], ['Radio power saving', 'get radio.rxps'],
      ['Install verification', 'ota installhash']
    ];
    let next = 0, allReplies = true;
    try {
      if (!link) throw Error('Connect a source radio first.');
      target = selectTarget().toLowerCase();
      const password = $('acl').checked ? '' : $('password').value;
      if(standard) {
        add('Companion warning: this radio can investigate, but needs guarded OTA support before it can send an update.');
        await link.enableStandardInvestigation();
        await link.investigateVerifyPeer(target, password);
      } else await link.verifyPeer(target, password);
      verified = !standard || link.investigationVerified===true;
      add(verified ? 'Identity: full public key verified (' + target.slice(0, 12).toUpperCase() + ')' :
        'Identity not fully verified: this target does not support the full-key check. Results are diagnostics only; firmware selection and updates are disabled.');
      if(standard) {
        add('Account access: '+(link.investigationPermission||'unknown'));
        add('Disconnect the Companion to choose another target.');
      }
      for (; next < checks.length; next++) {
        const [label, command] = checks[next];
        let reply;
        try { reply = String(await (standard ? link.investigateCommand(target, password, command) : link.remoteCommand(target, password, command))).trim(); }
        catch(error) {
          if(standard && !link.failure && error?.message==='Remote command timed out; result is unknown') {
            allReplies=false; replies[command]=''; add(label+': Timed out; not checked'); continue;
          }
          throw error;
        }
        replies[command] = reply;
        const normalized = reply.replace(/^>\s*/, '');
        if (command === 'ota installhash') {
          catalogInstallSupported=normalized === 'ERR usage: ota installhash <64-hex-container-sha256>';
          add(label + ': ' + (catalogInstallSupported
            ? 'Supported' : 'Not supported or unknown (' + (normalized || 'empty response') + ')'));
          if(!catalogInstallSupported)add('This updater cannot install to the current target without confirmed verified installation support.');
        } else {
          const unknown = !normalized || /^(?:err(?:or)?\b|unknown\b|unsupported\b)/i.test(normalized);
          add(label + ': ' + (unknown ? 'Unknown or unsupported (' + (normalized || 'empty response') + ')' : normalized));
        }
      }
      if (/\bqspi\b/i.test((replies['ota status'] || '') + ' ' + (replies['ota self'] || ''))) {
        try {
          const qspi = String(await (standard ? link.investigateCommand(target, password, 'ota qspi') : link.remoteCommand(target, password, 'ota qspi'))).trim();
          replies['ota qspi'] = qspi;
          const normalized = qspi.replace(/^>\s*/, '');
          add('External flash: ' + (!normalized || /^(?:err(?:or)?\b|unknown\b|unsupported\b)/i.test(normalized)
            ? 'Unknown or unsupported (' + (normalized || 'empty response') + ')' : normalized));
        } catch(error) {
          if(!standard || link.failure || error?.message!=='Remote command timed out; result is unknown')throw error;
          allReplies=false; replies['ota qspi']=''; add('External flash: Timed out; not checked');
        }
      }
      complete = allReplies;
      if(verified)try {
        catalogDest=LoraOtaChecks.destination(replies['ota status'],replies['ota self'],replies.ver);catalogVersion=replies.ver;
        if(catalogDest.nrf && !catalogDest.external && !catalogDest.sd)
          add('Update method: this target needs an in-place differential.');
        const contact=contacts.find(c=>String(c.publicKey).toLowerCase()===target);
        const role=({2:'repeater',3:'room',4:'sensor'})[contact?.type];
        if(!role){
          $('firmware-role-wrap').hidden=false;
          $('firmware-catalog-status').textContent=(catalogInstallSupported ? '' : 'The target has not confirmed verified installation; this updater cannot install to it. ')+'Choose the target type to find matching firmware.';
        }else if(!globalThis.LoraOtaCatalog){
          $('firmware-catalog-status').textContent='Firmware catalog is unavailable. Choose a file manually.';
        }else await discoverCatalog(role);
      } catch(error) {
        $('firmware-catalog-status').textContent='Could not select firmware automatically: '+(error?.message || error)+'. You can choose a file manually.';
      } else $('firmware-catalog-status').textContent='Target identity is not fully verified; firmware selection and updates are disabled.';
    } catch (error) {
      add((verified ? 'Check interrupted: ' : 'Identity not verified: ') + (error?.message || error));
      if(standard && link?.investigationEnabled)add('Disconnect the Companion before choosing another target or retrying the login.');
      for (; next < checks.length; next++) add(checks[next][0] + ': Not checked');
      fail(error);
    } finally {
      if(standard) {
        $('legacy-source-block').hidden=false;
        const notice=' This Companion cannot send OTA updates until it has guarded OTA support.';
        if(!$('firmware-catalog-status').textContent.includes(notice.trim()))$('firmware-catalog-status').textContent+=notice;
        refreshPreservedMessages();
      }
      add(verified ? 'Firmware compatibility: run the package check before transferring.' : 'Firmware compatibility: not checked because the target identity was not fully verified.');
      ui.dispatchEvent(new CustomEvent('lora-ota-investigated', {detail:{target,replies,complete,verified}}));
      busy(false); $('inspect').disabled = !link;
    }
  }
  async function findRoute() {
    invalidate(); busy(true); $('find-route').disabled = true; $('inspect').disabled = true;
    try {
      if (!link || link.relays || !link.aclOnly || !link.supportsRoutedPath) throw Error('Connect in ACL mode without a manual route first.');
      const target = $('key').value.trim().toLowerCase();
      const candidates = $('candidates').value.trim() ? $('candidates').value.trim().split(/[\s,]+/).map(k => k.toLowerCase()) : [];
      const allowlist = [link.sourceKey, target, ...candidates];
      if (allowlist.some(k => !/^[0-9a-f]{64}$/.test(k)) || new Set(allowlist).size !== allowlist.length) throw Error('Enter unique full keys for candidate repeaters.');
      const minSnrDb = Number($('snr').value), hours = Number($('age').value);
      if (!Number.isFinite(minSnrDb) || !Number.isFinite(hours) || hours <= 0 || hours > 168) throw Error('Enter a valid SNR floor and observation age.');
      log('Checking cached neighbor observations and ACL access…');
      const observed = await LoraOtaCollect.collectAdminNeighbors({link,allowlist,minSnrDb,maxAgeSeconds:Math.floor(hours*3600),maxRelays:8,
        progress:event => log('Checked neighbor table ' + event.queries + '.')});
      const ranked = LoraOtaDiscovery.rankRoutes({source:link.sourceKey,target,allowlist,observations:observed.observations,
        minSnrDb,maxAgeSeconds:Math.floor(hours*3600),maxRelays:8});
      if (!ranked.routes.length) throw Error('No route has sufficient recent measurements in both directions. Enter a manual admin route if needed.');
      const chosen = ranked.routes[0];
      if (chosen.relays.length) {
        const routed = new LoraOtaRoute.RoutedRepeaterTransport(link,target,chosen.relays);
        await routed.open(); link = routed;
      }
      $('relays').value = chosen.relays.join('\n');
      log('Selected admin route: ' + chosen.hops + ' relay(s), weakest measured SNR ' + chosen.bottleneckSnrDb + ' dB. OTA data still floods.');
      if (observed.truncated) log('The search reached its query limit; other paths may exist.');
      log('Measurements are cached, may be stale, and do not identify the radio profile.');
    } catch (error) { fail(error); }
    finally { busy(false); $('inspect').disabled = !link; $('find-route').disabled = !link || !!link.relays || !link.aclOnly || !link.supportsRoutedPath; }
  }
  async function inspect() {
    invalidate(); busy(true); $('inspect').disabled = true;
    try {
      if (!link) throw Error('Connect a source radio first.');
      const targetKey=selectTarget(), password=$('acl').checked ? '' : $('password').value;
      const standard=$('source').value==='companion' && link.otaSupported===false;
      if(standard && (!link.investigationAuthenticated || link.investigationVerified!==true))throw Error('Run Investigate and verify the target’s full public key before checking firmware.');
      const verify=()=>standard ? link.investigateVerifyPeer(targetKey,password) : link.verifyPeer(targetKey,password);
      const remote=command=>standard ? link.investigateCommand(targetKey,password,command) : link.remoteCommand(targetKey,password,command);
      const file = catalogFile || $('file').files[0]; if (!file) throw Error('Choose a firmware .bin or .mota file.');
      const isFirmware = /\.(?:bin|zip)$/i.test(file.name);
      const baseFile = $('base').files[0] || (isFirmware ? catalogBaseFile : null);
      const input = isFirmware ? await firmwareBytes(file, baseFile ? 4 * 1024 * 1024 : undefined) : await bytes(file);
      const signerText = $('signer').value.trim();
      let expectedImage = null, inplacePlan = null;
      if (signerText && !/^[0-9a-f]{64}$/i.test(signerText)) throw Error('The trusted signing key must be 64 hexadecimal characters.');
      expectedSigner = signerText ? Uint8Array.from(signerText.match(/../g), h => parseInt(h, 16)) : null;
      if (isFirmware) {
        if (expectedSigner) throw Error('A package created here is unsigned. Select a signed .mota file instead.');
        if (baseFile) {
          await verify();
          const status = await remote('ota status');
          const self = await remote('ota self');
          const bootloader = await remote('get bootloader.ver');
          const version = await remote('ver');
          const destination = LoraOtaChecks.destination(status, self, version);
          const base = await firmwareBytes(baseFile, 4 * 1024 * 1024);
          const current = await MeshCoreMotaPackage.parseEndF(base);
          if (current.targetId !== destination.targetId || current.hwId !== destination.hwId ||
              current.fwVersion !== destination.version || LoraOtaChecks.hex(current.bodyHash) !== destination.baseHash)
            throw Error('The installed firmware image does not exactly match the target.');
          let deltaOptions = {mode:'sequential'};
          if (destination.nrf) {
            const layout = MeshCoreLoraDelta.nrfLayout(current.image);
            if (destination.external) {
              const diagnostics = await remote('ota qspi');
              const capacity = LoraOtaChecks.qspiCapacity(status, self, diagnostics, destination);
              deltaOptions = {mode:'in-place', runtimeStorage:'qspi', runtimeQspiApply:true, runtimeStorageBytes:capacity};
              inplacePlan = {storage:'qspi', app:layout.app, layoutEnd:layout.end};
            } else {
              const stage = LoraOtaChecks.internalStage(status, self, destination);
              deltaOptions = {mode:'in-place', runtimeStorage:'internal', runtimeStageCeiling:stage};
              inplacePlan = {storage:'internal', app:layout.app, layoutCeiling:layout.ceiling, layoutFlags:layout.flags};
            }
          } else LoraOtaChecks.confirmSequentialTarget(status, self, bootloader);
          preparing = new AbortController();
          log('Preparing the differential package in this browser…');
          const delta = await MeshCoreLoraDelta.buildInWorker(base, input, {...deltaOptions, signal:preparing.signal});
          if (inplacePlan) inplacePlan.memorySize = delta.memorySize;
          preparing = null;
          const fullSize = input.length + 210 + Math.ceil(input.length / 1024) * 4;
          if (delta.blob.length >= fullSize && (!destination.nrf || destination.external)) {
            info = await MeshCoreMotaPackage.parseMota(await MeshCoreMotaPackage.buildFullMota(input));
            log('The full package is smaller, so it will be used.');
          } else {
            info = await MeshCoreMotaPackage.parseMota(delta.blob); expectedImage = input;
            log('Differential payload: ' + delta.payloadBytes + ' bytes.');
          }
        } else info = await MeshCoreMotaPackage.parseMota(await MeshCoreMotaPackage.buildFullMota(input));
      } else {
        if (baseFile) throw Error('An installed firmware image is only used when creating a differential from a .bin.');
        info = await MeshCoreMotaPackage.parseMota(input, expectedSigner ? {expectedSigner} : {});
      }
      const targetImage = $('image').files[0];
      if (!info.isFull && !targetImage && !expectedImage) throw Error('Select the matching new .bin image for the differential package.');
      if (!expectedImage && targetImage) expectedImage = await firmwareBytes(targetImage);
      if(standard) {
        await verify();
        const status=await remote('ota status'), self=await remote('ota self'), version=await remote('ver');
        const dest=LoraOtaChecks.destination(status,self,version);
        LoraOtaChecks.validate(info,dest,!!expectedSigner && info.signed);
        LoraOtaChecks.validateInternalPlan(inplacePlan,info,status,self,dest,
          inplacePlan?.storage==='qspi' ? await remote('ota qspi') : null);
        if(!info.isFull && info.codecId===1)
          LoraOtaChecks.confirmSequentialTarget(status,self,await remote('get bootloader.ver'));
        if(info.signed) {
          const trusted=await remote('ota key list');
          if(!trusted.toLowerCase().split(/\s+/).includes(LoraOtaChecks.hex(info.signer.subarray(0,8))))
            throw Error('The target does not list the supplied trusted signer; add it on the target first');
        }
        await LoraOtaSession.expectedBody(info,expectedImage);
        const install=String(await remote('ota installhash')).trim().replace(/^>\s*/,'');
        const supported=install==='ERR usage: ota installhash <64-hex-container-sha256>';
        const warning='This Companion cannot send OTA updates until it has guarded OTA support.';
        const result='Package matches the reported target hardware, version and image checks. '+
          (supported?'Target verified installation is supported. ':'Target verified installation is not confirmed. ')+
          'Source radio, queue and final transfer preflight were not checked. '+warning;
        $('firmware-catalog-status').textContent=result; log(result); session=null;
        return;
      }
      const Session = link.relays?.length ? LoraOtaRoutedSession.RoutedSession : LoraOtaSession.DirectSession;
      session = new Session(link, $('key').value.trim(), $('acl').checked ? '' : $('password').value, info, {
        signerVerified:!!expectedSigner && info.signed, image:expectedImage, inplacePlan,
        compress:supportedCompression(), progress:log, journal, readJournal
      });
      const dest = await session.inspect({allowExisting:true});
      log('Verified ' + dest.hwId + '; ' + (info.isFull ? 'full' : 'differential') + ' package ' + LoraOtaChecks.hex(info.merkleRoot).toUpperCase() + '.');
      log(info.signed ? (expectedSigner ? 'Package signature matches your trusted key.' : 'Package signature is valid; no trusted signing key was supplied.') : 'This package is unsigned.');
      $('transfer').disabled = dest.download !== 'idle';
      $('resume').disabled = dest.download !== 'active';
      $('install').disabled = dest.download !== 'ready';
      if (dest.download === 'active') log('A matching transfer is in progress. Select Resume.');
      if (dest.download === 'ready') log('The matching package is ready to install.');
      if (dest.download === 'installed') log('The previously requested install is verified.');
    } catch (error) {
      invalidate(); fail(error);
      if(link && $('source').value==='companion' && link.otaSupported===false)
        $('firmware-catalog-status').textContent='Package check could not be completed: '+(error?.message||error)+'. This Companion cannot send OTA updates until it has guarded OTA support.';
    }
    finally { preparing = null; busy(false); $('inspect').disabled = !link; }
  }
  async function transfer(resume) {
    $('transfer').disabled = true; $('resume').disabled = true; $('inspect').disabled = true; busy(true);
    try { await session.start({resume}); log('Transfer complete. Check the target and select Install.'); $('install').disabled = false; }
    catch (error) { fail(error); }
    finally { busy(false); $('inspect').disabled = !link; }
  }
  function run(task) { activeTask = task.finally(() => { activeTask = null; }); return activeTask; }
  $('firmware-role').addEventListener('change',()=>run((async()=>{
    catalogGeneration++;catalogFile=null;catalogBaseFile=null;catalogCandidates=[];invalidate();
    $('firmware-base-status').textContent='';
    $('firmware-choice-wrap').hidden=true;$('firmware-download').hidden=true;
    if(!$('firmware-role').value){$('firmware-catalog-status').textContent='Choose the target type to find matching firmware.';return;}
    busy(true);$('inspect').disabled=true;
    try{await discoverCatalog($('firmware-role').value);}
    catch(error){$('firmware-catalog-status').textContent='Could not select firmware automatically: '+(error?.message||error)+'. You can choose a file manually.';}
    finally{busy(false);$('inspect').disabled=!link;}
  })()));
  $('firmware-choice').addEventListener('change',()=>{
    catalogFile=null;invalidate();
    $('firmware-download').disabled=!$('firmware-choice').value;
    if($('firmware-choice').value)$('firmware-catalog-status').textContent='Download the selected firmware, then check it against the target.'+
      (catalogInstallSupported ? '' : ' The current target has not confirmed verified installation; this updater cannot install to it.');
  });
  $('firmware-download').addEventListener('click',()=>run((async()=>{
    const index=Number($('firmware-choice').value),generation=catalogGeneration;
    if(!$('firmware-choice').value || !Number.isInteger(index) || !catalogCandidates[index])return;
    busy(true);$('inspect').disabled=true;
    try{await loadCatalogCandidate(catalogCandidates[index],generation);}
    catch(error){$('firmware-catalog-status').textContent='Firmware download failed: '+(error?.message||error)+'. You can choose a file manually.';}
    finally{busy(false);$('inspect').disabled=!link;}
  })()));
  $('connect').addEventListener('click', () => run(connect()));
  $('investigate').addEventListener('click', () => run(investigate()));
  $('legacy-investigate').addEventListener('click', () => run(investigate(true)));
  $('inspect').addEventListener('click', () => run(inspect()));
  $('find-route').addEventListener('click', () => run(findRoute()));
  $('transfer').addEventListener('click', () => run(transfer(false)));
  $('resume').addEventListener('click', () => run(transfer(true)));
  async function forwarding(action) {
    busy(true);
    try {
      const selection = $('repeat-node').value;
      const key = selection === 'source' ? link?.sourceKey : selection === 'target' ? $('key').value.trim() : $('repeat-key').value.trim();
      const password = key?.toLowerCase() === $('key').value.trim().toLowerCase() && !$('acl').checked ? $('password').value : '';
      const relays = $('relays').value.trim() ? $('relays').value.trim().split(/[\s,]+/) : [];
      const setting = $('repeat-kind').value;
      const state = await LoraOtaRepeat.control(link, key || '', password, relays, action, setting);
      if (action !== 'read') invalidate();
      log(setting + ' is ' + state + ' on ' + key + '. This setting persists until changed.');
    } catch (error) { fail(error); }
    finally { busy(false); $('inspect').disabled = !link; }
  }
  $('repeat-read').addEventListener('click', () => run(forwarding('read')));
  $('repeat-off').addEventListener('click', () => run(forwarding('off')));
  $('repeat-on').addEventListener('click', () => run(forwarding('on')));
  async function install() {
    $('install').disabled = true; $('inspect').disabled = true; busy(true);
    try { const result = await session.install(); log('Installed and verified: ' + result.hwId + ' ' + result.baseHash.toUpperCase()); }
    catch (error) { fail(error); }
    finally { busy(false); $('inspect').disabled = !link; }
  }
  async function planChain() {
    invalidate(); busy(true); $('inspect').disabled = true;
    try {
      if (!link) throw Error('Connect a source repeater first.');
      const baseFile=$('base').files[0] || catalogBaseFile, goalFile=catalogFile || $('file').files[0], middle=[...$('chain-files').files];
      if (!baseFile || !goalFile || !/\.(?:bin|zip)$/i.test(baseFile.name) || !/\.(?:bin|zip)$/i.test(goalFile.name))
        throw Error('Choose the exact installed image and final firmware BIN or ZIP.');
      if (middle.length > 6 || middle.some(f=>!/\.(?:bin|zip)$/i.test(f.name))) throw Error('Choose up to six intermediate BIN or ZIP images.');
      if (middle.length && !$('chain-attest').checked)
        throw Error('Confirm that every selected intermediate has been tested on this exact hardware and retains working LoRa OTA.');
      const objective=$('chain-objective').value, preamble=Number($('chain-preamble').value), minutes=Number($('chain-minutes').value);
      if (!['packets','airtime'].includes(objective) || !Number.isInteger(preamble) || preamble<1 || preamble>65535 ||
          !Number.isInteger(minutes) || minutes<1 || minutes>10080) throw Error('Choose a valid objective, preamble and temporary-window duration.');
      const target=selectTarget().toLowerCase(), password=$('acl').checked?'':$('password').value;
      const live=await LoraOtaChainUi.readLive(link,target,password);
      if(!live.nrf || live.external)throw Error('Multi-version planning is for nRF52 internal staging; use the single-update controls for ESP32 or external-storage targets.');
      const bootVersion=await link.remoteCommand(target,password,'get bootloader.ver');
      const bootStatus=await link.remoteCommand(target,password,'ota bootloader status');
      const bootProfile=LoraOtaSim.identify(bootVersion,bootStatus);
      const installProbe=String(await link.remoteCommand(target,password,'ota installhash')).trim().replace(/^>\s*/, '');
      if(installProbe!=='ERR usage: ota installhash <64-hex-container-sha256>')
        throw Error('The target does not support verified package installation');
      const sourceRadio=LoraOtaSession.temp(await link.localCommand('get tempradio'));
      const old=await firmwareBytes(baseFile,4*1024*1024), first=await MeshCoreMotaPackage.parseEndF(old);
      const saved=readChainJournal(), pending=!!saved&&saved.phase!=='complete', savedMeta=readChainMeta();
      let current,caps,targetPreamble;
      if (pending) {
        if (!savedMeta || savedMeta.source!==link.sourceKey || savedMeta.target!==target || savedMeta.objective!==objective ||
            savedMeta.preamble!==preamble || savedMeta.minutes!==minutes || !LoraOtaChainUi.sameRadio(savedMeta.sourceRadio,sourceRadio) ||
            savedMeta.baseImageHash!==LoraOtaChecks.hex(first.imageHash) || savedMeta.current?.bodyHash!==LoraOtaChecks.hex(first.bodyHash) ||
            savedMeta.bootProfile!==bootProfile)
          throw Error('Select the same source, files and planning settings used by the unfinished chain.');
        current=savedMeta.current;caps=savedMeta.caps;targetPreamble=savedMeta.targetPreamble;
        if (!Number.isInteger(targetPreamble) || targetPreamble<1 || targetPreamble>65535)
          throw Error('The saved chain lacks a confirmed target preamble.');
        if (live.targetId!==current.targetId || live.hwId!==current.hwId || live.nrf!==caps.nrf)
          throw Error('The live target no longer matches the saved chain.');
      } else {
        current={node:'base',targetId:live.targetId,hwId:live.hwId,version:live.version,bodyHash:live.bodyHash};
        caps=live;
        const targetRadio=await link.remoteCommand(target,password,'get tempradio');
        if(!LoraOtaChainUi.sameRadio(LoraOtaSession.temp(targetRadio),sourceRadio))
          throw Error('Source and target temporary radio settings do not match.');
        targetPreamble=LoraOtaChainUi.preamble(targetRadio);
      }
      const images=[{id:'base',bytes:old,capabilities:caps}], all=[...middle,goalFile];
      for (let i=0;i<all.length;i++) {
        const image=await firmwareBytes(all[i],4*1024*1024);
        const capabilities=LoraOtaChainUi.evidence(image,caps,old,i<all.length-1);
        images.push({id:i===all.length-1?'goal':'intermediate-'+(i+1),bytes:image,capabilities});
      }
      const radio={bandwidth:sourceRadio[1],sf:sourceRadio[2],cr:sourceRadio[3],preamble};
      preparing=new AbortController();
      log('Comparing update paths in this browser…');
      chainPrepared=await LoraOtaCandidates.prepare({images,current,goal:'goal',objective,radio,kinds:['delta'],
        relays:link.relays?.length||0,restartSeconds:60,signal:preparing.signal,progress:log},
        {compressBlock:supportedCompression()});
      preparing=null;
      log('Simulating each differential with the matching bootloader…');
      await LoraOtaSim.simulate(bootProfile,chainPrepared,caps.runtimeStageCeiling);
      chainConfig={source:link.sourceKey,target,current,caps,baseImageHash:LoraOtaChecks.hex(first.imageHash),objective,preamble,minutes,sourceRadio,targetPreamble,bootProfile};
      const route=chainPrepared.route;
      log('Selected path: '+route.steps.map(s=>s.from+' → '+s.to+' ('+s.kind+')').join('; ')+'.');
      log('Estimate: '+route.totals.rfTransmissions+' radio transmissions'+
        (route.totals.rfAirtimeSeconds===null?'':', '+route.totals.rfAirtimeSeconds.toFixed(1)+' seconds of radio airtime')+
        '; '+route.totals.installs+' installation(s)'+
        (Number.isFinite(route.totals.estimatedSeconds)?', '+route.totals.estimatedSeconds.toFixed(1)+' seconds including an assumed 60 seconds per reboot':'')+
        '. Polls, retries, duty pacing and congestion are excluded.');
      for(const warning of new Set([...chainPrepared.warnings,...(route.assumptions||[])]))log(warning);
      if(!chainPrepared.complete)log('Some candidates could not be prepared; the selected path is only the best among those checked.');
      log('Every selected differential passed the bootloader model. The model does not replace testing the intermediate firmware on this hardware.');
    }catch(error){chainPrepared=chainConfig=null;fail(error);}
    finally{preparing=null;busy(false);$('inspect').disabled=!link;}
  }
  async function runChain(resume=false) {
    if (!chainPrepared || !chainConfig || !link) return fail(Error('Compare and simulate the update path first.'));
    busy(true); $('inspect').disabled=true;
    const signalController = new AbortController(); preparing=signalController;
    try {
      const {target,bootProfile,caps,sourceRadio,minutes,targetPreamble}=chainConfig;
      const password=$('acl').checked?'':$('password').value;
      const readLive=async()=>{
        const current=await LoraOtaChainUi.readLive(link,target,password);
        const version=await link.remoteCommand(target,password,'get bootloader.ver');
        const status=await link.remoteCommand(target,password,'ota bootloader status');
        if(LoraOtaSim.identify(version,status)!==bootProfile) throw Error('The target bootloader changed during the update');
        return current;
      };
      await readLive();
      await LoraOtaSim.simulate(bootProfile,chainPrepared,caps.runtimeStageCeiling);
      if(!resume) localStorage.setItem('meshcore-browser-lora-ota-chain-meta',JSON.stringify(chainConfig));
      const Session=link.relays?.length?LoraOtaRoutedSession.RoutedSession:LoraOtaSession.DirectSession;
      const runner=new LoraOtaChain.ChainRunner({
        prepared:chainPrepared, link, targetKey:target, readLive,
        readStepJournal,writeStepJournal,readChainJournal,writeChainJournal,
        createSession:async step=>new Session(link,target,password,step.package.info,{
          image:step.targetImage,inplacePlan:step.edge.inplacePlan,compress:supportedCompression(),
          progress:log,journal:writeStepJournal,readJournal:readStepJournal
        }),
        rearm:async ({signal})=>LoraOtaChainUi.rearm(link,target,password,sourceRadio,minutes,{signal,preamble:targetPreamble}),
        confirmRearm:async()=>LoraOtaChainUi.activeRadio(link,target,password,sourceRadio,targetPreamble)
      });
      const result=await runner.run({resume,signal:signalController.signal});
      log('Update sequence complete; verified '+result.steps+' installation(s).');
    } catch(error) { fail(error); }
    finally { preparing=null;busy(false);$('inspect').disabled=!link; }
  }
  $('install').addEventListener('click', () => run(install()));
  $('chain-plan').addEventListener('click', () => run(planChain()));
  $('chain-run').addEventListener('click', () => run(runChain(false)));
  $('chain-resume').addEventListener('click', () => run(runChain(true)));
  $('disconnect').addEventListener('click', () => close().then(() => log('Disconnected.')).catch(fail));
})();
