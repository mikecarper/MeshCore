/* Direct browser LoRa updater. All package and radio traffic stays local. */
(function () {
  'use strict';
  const ui = document.querySelector('[data-lora-ota]');
  if (!ui) return;
  const $ = name => ui.querySelector('[data-role="' + name + '"]');
  let link, session, info, expectedSigner, activeTask, preparing, chainPrepared, chainConfig;
  let contacts=[], targetLocked=false, contactKey='';
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
    if(link && $('source').value==='companion' && (link.guard || link.authenticated || link.serving))targetLocked=true;
    for (const name of editable) $(name).disabled = active || (!!link && (name === 'source' || name === 'acl' || name === 'relays' || (name === 'key' && ($('source').value === 'repeater' || targetLocked))));
    $('contact-search').disabled = active || !link || $('source').value !== 'companion' || targetLocked;
    $('contact').disabled = $('contact-search').disabled;
    for (const name of ['repeat-read', 'repeat-off', 'repeat-on']) $(name).disabled = active || !link;
    $('repeat-node').disabled = $('repeat-key').disabled = $('repeat-kind').disabled = active;
    const pending = readChainJournal();
    $('chain-plan').disabled = active || !link;
    $('chain-run').disabled = active || !link || !chainPrepared || !chainConfig || !!pending && pending.phase !== 'complete';
    $('chain-resume').disabled = active || !link || !chainPrepared || !chainConfig || !pending || pending.phase === 'complete';
  }
  function invalidate() { session = info = chainPrepared = chainConfig = null; $('transfer').disabled = true; $('resume').disabled = true;
    $('install').disabled = true; $('chain-run').disabled = true; $('chain-resume').disabled = true; }
  for (const name of editable) $(name).addEventListener('change', invalidate);
  function renderContacts() {
    const select=$('contact'), current=contactKey, matches=LoraOtaContacts.choices(contacts,$('contact-search').value);
    select.replaceChildren();
    const prompt=document.createElement('option');prompt.value='';prompt.textContent=matches.length?'Choose a contact':'No matching contacts';select.appendChild(prompt);
    for(const contact of matches) {
      const option=document.createElement('option');option.value=contact.publicKey.toLowerCase();option.textContent=LoraOtaContacts.label(contact);select.appendChild(option);
    }
    if(current && matches.some(c=>c.publicKey.toLowerCase()===current))select.value=current;
    else if(current) { contactKey='';$('key').value='';invalidate(); }
  }
  $('contact-search').addEventListener('input',renderContacts);
  $('contact').addEventListener('change',()=>{contactKey=$('contact').value;$('key').value=contactKey;invalidate();});
  $('key').addEventListener('input',()=>{if(contactKey!==$('key').value.toLowerCase()){contactKey='';$('contact').value='';}invalidate();});
  $('source').addEventListener('change',()=>{if(!link)$('acl').checked=$('source').value==='repeater';});
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
    $('key').value='';$('password').value='';$('contact-search').value='';renderContacts();busy(false); $('connect').disabled = false; $('inspect').disabled = true; $('find-route').disabled = true;
    $('chain-plan').disabled = true; $('chain-run').disabled = true; $('chain-resume').disabled = true;
    $('transfer').disabled = true; $('resume').disabled = true; $('install').disabled = true;
  }
  async function connect() {
    $('connect').disabled = true; busy(true);
    try {
      if (!navigator.serial) throw Error('Web Serial is unavailable. Use a supported browser on a secure page.');
      const companion=$('source').value==='companion', key = $('key').value.trim();
      if (!companion && !/^[0-9a-f]{64}$/i.test(key)) throw Error('Enter the target’s complete public key.');
      const relays = $('relays').value.trim() ? $('relays').value.trim().split(/[\s,]+/) : [];
      if (companion && relays.length) throw Error('Companion updates currently require a zero-hop connection.');
      if (relays.length && !$('acl').checked) throw Error('Routed updates require existing ACL access on every relay and the target.');
      if (relays.some(r => !/^[0-9a-f]{64}$/i.test(r)) || relays.length > 8) throw Error('Enter up to eight full relay public keys.');
      const port = await navigator.serial.requestPort();
      if(companion)$('acl').checked=false;
      const direct = companion
        ? new LoraOtaCompanion.CompanionTransport(port, {remoteTimeout:75000})
        : new LoraOtaSerial.RepeaterTransport(port, {knownRepeater:key, aclOnly:$('acl').checked, remoteTimeout:75000});
      link = relays.length ? new LoraOtaRoute.RoutedRepeaterTransport(direct, key, relays) : direct;
      await link.open();
      if(companion){
        contacts=await link.contacts();renderContacts();
        log('Loaded '+LoraOtaContacts.choices(contacts).length+' eligible contacts.');
        if(link.otaUnavailableReason)log(link.otaUnavailableReason);
      }
      busy(false); $('connect').disabled = true; $('inspect').disabled = false;
      $('chain-plan').disabled = false;
      $('find-route').disabled = !link.aclOnly || !link.supportsRoutedPath || !!link.relays;
      log('Connected to source ' + link.sourceKey + '.');
    } catch (error) {
      fail(error);
      if (link) { try { await link.close(); } catch (_) {} }
      link = session = null; $('connect').disabled = false; $('inspect').disabled = true;
    } finally { busy(false); }
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
      selectTarget();
      const file = $('file').files[0]; if (!file) throw Error('Choose a firmware .bin or .mota file.');
      const isFirmware = /\.(?:bin|zip)$/i.test(file.name);
      const input = isFirmware ? await firmwareBytes(file, $('base').files[0] ? 4 * 1024 * 1024 : undefined) : await bytes(file);
      const signerText = $('signer').value.trim();
      let expectedImage = null, inplacePlan = null;
      if (signerText && !/^[0-9a-f]{64}$/i.test(signerText)) throw Error('The trusted signing key must be 64 hexadecimal characters.');
      expectedSigner = signerText ? Uint8Array.from(signerText.match(/../g), h => parseInt(h, 16)) : null;
      if (isFirmware) {
        if (expectedSigner) throw Error('A package created here is unsigned. Select a signed .mota file instead.');
        if ($('base').files[0]) {
          const targetKey = $('key').value.trim(), password = $('acl').checked ? '' : $('password').value;
          await link.verifyPeer(targetKey, password);
          const status = await link.remoteCommand(targetKey, password, 'ota status');
          const self = await link.remoteCommand(targetKey, password, 'ota self');
          const bootloader = await link.remoteCommand(targetKey, password, 'get bootloader.ver');
          const version = await link.remoteCommand(targetKey, password, 'ver');
          const destination = LoraOtaChecks.destination(status, self, version);
          const base = await firmwareBytes($('base').files[0], 4 * 1024 * 1024);
          const current = await MeshCoreMotaPackage.parseEndF(base);
          if (current.targetId !== destination.targetId || current.hwId !== destination.hwId ||
              current.fwVersion !== destination.version || LoraOtaChecks.hex(current.bodyHash) !== destination.baseHash)
            throw Error('The installed firmware image does not exactly match the target.');
          let deltaOptions = {mode:'sequential'};
          if (destination.nrf) {
            const layout = MeshCoreLoraDelta.nrfLayout(current.image);
            if (destination.external) {
              const diagnostics = await link.remoteCommand(targetKey, password, 'ota qspi');
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
        if ($('base').files[0]) throw Error('An installed firmware image is only used when creating a differential from a .bin.');
        info = await MeshCoreMotaPackage.parseMota(input, expectedSigner ? {expectedSigner} : {});
      }
      const targetImage = $('image').files[0];
      if (!info.isFull && !targetImage && !expectedImage) throw Error('Select the matching new .bin image for the differential package.');
      if (!expectedImage && targetImage) expectedImage = await firmwareBytes(targetImage);
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
    } catch (error) { invalidate(); fail(error); }
    finally { preparing = null; busy(false); $('inspect').disabled = !link; }
  }
  async function transfer(resume) {
    $('transfer').disabled = true; $('resume').disabled = true; $('inspect').disabled = true; busy(true);
    try { await session.start({resume}); log('Transfer complete. Check the target and select Install.'); $('install').disabled = false; }
    catch (error) { fail(error); }
    finally { busy(false); $('inspect').disabled = !link; }
  }
  function run(task) { activeTask = task.finally(() => { activeTask = null; }); return activeTask; }
  $('connect').addEventListener('click', () => run(connect()));
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
      const baseFile=$('base').files[0], goalFile=$('file').files[0], middle=[...$('chain-files').files];
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
