/* Direct browser LoRa updater. All package and radio traffic stays local. */
(function () {
  'use strict';
  const ui = document.querySelector('[data-lora-ota]');
  if (!ui) return;
  const $ = name => ui.querySelector('[data-role="' + name + '"]');
  let link, session, info, expectedSigner, activeTask, preparing, chainPrepared, chainConfig, connectController;
  let autoChain=null, autoStep='';
  let contacts=[], targetLocked=false, contactKey='', disconnectGeneration=0, activeSourceKind=null;
  const sourceKind=()=>activeSourceKind||($('source').value==='auto'?'companion':$('source').value);
  const aclIntent=()=>$('acl').checked||$('password').value.length===0;
  const repeaterAclIntent=()=>sourceKind()==='repeater'&&aclIntent();
  let catalogFile=null, catalogBaseFile=null, catalogDest=null, catalogVersion='', catalogInstallSupported=false, catalogCandidates=[], catalogGeneration=0;
  let preservedCount=0, preservedExportSnapshot=null, preservedExportToken=null, preservedExportInterrupted=false;
  let investigationTimer=null, investigationStage='', investigationStopping=false, investigationActive=false;
  let operationErrorStep='LoRa update', investigationErrorContext=null;
  let failureSerial=0;
  let usbRetryActive=false,disconnecting=false;
  const usbRetryAttempted=new WeakSet();
  let sourceOtaUnsupported=false, targetAvailability=null, preparedDownload=null, preparedReplacement=null, uiBusy=false, updatePhase='';
  let sourceBytesRead=0, sourceActivityAt=0, sourceManifestReadLogged=false, lastTransferPercent=null, radioOverride=false, transferStage='';
  let targetRadioHwId=null;
  const quietStoreKey='meshcore-browser-lora-ota-quiet-v1';
  let quietPlan=null,quietWarning='',secondaryWarning='';
  const quietStore={
    read:()=>{const value=localStorage.getItem(quietStoreKey);return value?JSON.parse(value):null;},
    write:record=>localStorage.setItem(quietStoreKey,JSON.stringify(record)),
    clear:()=>localStorage.removeItem(quietStoreKey)
  };
  let transferStopJob=null,transferStopTask=null;
  function refreshTransferStop(){
    $('transfer-stop').hidden=updatePhase!=='transfer';
    $('transfer-stop').disabled=updatePhase!=='transfer'||!!transferStopTask||transferStopJob===session;
  }
  function status(message, state='working') {
    if(autoStep)message=autoStep+message;
    $('workflow-status').textContent=message;
    $('workflow-status').dataset.state=state;
    if(updatePhase){$('update-progress').hidden=false;$('update-progress').textContent=message;}
    refreshTransferStop();
  }
  function openDetails(element) {
    for(let node=element;node && node!==ui;node=node.parentElement)if(node.tagName==='DETAILS')node.open=true;
  }
  function updateButton() {
    $('update').disabled=uiBusy || !link || (!session && !autoChain) || !preparedDownload || updatesBlocked();
    $('update').textContent=autoChain?.resume ? 'Continue firmware update' : preparedDownload==='replace' ? 'Replace and install update' :
      preparedDownload==='failed' ? 'Restart and install update' : preparedDownload==='active' ? 'Resume and install update' :
      preparedDownload==='ready' ? 'Install and verify update' : 'Update remote firmware';
  }
  $('browser-support').hidden=!!navigator.serial;
  const updatesBlocked = () => !!link?.failure || sourceOtaUnsupported || !!targetAvailability && targetAvailability.state !== 'supported';
  function showAvailability(result) {
    const notice=$('ota-availability');
    notice.hidden=!result;
    notice.textContent=result?.message || '';
    notice.dataset.state=result?.state || '';
    if(result && result.state!=='supported')notice.scrollIntoView?.({block:'nearest',behavior:'smooth'});
  }
  function requireUpdatesAvailable() {
    if(updatesBlocked())throw Error($('ota-availability').textContent || 'LoRa updates are unavailable. Select Find matching firmware to check the device again.');
  }
  const log = text => {
    $('status').textContent += String(text) + '\n'; $('status').scrollTop = $('status').scrollHeight;
    if(updatePhase==='transfer'&&transferStopJob!==session) {
      const percent=/\bdownload:.*?\((\d{1,3})%\)/i.exec(String(text));
      if(percent && Number(percent[1])<=100){
        lastTransferPercent=Number(percent[1]);
        status('Target reports download progress: '+lastTransferPercent+'%. Keep this page open.');
      }
    }
  };
  function refreshQuietRecovery(){
    const notice=$('quiet-forwarding-status'),button=$('restore-forwarding');
    if(!notice||!button)return;
    try{
      const record=quietStore.read();
      notice.hidden=!record&&!quietWarning&&!secondaryWarning;button.hidden=!record;
      if(record)notice.textContent=record.state==='paused'
        ? 'Forwarding for target '+String(record.target||'').slice(0,12)+' is paused. Restoration will be checked separately from firmware completion. Keep this page open.'
        : 'Forwarding restoration is pending for target '+String(record.target||'').slice(0,12)+
          '. Keep this page open. Reconnect the same USB source and target if necessary, then select Restore forwarding.';
      else notice.textContent=quietWarning;
      if(record&&quietWarning)notice.textContent+=' '+quietWarning;
      if(secondaryWarning)notice.textContent+=(notice.textContent?' ':'')+secondaryWarning;
    }catch(_){
      notice.hidden=button.hidden=false;
      notice.textContent='The saved forwarding recovery record could not be read. No automatic setting change will be made.';
    }
    button.disabled=uiBusy||!link||!!link.failure;
  }
  function quietEnabled(){return $('quiet-forwarding')?.checked===true&&!link?.relays?.length;}
  const quietProgress=message=>{log(message);refreshQuietRecovery();};
  function quietContinuation(){
    const connection=link,generation=disconnectGeneration,controller=preparing;
    return ()=>link===connection&&disconnectGeneration===generation&&!controller?.signal.aborted;
  }
  function quietDetail(error){
    const secret=$('password').value;
    let detail=error?String(error.message||error):'';
    if(secret)detail=detail.split(secret).join('<hidden>');
    return detail;
  }
  function quietWarn(message,error){
    const detail=quietDetail(error);
    quietWarning=message+(detail?' '+detail:'');log('Warning: '+quietWarning);refreshQuietRecovery();
  }
  function quietMustHalt(error,connection,canContinue){
    if(!canContinue()||disconnecting||connection!==link||connection?.failure||
        error?.otaQuietCancelled||error?.otaQuietIdentityChanged||error?.otaQuietTransport||error?.otaUsbTimeout||
        (['USB Companion','USB Repeater'].includes(error?.otaEndpoint)&&error?.otaOptionalUnavailable!==true)||error?.name==='AbortError')
      throw connection?.failure||error||Error('Forwarding administration stopped because the USB source changed.');
  }
  async function prepareQuietForwarding(target,password){
    if(!quietEnabled())return null;
    if(!quietPlan||quietPlan.link!==link||quietPlan.target!==target.toLowerCase()||quietPlan.password!==password){
      const connection=link,canContinue=quietContinuation();let plan;
      try{
        if(!globalThis.LoraOtaQuiet)throw Error('Forwarding pause support did not load.');
        if(connection.browserRawSource!==true||connection.otaSupported!==true)
          throw Error('This source has no bounded optional forwarding reads; forwarding is unchanged.');
        plan=await LoraOtaQuiet.plan(connection,target,password,{store:quietStore,progress:quietProgress,canContinue,allowReacquire:false});
      }catch(error){
        quietMustHalt(error,connection,canContinue);
        quietWarn('Forwarding pause skipped; the firmware update can continue.',error);
        plan={state:'skipped',source:connection.sourceKey,target:target.toLowerCase()};
      }
      quietPlan={link:connection,target:target.toLowerCase(),password,plan};
    }
    return quietPlan.plan;
  }
  async function pauseQuietForwarding(target,password,radio){
    if(!quietEnabled()||!radio||![radio.sourceProfile,radio.targetProfile].includes(1))return;
    const connection=link,canContinue=quietContinuation();
    const plan=await prepareQuietForwarding(target,password);
    if(!canContinue())throw Error('Update preparation stopped before pausing forwarding.');
    if(plan?.state==='skipped')return;
    try{
      const result=await LoraOtaQuiet.pause(connection,target,password,plan,
        {store:quietStore,progress:quietProgress,radioVerified:true,canContinue,allowReacquire:false});
      if(!canContinue())throw Error('Update preparation stopped; forwarding restoration will be checked.');
      if(!['paused','already-off','skipped'].includes(result?.state))throw result?.error||Error('Forwarding pause is unconfirmed; its recovery record is retained.');
    }catch(error){
      quietMustHalt(error,connection,canContinue);
      quietWarn('Forwarding pause is unconfirmed; continuing the firmware update without repeating its setter.',error);
      quietPlan={link:connection,target:target.toLowerCase(),password,plan:{state:'skipped',source:connection.sourceKey,target:target.toLowerCase()}};
    }finally{refreshQuietRecovery();}
  }
  async function restoreQuietForwarding({required=false,retry=false}={}){
    try{
      const record=quietStore.read();
      if(!record)return;
      if(!globalThis.LoraOtaQuiet)throw Error('Forwarding restoration support did not load. Refresh the page before restoring the saved setting.');
      const target=$('key').value.trim().toLowerCase();
      if(!link||link.failure||link.serving||link.folderUnknown||link.relays?.length||
          record.source!==link.sourceKey?.toLowerCase()||record.target!==target){
        if(required)throw Error('Forwarding still needs restoration. Reconnect the same source and target, then select Restore forwarding.');
        return;
      }
      if(link.otaRecoveryProfilePending&&!(link.authenticated===true&&link.knownRepeater?.toLowerCase()===target))
        throw Error('USB is connected, but its previous temporary update channel could not be verified. Check the update channel before restoring forwarding; no target command was sent.');
      if(!required&&(link.browserRawSource!==true||link.otaSupported!==true))
        throw Error('This source has no bounded automatic cleanup. Select Restore forwarding to check it explicitly.');
      // A fresh connection needs selection; an authenticated same-target
      // session must not be reselected while its OTA guard is still active.
      if(link.knownRepeater?.toLowerCase()!==target)selectTarget();
      const result=await LoraOtaQuiet.restore(link,target,$('acl').checked?'':$('password').value,
        {store:quietStore,progress:quietProgress,retry,allowReacquire:required});
      link.otaRecoveryProfilePending=false;
      quietPlan=null;
      if(!['restored','none','already-off'].includes(result?.state))
        throw Error('Forwarding restoration is still pending. Select Restore forwarding.');
      quietWarning='';
    }catch(error){
      quietWarn('Forwarding restoration remains pending; the firmware result is unchanged.',error);
      if(link?.failure)$('reconnect').hidden=false;
      if(required)throw error;
    }finally{quietPlan=null;refreshQuietRecovery();}
  }
  async function finishQuietForwarding(job=session){
    // The caller has already verified identity, installed version and image
    // hash. Optional housekeeping must not turn that completed update red.
    status('Firmware update DONE and verified. Checking forwarding restoration...', 'complete');
    try{
      const saved=job?.readJournal?.()||readJournal();
      if(saved?.secondaryRestorationPending){
        if(!job||job.link!==link||link?.browserRawSource!==true||link.otaSupported!==true||
            link.failure||link.serving||link.folderUnknown||link.relays?.length)
          throw Error('Use the same healthy guarded USB Companion to check the normal radio2 profile.');
        await job.verifyRestoredTargetSecondary({maxDurationMs:5000});
        if(job.readJournal()?.secondaryRestorationPending)throw Error('The saved radio2 profile remains unconfirmed.');
        secondaryWarning='';
      }
    }catch(error){
      secondaryWarning='Firmware is verified; normal radio2 restoration has not yet been confirmed. '+quietDetail(error);
      log('Warning: '+secondaryWarning);refreshQuietRecovery();
    }
    await restoreQuietForwarding();
  }
  function completionDisconnectTip(){
    let pending=!!secondaryWarning;
    try{pending=pending||!!quietStore.read();}catch(_){pending=true;}
    return pending?' Firmware is verified. Review the pending settings warning before disconnecting.':
      ' You can disconnect the USB radio.';
  }
  function sessionStage(stage) {
    if(updatePhase!=='transfer' || transferStopJob===session || transferStage===stage)return;
    transferStage=stage;
    const messages={
      checking:link?.relays?.length ? 'Checking the route, target and radio settings...' : 'Checking the target and radio settings...',
      'preparing-source':'Preparing the source radio to offer the firmware...',
      discovering:'Waiting for the target to discover the firmware...',
      requesting:'Asking the target to begin receiving the firmware...',
      transferring:'The target reports that its firmware download is in progress. Keep this page open...'
    };
    if(messages[stage]) { operationErrorStep=messages[stage];status(messages[stage]); log(messages[stage]); }
  }
  function onFolderRead({op,args,status:result}) {
    if(result!==0 || ![3,9].includes(op) || args?.length!==7 || updatePhase!=='transfer'||transferStopJob===session)return;
    const length=args[5]+256*args[6];if(!length)return;
    if(op===3 && args[0]===0 && !sourceManifestReadLogged){
      const offset=args[1]+args[2]*256+args[3]*65536+args[4]*16777216;
      if(offset<205 && offset+length>8){
        sourceManifestReadLogged=true;
        log('USB source read firmware manifest metadata; this does not confirm radio delivery.');
      }
    }
    if(transferStage!=='transferring')return;
    sourceBytesRead+=length;
    if(Date.now()-sourceActivityAt<1000)return;
    sourceActivityAt=Date.now();
    const confirmed=lastTransferPercent===null?'':lastTransferPercent+'% last reported by the remote device. ';
    // Host-file reads show real source activity, not remote reception or
    // verification. Repeated metadata/proof reads can exceed the image size.
    status(confirmed+'The USB radio has read '+Math.ceil(sourceBytesRead/1024)+' KB of firmware data. Keep this page open.');
  }
  function onFolderBootstrap() {
    if(updatePhase!=='transfer'||transferStopJob===session)return;
    const text='Waiting for the target to request the firmware manifest. This can take a minute; keep this page open.';
    status(text);log(text);
  }
  function onFolderAdmission({advertised,offered}) {
    if(updatePhase!=='transfer'||transferStopJob===session)return;
    if(advertised===0){
      const message='USB source omitted the selected host package ('+advertised+'/'+offered+'). Another copy may be available; if the manifest times out, check source capacity or use a different source.';
      log(message);status(message);
    }else{
      const message='USB source registered the host package. Waiting for the target to request its manifest.';
      log(message);status(message);
    }
  }
  function showSourceUpdate(kind) {
    if(kind==='companion' || kind==='repeater') {
      const role=kind==='companion'?'companionFull':'repeater';
      $('source-update-link').href='https://mikecarper.github.io/meshfirmware/?provider=Keymind%20Cascade&role='+role+'&mode=update#firmware-choices';
      $('source-update-link').textContent=kind==='companion'?'Update Companion firmware':'Update Repeater firmware';
      $('source-update-help').hidden=false;
    }
  }
  function failurePath() {
    if(!link)return 'Unknown (USB source not connected)';
    if(typeof link.otaPath==='string')return link.otaPath;
    if(typeof link.link?.otaPath==='string')return link.link.otaPath;
    if(sourceKind()==='companion'&&link.otaSupported===true)return 'Direct (0 hops; guarded OTA session)';
    if(Array.isArray(link.relays)&&link.relays.length)return 'Selected route: '+link.relays.length+' relay hop'+(link.relays.length===1?'':'s')+' (request path not confirmed)';
    if(sourceKind()==='companion'){
      const contact=contacts.find(item=>item.publicKey?.toLowerCase()===$('key').value.trim().toLowerCase());
      if(Number.isInteger(contact?.hops)&&contact.hops>=0&&contact.hops<=63)return 'Stored contact route: '+(contact.hops===0?'direct (0 hops)':contact.hops+' hops')+' (not verified for this request)';
    }
    return 'Unknown (not confirmed for this request)';
  }
  function failureRadio() {
    if(!link)return 'Unknown (USB source not connected)';
    const source=link.link||link;
    if(source.otaRadioProfile===1&&source.supportsOtaRadio2)return 'Radio 2 (selected source OTA profile; reception not confirmed)';
    if(source.otaRadioProfile===0&&(source.otaSupported===true||source.supportsOtaRadio2))return 'Primary radio (selected source OTA profile; reception not confirmed)';
    return 'Unknown (not confirmed for this request)';
  }
  function failureRecord(error) {
    // Context is display-only. Never rewrite the transport error/message:
    // timeout matching and uncertain-install handling depend on the original.
    const context=investigationActive&&investigationErrorContext?investigationErrorContext:{};
    return {otaPath:failurePath(),otaRadio:failureRadio(),...context,...error,message:error?.message||String(error)};
  }
  function failureMessage(error,step=operationErrorStep) {
    const display=failureRecord(error);
    const secrets=[$('password').value].filter(Boolean);
    if(typeof LoraOtaChecks.errorDetails==='function')return LoraOtaChecks.errorDetails(display,{step,secrets});
    let message=String(display.message);for(const secret of secrets)message=message.split(secret).join('<hidden>');
    return 'Step: '+step+'\nDetails: '+message;
  }
  function showDetailedErrors() {
    const visible=$('verbose-errors').checked;
    $('verbose-error-panel').hidden=!visible;
    if(visible)openDetails($('diagnostics'));else $('diagnostics').open=false;
    if(investigationActive||!$('investigation-details').hidden)$('investigation-details').open=visible;
  }
  $('verbose-errors').addEventListener('change',showDetailedErrors);
  const fail = async (error,step=operationErrorStep) => {
    const failureId=++failureSerial;
    const record=failureRecord(error),connection=link,generation=disconnectGeneration;
    const direct=connection?.link||connection,sourceKey=connection?.sourceKey;
    const target=$('key').value.trim().toLowerCase(),password=$('acl').checked?'':$('password').value;
    const source={firmwareVersion:direct?.firmwareVersion,firmwareBoardName:direct?.firmwareBoardName};
    let targetIdentity={};
    try{targetIdentity=connection?.cachedFailureIdentity?.(target)||{};}catch(_){}
    const message=failureMessage(record,step);
    const reason=message.includes('\nReason: ')?message.slice(message.indexOf('\nReason: ')+9):message;
    const summary=message.split('\n')[0]+'\n'+reason;
    status(summary, 'error');
    $('error-details').textContent=message;
    // Local sizing failures have a complete, actionable summary. Keep their
    // technical context available without opening the long transport report.
    if(error?.otaLocalCheck!==true)$('verbose-errors').checked=true;
    showDetailedErrors();
    if(error?.sourcePacketCapacity) {
      sourceOtaUnsupported=true;invalidate();
      showAvailability({state:'unsupported',message:error.message});
      $('firmware-catalog-status').textContent=error.message;
    } else if(error?.sourceUpdateRequired==='companion' && link?.rawPacketSupport==='unsupported') {
      sourceOtaUnsupported=true;invalidate();
      showAvailability({state:'unsupported',message:'LoRa updates are unavailable with this USB Companion: its firmware cannot send OTA packets. Update the source Companion firmware first.'});
      $('firmware-catalog-status').textContent=$('ota-availability').textContent;
    }
    if(error?.otaAvailability && typeof error.otaAvailability==='object') {
      targetAvailability=error.otaAvailability;
      showAvailability(targetAvailability);invalidate();
      $('firmware-catalog-status').textContent=targetAvailability.message;
    }
    if(/temporary radio|temporary settings|update channel|saved primary/i.test(error?.message || '')) {
      $('radio-setup-status').textContent=message;
      openDetails($('radio-setup-panel'));
      if(error?.manualRadioCommand)$('radio-commands').textContent='On the remote target first, then on the USB source:\n'+error.manualRadioCommand;
      openDetails($('radio-commands'));
      $('radio-setup-panel').scrollIntoView?.({block:'nearest',behavior:'smooth'});
    }
    if(error?.changedSettings?.some(change=>!change.confirmed))openDetails($('diagnostics'));
    log('Error: ' + message);
    showSourceUpdate(error?.sourceUpdateRequired);
    if(link?.failure){
      invalidate();refreshPreservedMessages();
      status(summary+'\nUSB communication stopped. Select Reconnect USB source; your target and firmware choices are kept.', 'error');
      $('reconnect').hidden=false;$('reconnect').scrollIntoView?.({block:'nearest',behavior:'smooth'});
    }
    // Binary USB replies have no transaction ID. Reopen and re-identify the
    // same port once instead of replaying an unanswered frame into that stream.
    // The interrupted update remains stopped; only recovery reads/cleanup run.
    if((error?.otaUsbTimeout||direct?.failure?.otaUsbTimeout)&&connection===link&&
        generation===disconnectGeneration&&!connectController&&!usbRetryActive&&!disconnecting&&
        sourceKind()==='companion'&&connection?.port&&/^[a-f0-9]{64}$/i.test(sourceKey||'')&&
        !usbRetryAttempted.has(connection)){
      usbRetryAttempted.add(connection);usbRetryActive=true;
      status(summary+'\nRetrying the same USB connection once. The update is stopped; no install command will be repeated.', 'working');
      log('USB command acknowledgement was lost. Reopening the same USB source once; no unanswered command is replayed.');
      try{
        const recovered=await reconnect({automatic:true});
        if(recovered&&generation===disconnectGeneration&&$('key').value.trim().toLowerCase()===target){
          busy(true);
          if(globalThis.LoraOtaQuiet)await restoreQuietForwarding();
          if(link?.failure){
            $('reconnect').hidden=false;
            status(summary+'\nUSB recovery encountered another communication failure. Select Reconnect USB source; the update remains stopped.', 'error');
          }else{
            status(summary+'\nUSB reconnected. The update is stopped. Select Prepare update to check the target before continuing.', 'error');
            log('USB source identity confirmed after retry. Select Prepare update to read the target state; the interrupted update was not restarted.');
          }
        }
      }catch(recoveryError){
        log('Automatic USB reconnect did not finish: '+failureMessage(recoveryError,'Reconnect the same USB source'));
        if(generation===disconnectGeneration)
          status(summary+'\nThe USB retry failed. Select Connect USB source or reconnect its USB data cable; the update remains stopped.', 'error');
      }finally{usbRetryActive=false;}
      return;
    }
    if(globalThis.LoraOtaQuiet)await restoreQuietForwarding();
    if(error?.otaLocalCheck===true || typeof LoraOtaChecks.collectFailureIdentity!=='function')return;
    const sameDevice=()=>failureId===failureSerial&&link===connection&&disconnectGeneration===generation&&connection?.sourceKey===sourceKey&&$('key').value.trim().toLowerCase()===target;
    const canRead=()=>sameDevice()&&($('acl').checked?'':$('password').value)===password&&!!direct?.running&&!direct.failure&&!connection?.failure&&!direct.investigationCancelled&&!investigationStopping&&!direct.serving&&!direct.seeder&&!direct.folderUnknown&&!direct.usbFolderAttached;
    try{
      // Keep the UI action reserved until these bounded reads finish. They use
      // only an existing session: no login, route/radio change or install retry.
      const identity=await LoraOtaChecks.collectFailureIdentity({source,target:targetIdentity,canContinue:canRead,
        read:typeof connection?.diagnosticRead==='function'?(command,options)=>connection.diagnosticRead(target,password,command,{...options,canContinue:canRead}):undefined});
      if(!sameDevice())return;
      record.otaIdentity=identity;
      const details=LoraOtaChecks.errorDetails(record,{step,secrets:[password].filter(Boolean)});
      $('error-details').textContent=details;
      log('Failure device information:\n'+details);
      if(direct?.failure||connection?.failure){status(summary+'\nUSB communication stopped while collecting device information. Reconnect the USB source before retrying.','error');$('reconnect').hidden=false;}
    }catch(_){
      // Optional diagnostics must never replace the failure or cause an
      // uncertain command to be repeated. The original error remains visible.
    }
  };
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
  function readStepRecord(storageKey) {
    const raw=localStorage.getItem(storageKey);
    if(raw===null)return null;
    let value;
    try{value=JSON.parse(raw);}catch(_){}
    if(!value||typeof value!=='object'||Array.isArray(value))
      throw Object.assign(Error('The saved update-step record is unreadable. No recovery data was cleared. This local check did not send a radio command.'),
        {otaLocalCheck:true,otaLocalStateCheck:true,otaEndpoint:'Browser',otaStep:'Read saved update-step recovery data'});
    return value;
  }
  const readStepJournal = () => readStepRecord('meshcore-browser-lora-ota-chain-step');
  const writeChainJournal = state => localStorage.setItem('meshcore-browser-lora-ota-chain', JSON.stringify({...state, at:Date.now()}));
  const readChainJournal = () => { try { return JSON.parse(localStorage.getItem('meshcore-browser-lora-ota-chain')); } catch (_) { return null; } };
  const readChainMeta = () => { try { return JSON.parse(localStorage.getItem('meshcore-browser-lora-ota-chain-meta')); } catch (_) { return null; } };
  const editable = ['source', 'key', 'acl', 'relays', 'candidates', 'snr', 'age', 'password', 'file', 'base', 'full-rebuild', 'image', 'signer',
    'chain-files', 'chain-attest', 'chain-objective', 'chain-preamble', 'chain-minutes','auto-search','auto-objective','auto-depth'];
  const radioFields=['radio-frequency','radio-bandwidth','radio-sf','radio-cr','radio-preamble','radio-minutes'];
  const radioDefaults=Object.fromEntries([...radioFields,'radio-region'].map(name=>[name,$(name).value]));
  for(const profile of LoraOtaRadioSetup.regions||[]){
    const option=document.createElement('option');option.value=profile.id;option.textContent=profile.label;$('radio-region').appendChild(option);
  }
  function radioOptions() {
    return {frequency:Number($('radio-frequency').value),bandwidth:Number($('radio-bandwidth').value),
      sf:Number($('radio-sf').value),cr:Number($('radio-cr').value),
      preamble:Number($('radio-preamble').value),minutes:Number($('radio-minutes').value)};
  }
  const radioHardware=()=>({sourceBoard:link?.firmwareBoardName,targetHwId:targetRadioHwId});
  const explicitRadioChoice=()=>radioOverride||$('radio-region').value!=='auto';
  function chosenRadioMatches(probe) {
    const options=radioOptions();
    return Array.isArray(probe.tempRadio)&&LoraOtaRadioSetup.same(probe.tempRadio,[options.frequency,options.bandwidth,options.sf,options.cr])&&
      probe.verifiedRadio?.preamble===options.preamble;
  }
  function showRadioCommands() {
    try {
      const options=radioOptions();
      if(link?.browserRawSource && link.otaSupported!==true && !link.usesUsbFolderFallback && Array.isArray(link.radio)) {
        // Older source firmware cannot bind a different temporary channel.
        // Suggest the usable saved tuple, not the separate-channel default.
        [options.frequency,options.bandwidth,options.sf,options.cr]=link.radio;
      }
      const command=LoraOtaRadioSetup.commands(options);
      $('radio-commands').textContent=command.manual;
    } catch(error) { $('radio-commands').textContent=error.message; }
  }
  function canPrepareRadioChannel() {
    return !!link && !link.relays?.length && typeof link.closeRadioSession==='function' &&
      (!link.browserRawSource || link.localCliSupported===true);
  }
  async function recoverUpdateChannel(target) {
    if(!globalThis.LoraOtaRecovery || link.relays?.length)return null;
    const result=await LoraOtaRecovery.restoreSource(link,target,readJournal(),{override:explicitRadioChoice(),progress:message=>{status(message);log(message);}});
    if(!result.requiresVerification)return null;
    [$('radio-frequency').value,$('radio-bandwidth').value,$('radio-sf').value,$('radio-cr').value]=
      [result.tuple[0].toFixed(3),...result.tuple.slice(1).map(String)];
    $('radio-preamble').value=String(result.preamble);showRadioCommands();
    $('radio-region-status').textContent='Using the verified temporary channel for this interrupted update.';
    return result;
  }
  async function confirmUpdateChannel(result,target,password) {
    if(!result?.requiresVerification)return;
    let verifiedKey=target,adminVerified=false;
    if(!link.browserRawSource){
      verifiedKey=String(await link.remoteCommand(target,password,'get public.key')).trim().replace(/^>\s*/,'');
      if(verifiedKey.toLowerCase()!==target.toLowerCase())throw Error('Target did not prove its full public key on the recovered channel');
      // An ACL command replaces the Repeater source's current peer session.
      // Renew its ordinary access proof before extending our source lease.
      if(link.authenticatedKey?.toLowerCase()!==target.toLowerCase())await link.verifyPeer(target,password);
    }else if(link.otaSupported!==true && link.investigationPermission==='unknown'){
      const config=String(await link.investigateCommand(target,password,'ota config')).trim().replace(/^>\s*/,'');
      // Some old ACL replies omit the role. This read is restricted to admins
      // by the target; a publicly readable identity alone does not prove it.
      if(!/^ota config:/i.test(config)||!/(?:^|\s)autofetch=(?:on|off)(?:\s|$)/i.test(config)||
          !/(?:^|\s)autoinstall=(?:on|off)(?:\s|$)/i.test(config))throw Error('Target administration could not be verified on the recovered channel');
      adminVerified=true;
    }
    await LoraOtaRecovery.confirmSource(link,result,{verifiedKey,adminVerified,progress:log});
    link.otaRecoveryProfilePending=false;
  }
  async function suggestRadioChannel() {
    if(!radioOverride) {
      const source=link,region=$('radio-region').value;
      let saved=source?.radio,active=null;
      const readable=typeof source?.localCommand==='function'&&(!source.browserRawSource||source.localCliSupported===true);
      if(readable)try{
        if(!Array.isArray(saved))saved=LoraOtaRadioSetup.primary(await source.localCommand('get radio'));
        active=await LoraOtaRadioSetup.readTemporary(command=>source.localCommand(command),source.otaRadioProfile??0);
      }catch(error){if(source.failure)throw error;}
      if(source!==link||region!==$('radio-region').value)return;
      let profile=LoraOtaRadioSetup.recommendProfile(saved,{region,active,hardware:radioHardware()});
      if(!radioOverride&&source&&!source.browserRawSource&&profile?.sf===5&&['region','community'].includes(profile.selection)&&typeof source.firmwareBoardName!=='string'){
        source.firmwareBoardName=String(await source.localCommand('board')).trim().replace(/^>\s*/,'');
        if(source!==link||region!==$('radio-region').value)return;
        profile=LoraOtaRadioSetup.recommendProfile(saved,{region,active,hardware:radioHardware()});
      }
      if(!radioOverride&&profile){
        $('radio-frequency').value=profile.frequency.toFixed(3);$('radio-bandwidth').value=String(profile.bandwidth);
        $('radio-sf').value=String(profile.sf);$('radio-cr').value=String(profile.cr);
        if(profile.preamble!==undefined)$('radio-preamble').value=String(profile.preamble);
        $('radio-region-status').textContent=profile.label+': '+profile.frequency.toFixed(3)+' MHz, BW'+profile.bandwidth+', SF'+profile.sf+', CR'+profile.cr+'.'+
          (profile.ambiguous?' Country cannot be determined from these radio settings.':'')+
          (profile.hardwareAdjusted?' '+profile.hardwareReason+'.':'');
      }else if(!radioOverride){
        $('radio-region-status').textContent='Connect a radio to detect its settings, or choose a region.';
      }
    }
    if(radioOverride){
      $('radio-region-status').textContent='Your manually selected radio settings take precedence.';
    }
    showRadioCommands();
    $('radio-setup-status').textContent=!link?'Connect a USB radio to prepare the update channel.':canPrepareRadioChannel()
      ? 'The update channel is prepared automatically. The target\'s normal radio2 may pause temporarily; its saved settings return after reboot or window expiry.'
      : 'Update this Companion to enable automatic channel setup: its firmware cannot read and set temporary channels over USB.';
  }
  async function applyTargetRadioHardware(hwId){
    if(targetRadioHwId===hwId)return;
    targetRadioHwId=hwId;
    if(!radioOverride)await suggestRadioChannel();
  }
  async function prepareRadioChannel(target,password,options={}) {
    await prepareQuietForwarding(target,password);
    if(!$('radio-frequency').value || link.browserRawSource && link.otaSupported!==true && !link.usesUsbFolderFallback) {
      const reply=String(await link.localCommand('get radio')).trim().replace(/^>\s*/,'');
      const match=/^(\d+(?:\.\d+)?),(\d+(?:\.\d+)?),(\d+),(\d+)$/.exec(reply);
      if(!match)throw Error('Cannot read the Companion saved radio settings for automatic setup.');
      const saved=match.slice(1).map(Number), compatibility=link.browserRawSource && link.otaSupported!==true && !link.usesUsbFolderFallback;
      const chosen=radioOptions();
      if(compatibility&&explicitRadioChoice()&&!LoraOtaRadioSetup.same(saved,[chosen.frequency,chosen.bandwidth,chosen.sf,chosen.cr])){
        const error=Error('This USB Companion can use only its saved primary channel for OTA. Use those saved settings or update the source Companion to use another profile.');
        error.sourceUpdateRequired='companion';throw error;
      }
      const profile=compatibility?{frequency:saved[0],bandwidth:saved[1],sf:saved[2],cr:saved[3]}:LoraOtaRadioSetup.recommendProfile(saved,{region:$('radio-region').value,hardware:radioHardware()});
      $('radio-frequency').value=profile.frequency.toFixed(3);
      $('radio-bandwidth').value=String(profile.bandwidth);$('radio-sf').value=String(profile.sf);$('radio-cr').value=String(profile.cr);
      showRadioCommands();
    }
    const showChanges=changes=>{
      for(const change of changes||[]){
        log((change.endpoint==='source'?'USB source':'Target')+' saved '+change.setting+': '+change.before+' -> '+change.after+
          (change.confirmed?' (verified)':' (result uncertain)')+'.');
        if(change.restoreCommand)log('Previous setting: '+change.restoreCommand);
      }
    };
    try{
      const result=await LoraOtaRadioSetup.prepare(link,target,password,radioOptions(),{...options,progress:message=>{
        $('radio-setup-status').textContent=message;status(message);log(message);
      }});
      $('radio-frequency').value=result.source[0].toFixed(3);
      $('radio-bandwidth').value=String(result.source[1]);$('radio-sf').value=String(result.source[2]);$('radio-cr').value=String(result.source[3]);
      $('radio-preamble').value=String(result.preamble);showRadioCommands();
      $('radio-region-status').textContent='Verified update channel: '+result.source[0].toFixed(3)+' MHz, BW'+result.source[1]+', SF'+result.source[2]+', CR'+result.source[3]+'.';
      showChanges(result.changedSettings);link.otaRecoveryProfilePending=false;
      await pauseQuietForwarding(target,password,result);
      return result;
    }catch(error){showChanges(error.changedSettings);throw error;}
  }
  async function ensureRadioChannel(target,password) {
    const probe=new LoraOtaSession.DirectSession(link,target,password,null,{progress:log,deferSecondaryRestoration:true});
    try{
      const result=await probe.inspectRadios();
      if(!explicitRadioChoice()||chosenRadioMatches(probe)){
        link.otaRecoveryProfilePending=false;
        await pauseQuietForwarding(target,password,probe.verifiedRadio);return result;
      }
    }
    catch(error) {
      // Fix verified setup requirements automatically. The setup preflight
      // still refuses pending/unknown state and transport failures.
      if(!canPrepareRadioChannel() || !error.radioSetupRequired)throw error;
    }
    if(!canPrepareRadioChannel())throw Error('This USB source cannot prepare your selected update channel automatically.');
    await prepareRadioChannel(target,password,{useActivePrimary:!explicitRadioChoice()});
    const result=await probe.inspectRadios();
    link.otaRecoveryProfilePending=false;
    $('radio-setup-status').textContent='Update channel ready. Source: '+(link.otaRadioProfile===1?'temporary radio2':'temporary primary')+
      '; target: '+(link.otaTargetProfile===1?'temporary radio2':'temporary primary')+'.';
    await pauseQuietForwarding(target,password,probe.verifiedRadio);
    return result;
  }
  async function checkRadioChannel(prepare) {
    invalidate();busy(true);
    try {
      if(!link)throw Error('Connect a source radio first.');
      requireUpdatesAvailable();
      if(link.relays?.length)throw Error('Prepare matching temporary settings on every route participant manually.');
      const target=selectTarget(),password=$('acl').checked?'':$('password').value;
      const recovered=await recoverUpdateChannel(target);
      await link.verifyPeer(target,password);
      await confirmUpdateChannel(recovered,target,password);
      if(prepare)await prepareRadioChannel(target,password);
      const probe=new LoraOtaSession.DirectSession(link,target,password,null,{progress:log,deferSecondaryRestoration:true});
      const result=await probe.inspectRadios();
      const description=result.source[0].toFixed(3)+' MHz, BW'+result.source[1]+', SF'+result.source[2]+', CR4/'+result.source[3];
      const message=result.sourceReadback==='saved-only'
        ? 'Target channel matches the Companion saved settings ('+description+'). This Companion cannot confirm its active temporary settings; check them on the device.'
        : (prepare?'Channel prepared: ':'Channel ready: ')+description+'. Both radios report matching active temporary settings.';
      $('radio-setup-status').textContent=message;log(message);
    } catch(error) { $('radio-setup-status').textContent=failureMessage(error);await fail(error); }
    finally { busy(false);$('inspect').disabled=!link || updatesBlocked(); }
  }
  function busy(active) {
    if(link && sourceKind()==='companion' && (link.guard || link.authenticated || link.investigationEnabled || link.investigationAuthenticated || link.serving))targetLocked=true;
    uiBusy=active;updateButton();
    refreshTransferStop();
    $('inspect').disabled=active || !link || updatesBlocked();
    $('find-route').disabled=active || !link || updatesBlocked() || !!link.relays || !repeaterAclIntent() || !link.supportsRoutedPath;
    for(const [name,states] of [['transfer',['idle']],['resume',['active','failed']],['install',['ready']]])
      $(name).disabled=active || !link || !session || updatesBlocked() || !states.includes(preparedDownload);
    $('resume').textContent=preparedDownload==='failed' ? 'Restart' : 'Resume';
    $('target-controls').hidden=!link && sourceKind()==='companion';
    $('disconnect').disabled=!link && !connectController;
    $('reconnect').hidden=!link?.failure;
    $('reconnect').disabled=active || !link?.failure;
    for (const name of editable) $(name).disabled = active || (updatesBlocked() && ['file','base','image','signer','chain-files','chain-attest','chain-objective','chain-preamble','chain-minutes'].includes(name)) || (!!link && (name === 'source' || name === 'acl' || name === 'relays' || (name === 'key' && (targetLocked || !!link.knownRepeater))));
    $('contact-search').disabled = active || !link || sourceKind() !== 'companion' || targetLocked;
    updateContactButtons();
    $('investigate').disabled = active || !link || !!link.failure;
    $('legacy-inbox-export').disabled = active || !preservedCount;
    $('legacy-inbox-clear').disabled = active || !preservedCount || !preservedExportSnapshot;
    $('firmware-role').disabled = active || !link || updatesBlocked();
    $('firmware-choice').disabled = active || !link || updatesBlocked();
    $('firmware-download').disabled = active || !link || updatesBlocked() || !$('firmware-choice').value;
    for (const name of ['repeat-read', 'repeat-off', 'repeat-on']) $(name).disabled = active || !link || !!link.failure;
    $('repeat-node').disabled = $('repeat-key').disabled = $('repeat-kind').disabled = active;
    for(const name of radioFields)$(name).disabled=active;
    $('radio-region').disabled=active;
    $('radio-reset').disabled=active;
    $('radio-check').disabled=active || !link || updatesBlocked();
    $('radio-prepare').disabled=active || updatesBlocked() || !canPrepareRadioChannel();
    const pending = readChainJournal();
    $('chain-plan').disabled = active || !link || updatesBlocked();
    $('chain-run').disabled = active || !link || updatesBlocked() || !chainPrepared || !chainConfig || !!pending && pending.phase !== 'complete';
    $('chain-resume').disabled = active || !link || updatesBlocked() || !chainPrepared || !chainConfig || !pending || pending.phase === 'complete';
    if($('quiet-forwarding'))$('quiet-forwarding').disabled=active;
    refreshQuietRecovery();
  }
  function invalidate() { preparedDownload=preparedReplacement=null; $('update').disabled=true;
    autoChain=null;autoStep='';
    $('update-progress').hidden=true;
    $('update-summary').textContent='Prepare the firmware first. The update will transfer, install and verify the running firmware.';
    session = info = chainPrepared = chainConfig = null; $('transfer').disabled = true; $('resume').disabled = true;
    $('install').disabled = true; $('chain-run').disabled = true; $('chain-resume').disabled = true; }
  function showInvestigationProgress(label, timeoutMs) {
    const progress=$('investigation-progress'); if(progress.hidden)return;
    const stage=label+'|'+(Number.isFinite(timeoutMs)?timeoutMs:'');
    if(stage===investigationStage)return;
    investigationStage=stage; clearInterval(investigationTimer);
    $('investigation-progress-label').textContent=label;
    const started=Date.now(), update=()=>{
      const seconds=Math.floor((Date.now()-started)/1000);
      $('investigation-progress-elapsed').textContent='Elapsed: '+seconds+'s'+
        (Number.isFinite(timeoutMs)&&timeoutMs>0 ? ' (reply timeout: '+Math.ceil(timeoutMs/1000)+'s)' : '');
    };
    update();investigationTimer=setInterval(update,1000);
  }
  function finishInvestigationProgress(label) {
    clearInterval(investigationTimer);investigationTimer=null;investigationStage='';
    $('investigation-progress-label').textContent=label;
    $('investigation-progress-elapsed').textContent='';
  }
  function onInvestigationProgress(event) {
    if(!event || typeof event.stage!=='string' || investigationStopping || !investigationActive)return;
    if(event.stage==='clock-warning') {
      if(typeof event.message==='string')$('investigation').textContent+='Companion clock: '+event.message+'\n';
      return;
    }
    if(event.stage==='login-fallback') {
      if(typeof event.message==='string')$('investigation').textContent+=event.message+'\n';
      return;
    }
    investigationErrorContext={otaStep:event.stage==='identity'?'Check target identity':event.stage==='waiting-login'||event.stage==='sending-login'?'Log in to remote target':'Check remote device',otaCommand:event.command,otaTimeoutMs:event.timeoutMs,otaAttempt:event.attempt,otaMaxAttempts:event.maxAttempts};
    const labels={
      'checking-clock':'Checking Companion clock...',
      'refreshing-contacts':'Refreshing contacts...',
      'checking-acl':'Checking existing access without a password...',
      'sending-login':'Sending login...',
      'waiting-login':'Waiting for login response...',
      identity:'Checking target identity...',
      'sending-command':'Sending request to target...',
      'command-sent':'Waiting for target reply...',
      'waiting-reply':'Waiting for target reply...',
      'waiting-inbox':'Waiting for the USB Companion to finish reading a message...',
      'retrying-command':'No reply; retrying...'
    };
    const label=labels[event.stage];if(!label)return;
    const command=typeof event.command==='string' && /^[a-z0-9. ]{1,40}$/i.test(event.command) ? ' ('+event.command+')' : '';
    if(event.stage==='waiting-login')
      $('investigation').textContent+=event.command==='passwordless login' ? '> login (no password)\n' : '> login (password hidden)\n';
    const attempt=Number.isInteger(event.attempt) && event.attempt>0 && Number.isInteger(event.maxAttempts) && event.maxAttempts>=event.attempt
      ? ' (attempt '+event.attempt+' of '+event.maxAttempts+')' : '';
    if(event.stage==='command-sent' && command)
      $('investigation').textContent+='> '+event.command+attempt+'\n';
    if(event.stage==='retrying-command' && command)
      $('investigation').textContent+='No reply to '+event.command+attempt+'; retrying.\n';
    showInvestigationProgress(label+command+attempt,event.timeoutMs);
  }
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
        return parts.filter(Boolean).join('  /  ')+'\n'+String(message.text||message.warning||'[Raw message included in download]');
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
  function usesSharedCompanionInbox() {
    return sourceKind()==='companion' && link?.otaSupported===false;
  }
  function clearInvestigation() {
    globalThis.LoraOtaSession?.clearInstallCapability?.(link);
    quietPlan=null;
    $('full-rebuild').checked=false;
    targetRadioHwId=null;
    targetAvailability=null;
    showAvailability(sourceOtaUnsupported ? {state:'unsupported',message:'LoRa updates are unavailable with this USB Companion: its firmware cannot send OTA packets. Update the source Companion firmware first.'} : null);
    $('investigation').hidden = true; $('investigation').textContent = '';
    $('investigation-details').hidden=true;
    finishInvestigationProgress('');$('investigation-progress').hidden=true;
    $('investigation-stop').hidden=true; investigationStopping=false;
    $('legacy-warning').hidden = !usesSharedCompanionInbox();
    $('legacy-source-block').hidden = true;
    catalogGeneration++; catalogFile=null; catalogBaseFile=null; catalogDest=null; catalogVersion=''; catalogInstallSupported=false; catalogCandidates=[];
    $('firmware-role-wrap').hidden=true; $('firmware-role').value='';
    $('firmware-choice-wrap').hidden=true; $('firmware-choice').replaceChildren();
    $('firmware-download').hidden=true; $('firmware-download').disabled=true;
    $('firmware-catalog-status').textContent='Select the remote device, then choose Find matching firmware.';
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
    $('contact-status').textContent=sourceKind()!=='companion'
      ? (link ? 'Repeater connected. Select Find matching firmware to check the remote device.' : 'Enter the remote device key, then connect the USB Repeater.')
      : !link ? 'Connect a Companion to load contacts.' :
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
  $('password').addEventListener('input',()=>{clearInvestigation();invalidate();busy(uiBusy);});
  $('acl').addEventListener('change',()=>busy(uiBusy));
  $('file').addEventListener('change',()=>{
    catalogFile=null;$('full-rebuild').checked=false;
    if($('file').files[0])$('firmware-catalog-status').textContent='Using your selected firmware file. Choose Prepare update.';
  });
  $('base').addEventListener('change',()=>{
    $('firmware-base-status').textContent=$('base').files[0] ? 'Using your selected installed firmware file.' :
      catalogBaseFile ? 'Using the downloaded installed firmware matching the reported target hash.' : '';
  });
  $('source').addEventListener('change',()=>{
    if(!link)activeSourceKind=null;
    $('source-update-help').hidden=true;clearInvestigation();
    if(!link){$('target-controls').hidden=sourceKind()==='companion';renderContacts();}
    if(sourceKind()==='repeater')openDetails($('key'));
  });
  $('acl').checked=false;
  function selectTarget() {
    const key=$('key').value.trim();
    if(!/^[0-9a-f]{64}$/i.test(key))throw Error('Choose a contact or enter the complete target public key.');
    if(!targetLocked && typeof link.selectTarget==='function')link.selectTarget(key);
    else if(link.knownRepeater && link.knownRepeater!==key.toLowerCase())throw Error('Disconnect before choosing another target.');
    if(sourceKind()==='repeater' && link.options?.deferredTarget && !link.relays?.length)
      link.aclOnly=$('acl').checked;
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
    if(disconnecting)return;
    disconnecting=true;
    try{
    disconnectGeneration++;
    if (connectController) connectController.abort();
    if (session) session.stop();
    if (investigationActive) link?.cancelInvestigation?.();
    if (preparing) preparing.abort();
    if (activeTask) { log('Waiting for the current radio request to finish...'); await activeTask.catch(() => {}); }
    await restoreQuietForwarding();
    if (link) await link.close();
    sourceOtaUnsupported=false;
    link = session = chainPrepared = chainConfig = autoChain = null;autoStep='';contacts=[];targetLocked=false;contactKey='';activeSourceKind=null;radioOverride=false;
    transferStopJob=transferStopTask=null;
    for(const [name,value] of Object.entries(radioDefaults))$(name).value=value;
    $('radio-reset').hidden=true;
    $('radio-region-status').textContent='Detected from recognized radio settings. Choose a region to override.';showRadioCommands();
    $('source-info').hidden=true; $('source-info').textContent='';
    $('manual-contact-help').hidden=true;
    preparedDownload=null;status('Disconnected. Connect your USB radio to start another update.', 'ready');
    $('key').value='';$('password').value='';$('contact-search').value='';clearInvestigation();renderContacts();busy(false); $('connect').disabled = false; $('inspect').disabled = true; $('find-route').disabled = true;
    $('chain-plan').disabled = true; $('chain-run').disabled = true; $('chain-resume').disabled = true;
    $('transfer').disabled = true; $('resume').disabled = true; $('install').disabled = true;
    }finally{disconnecting=false;}
  }
  async function reconnect({automatic=false}={}) {
    if(disconnecting||uiBusy&&!automatic || !link?.failure)return false;
    const previous=link, sourceKey=link.sourceKey, port=link.port, kind=sourceKind();
    if(!port || !sourceKey)throw Error('Disconnect, then connect the USB source again.');
    const generation=disconnectGeneration,target=$('key').value.trim().toLowerCase();
    const profileRequired=kind==='companion'&&(previous.otaRadioProfile===1||previous.otaRecoveryProfilePending===true);
    const profile=globalThis.LoraOtaRecovery?.captureSourceProfile?.(previous,target);
    busy(true);invalidate();session=null;
    await previous.close();
    if(generation!==disconnectGeneration||link!==previous)return false;
    link=null;targetLocked=false;
    await connect({port,sourceKey,kind,target,profile,profileRequired,automatic});
    return generation===disconnectGeneration&&link?.port===port&&link.sourceKey===sourceKey&&!link.failure;
  }
  async function connect(recovery=null) {
    const controller = connectController = new AbortController();
    let detectedSource=null;
    $('source-update-help').hidden=true;
    $('connect').disabled = true; $('connect').textContent = 'Connecting...'; busy(true);
    $('contact-status').textContent = 'Choose a USB device...';
    try {
      if (!navigator.serial) throw Error('USB access is unavailable. Open this page in Chrome or Edge on a computer; Firefox does not support this USB connection.');
      const selected=recovery?.kind || $('source').value, key = $('key').value.trim();
      if (key && !/^[0-9a-f]{64}$/i.test(key)) throw Error('Enter the target\'s complete public key.');
      if (selected==='repeater' && !key && !recovery) throw Error('Enter the target\'s complete public key.');
      const relays = $('relays').value.trim() ? $('relays').value.trim().split(/[\s,]+/) : [];
      if (relays.length && !aclIntent()) throw Error('Routed updates require existing ACL access on every relay and the target. Leave the password blank or choose the ACL-only override.');
      if (relays.some(r => !/^[0-9a-f]{64}$/i.test(r)) || relays.length > 8) throw Error('Enter up to eight full relay public keys.');
      if (relays.length && !key) throw Error('Enter the target\'s complete public key for a routed update.');
      const port = recovery?.port || await LoraOtaPort.requestSerialPort(navigator.serial, {signal:controller.signal});
      if (controller.signal.aborted) throw new DOMException('Connection canceled', 'AbortError');
      $('contact-status').textContent = 'Connecting to radio...';
      const openOptions = {openSignal:controller.signal, onOpenAttempt:(attempt,maxAttempts)=>{
        if(!controller.signal.aborted) $('contact-status').textContent='Opening USB port (attempt '+attempt+' of '+maxAttempts+')...';
      }, onOpenSuccess:()=>{
        if(!controller.signal.aborted) $('contact-status').textContent='Connecting to radio...';
      }};
      if(selected==='auto'){
        $('contact-status').textContent='Detecting the USB radio...';
        detectedSource=await LoraOtaPort.detectSource(port,{...openOptions,signal:controller.signal});
        activeSourceKind=detectedSource.kind;openOptions.detectedSource=detectedSource;
      }else activeSourceKind=selected;
      const companion=sourceKind()==='companion';
      if (companion && relays.length) throw Error('Companion updates currently require a zero-hop connection.');
      const deferredTarget=!companion&&!key;
      const direct = companion
        ? new LoraOtaCompanion.CompanionTransport(port, {...openOptions, remoteTimeout:75000, onPreserved:refreshPreservedMessages, onInvestigationProgress, onFolderRead, onFolderBootstrap, onFolderAdmission})
        : new LoraOtaSerial.RepeaterTransport(port, {...openOptions, knownRepeater:key||null, deferredTarget, aclOnly:!!relays.length||!deferredTarget&&$('acl').checked, remoteTimeout:75000});
      link = relays.length ? new LoraOtaRoute.RoutedRepeaterTransport(direct, key, relays) : direct;
      await link.open();
      if(recovery && link.sourceKey!==recovery.sourceKey)throw Error('The reconnected USB radio has a different identity. Disconnect and choose the intended source.');
      if (controller.signal.aborted) throw new DOMException('Connection canceled', 'AbortError');
      showSourceUpdate(link.sourceUpdateRequired);
      $('legacy-warning').hidden=!usesSharedCompanionInbox();
      if(companion){
        sourceOtaUnsupported=link.rawPacketSupport==='unsupported';
        if(sourceOtaUnsupported) {
          showAvailability({state:'unsupported',message:'LoRa updates are unavailable with this USB Companion: its firmware cannot send OTA packets. Update the source Companion firmware first.'});
          showSourceUpdate('companion');
        }
        const sourceVersion=link.firmwareVersion ? link.firmwareVersion+(link.firmwareVersionTruncated?'...':'') : 'version unavailable';
        const sourceMode=link.rawPacketSupport==='unsupported' ? 'OTA packet sending unsupported' :
          link.usbFolderSupported && link.usbRawFrameMax<188 ? 'USB firmware-source compatibility selected automatically' :
          link.otaMode==='guarded' ? 'guarded OTA selected automatically' : 'raw-packet OTA compatibility selected automatically';
        $('source-info').textContent='Companion firmware: '+sourceVersion+'  /  '+sourceMode+'.';
        $('source-info').hidden=false;
        $('contact-status').textContent = 'Loading contacts...';
        contacts=await link.contacts();
        if (controller.signal.aborted) throw new DOMException('Connection canceled', 'AbortError');
        renderContacts();
        // Expand the fallback once after loading an empty eligible contact
        // list. Search filtering must not reopen it after the user closes it.
        const noEligibleContacts=!LoraOtaContacts.choices(contacts).length;
        $('manual-contact-help').hidden=link.otaSupported || !noEligibleContacts;
        if(noEligibleContacts)openDetails($('key'));
        log('Loaded '+LoraOtaContacts.choices(contacts).length+' eligible contacts.');
        if(link.rawPacketSupport==='unsupported')log('This Companion does not support sending raw OTA packets.');
        else if(link.usbFolderSupported && link.usbRawFrameMax<188)log('Using this Companion\'s existing USB firmware-source mode automatically; no source firmware update is needed.');
        else if(link.otaMode!=='guarded' && link.rawPacketSupport!=='supported')log('Using raw-packet compatibility. Packet-send support will be checked when the transfer starts.');
      } else {
        $('manual-contact-help').hidden=true;
        $('source-info').textContent='USB Repeater detected. The update will be sent over LoRa.';
        $('source-info').hidden=false;
        $('contact-status').textContent = key ? 'Repeater connected. Select Find matching firmware to check the remote device.' : 'Repeater connected. Enter the remote device key, then select Find matching firmware.';
        $('target-controls').hidden=false;
        if(!key)openDetails($('key'));
      }
      if(recovery?.profileRequired){
        const selected=recovery.profile&&recovery.target===key.toLowerCase()
          ?await LoraOtaRecovery.restoreSourceProfile(link,key,recovery.profile,{progress:log}):null;
        link.otaRecoveryProfilePending=selected?.state!=='selected';
        if(selected?.state==='selected')
          log('The same USB source still has the verified update channel. Target identity will be checked before any forwarding restoration.');
        else log('USB is connected, but the previous temporary update channel is unconfirmed. No target command or channel rewrite was sent.');
      }
      await suggestRadioChannel();
      busy(false); $('connect').disabled = true; $('inspect').disabled = updatesBlocked();
      $('chain-plan').disabled = updatesBlocked();
      $('find-route').disabled = !repeaterAclIntent() || !link.supportsRoutedPath || !!link.relays;
      log('Connected to source ' + link.sourceKey + '.');
      status(recovery ? 'USB reconnected. Select Prepare update to recheck the radios, or Find matching firmware to check the device.' : 'Connected. Choose the remote device and select Find matching firmware.', 'ready');
    } catch (error) {
      if(!recovery?.automatic&&!controller.signal.aborted && error?.name!=='NotFoundError')await fail(error);
      if (link) { try { await link.close(); } catch (_) {} }
      link = session = null;activeSourceKind=null; $('connect').disabled = false; $('inspect').disabled = true;
      $('manual-contact-help').hidden=true;
      sourceOtaUnsupported=false;targetAvailability=null;showAvailability(null);
      $('legacy-warning').hidden=true;
      $('source-info').hidden=true; $('source-info').textContent='';
      $('contact-status').textContent = controller.signal.aborted || error?.name === 'NotFoundError'
        ? 'USB connection canceled. Choose a USB device to try again.'
        : error?.usbOpenFailed
          ? 'Could not open the USB port. It may be in use by another app or browser tab. Disconnect the radio there and try again. If it still fails, reconnect its USB data cable.'
          : failureMessage(error)+'\nTry again.';
      if(recovery?.automatic)throw error;
    } finally {
      await detectedSource?.close();
      if(connectController===controller)connectController=null;
      $('connect').textContent = 'Connect USB source'; busy(false);
    }
  }
  async function loadCatalogCandidate(candidate, generation) {
    requireUpdatesAvailable();
    catalogFile=null;$('full-rebuild').checked=false; invalidate();
    $('firmware-catalog-status').textContent='Downloading and checking firmware...';
    const result=await LoraOtaCatalog.load(candidate,catalogDest);
    if(generation!==catalogGeneration || !link)throw Error('Target changed while firmware was downloading');
    if(!(result.bytes instanceof Uint8Array) || !result.bytes.length || !/\.bin$/i.test(result.name))throw Error('The downloaded firmware is invalid');
    catalogFile=new File([result.bytes],result.name,{type:'application/octet-stream'});
    $('file').value=''; invalidate();
    const deltaHelp=catalogDest.nrf && !catalogDest.external && !catalogDest.sd
      ? ($('base').files[0] || catalogBaseFile ? ' An installed image is selected for the required in-place differential.'
        : ' This target needs an in-place differential; select the exact installed firmware under local file options.') : '';
    $('firmware-catalog-status').textContent='Downloaded '+result.name+'; hardware identity verified. Select Prepare update.'+deltaHelp;
    if(catalogDest.nrf && !catalogDest.external && !catalogDest.sd && !catalogBaseFile && !$('base').files[0])openDetails($('base'));
    status('Matching firmware loaded. Select Prepare update to check the device and channel.', 'ready');
  }
  async function discoverCatalog(role) {
    requireUpdatesAvailable();
    const generation=catalogGeneration;
    if(!catalogDest || !role)return;
    if(catalogDest.nrf && !catalogDest.sd && (catalogDest.codecs & 4) && !$('base').files[0] && typeof LoraOtaCatalog.findInstalled==='function') {
      const baseHelp=catalogDest.external ? 'You can select it manually to make a differential.' : 'Select it manually for the required differential.';
      $('firmware-base-status').textContent='Looking for the installed firmware...';
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
    $('firmware-catalog-status').textContent='Finding matching firmware...';
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
    if(!candidates.length){$('firmware-catalog-status').textContent='No matching newer firmware found. You can choose a file manually.';return;}
    const recommended=typeof LoraOtaCatalog.recommendVerified==='function'
      ? await LoraOtaCatalog.recommendVerified(candidates,catalogDest) : null;
    if(generation!==catalogGeneration || !link)return;
    if(recommended && !$('file').files[0]){
      const index=candidates.indexOf(recommended);
      if(index<0)throw Error('Invalid firmware recommendation');
      select.value=String(index);
      await loadCatalogCandidate(recommended,generation);
    }else{
      $('firmware-catalog-status').textContent=$('file').files[0] ? 'Using your selected firmware file. Matching downloads are also listed.' : 'Choose a matching firmware version, then select Load selected firmware.';
    }
  }
  async function investigate() {
    const investigationLink=link,investigationConnection=disconnectGeneration;
    const standard = usesSharedCompanionInbox();
    status('Checking the remote device and finding compatible firmware...');
    clearInvestigation(); invalidate(); busy(true); $('inspect').disabled = true;
    const output = $('investigation'), replies = {};
    let target = '', complete = false, verified = false, stopped = false, failed = false;
    let repeaterTimeout = null, restoreRepeaterTimeout = false;
    let investigationTimeoutMs = 20000;
    output.hidden = false;$('investigation-details').hidden=false;
    output.textContent = 'Investigating target...\n';
    $('investigation-progress').hidden=false;
    investigationStopping=false;investigationActive=true;
    investigationErrorContext=null;
    $('investigation-stop').hidden=!(sourceKind()==='companion' && typeof link?.cancelInvestigation==='function');
    $('investigation-stop').disabled=false;
    showInvestigationProgress('Preparing target connection...');
    const add = text => { output.textContent += text + '\n'; };
    const checks = [
      ['OTA status', 'ota status'], ['OTA storage and image', 'ota self'],
      ['Firmware version', 'ver'], ['Bootloader version', 'get bootloader.ver'],
      ['Bootloader capabilities', 'ota bootloader status'], ['OTA settings', 'ota config'],
      ['Trusted OTA keys', 'ota key list'], ['Temporary radio', 'get tempradio'],
      ['Radio power saving', 'get radio.rxps'],
      ['Install verification', 'ota installhash']
    ];
    let next = 0, allReplies = true, targetReplied = false, esp32Target;
    const noPassword = $('acl').checked || $('password').value.length === 0;
    const password = $('acl').checked ? '' : $('password').value;
    const companion = sourceKind() === 'companion';
    const checkStopped=()=>{if(investigationStopping)throw Error('Investigation stopped by user');};
    // A completed repeater job timeout can be retried; a serial or browser-side
    // job timeout cannot, because the previous operation may still be running.
    const repeaterRead = async (description, operation, canRetry) => {
      const maxAttempts=targetReplied ? 11 : 7;
      for(let attempt=1;attempt<=maxAttempts;attempt++) {
        showInvestigationProgress(description+' (attempt '+attempt+' of '+maxAttempts+')',investigationTimeoutMs);
        add('> '+description+' (attempt '+attempt+' of '+maxAttempts+')');
        try { return await operation(); }
        catch(error) {
          if(error?.message!=='request timed out; result is unknown' || !canRetry())throw error;
          if(attempt===maxAttempts){error.attempts=attempt;error.loraReplyTimeout=true;throw error;}
          add('No reply; retrying.');
        }
      }
    };
    const readCommand = command => companion
      ? link.investigateCommand(target, password, command)
      : repeaterRead(command, () => link.remoteCommand(target, password, command),
          () => noPassword || (link.authenticatedKey===target && Date.now()-link.lastPeerUse<240000));
    const exhaustedTimeout = error => (error?.attempts===7 || error?.attempts===11) && error.loraReplyTimeout===true && !link.failure &&
      (error.message==='Remote command timed out; result is unknown' || error.message==='request timed out; result is unknown');
    const sourceTimeout = async value => {
      const response = String(await link.localCommand('ota.peer timeout'+(value===undefined?'':' '+value))).trim();
      const match = response.match(/^OTApeer timeout=(\d+)s$/i);
      if(!match || Number(match[1])<5 || Number(match[1])>300 || (value!==undefined && Number(match[1])!==value))throw Error('Could not verify the source timeout setting');
      return Number(match[1]);
    };
    try {
      if (!link) throw Error('Connect a source radio first.');
      requireUpdatesAvailable();
      target = selectTarget().toLowerCase();
      const recovered=await recoverUpdateChannel(target);
      if(!companion) {
        if(!link.relays?.length && await link.localCommand('ota.peer path')==='OTApeer path=off')investigationTimeoutMs=7000;
        showInvestigationProgress('Setting the source reply timeout...');
        repeaterTimeout = await sourceTimeout();
        const seconds=investigationTimeoutMs/1000;
        if(repeaterTimeout!==seconds) {
          // The write might reach the source even if its acknowledgement is lost.
          restoreRepeaterTimeout = true;
          await sourceTimeout(seconds);
          if(await sourceTimeout()!==seconds)throw Error('Could not verify the source timeout setting');
        }
      }
      if(standard) {
        add('Companion: using the shared message queue and raw packets. Keep other apps disconnected during the update.');
        await link.enableStandardInvestigation();
        showInvestigationProgress(link.investigationAuthenticated ? 'Checking target identity...' : noPassword ? 'Checking existing access without a password...' : 'Logging in and checking target identity...');
        await link.investigateVerifyPeer(target, password);
      } else if(companion) {
        showInvestigationProgress(link.authenticated ? 'Checking target identity...' : noPassword ? 'Checking existing access without a password...' : 'Logging in and checking target identity...');
        await link.investigateVerifyPeer(target, password);
      } else {
        if(sourceKind()!=='companion' && !link.authenticated && !noPassword)add('> login (password hidden)');
        showInvestigationProgress(link.authenticated ? 'Checking target identity...' : noPassword ? 'Checking existing access without a password...' : 'Logging in and checking target identity...');
        if(noPassword) await repeaterRead('Checking target identity', () => link.verifyPeer(target, password), () => true);
        else await link.verifyPeer(target, password);
      }
      checkStopped();targetReplied=true;verified = !standard || link.investigationVerified===true;
      if(verified)await confirmUpdateChannel(recovered,target,password);
      add(verified ? 'Identity: full public key verified (' + target.slice(0, 12).toUpperCase() + ')' :
        'Identity not fully verified: this target does not support the full-key check. Results are diagnostics only; firmware selection and updates are disabled.');
      if(standard) {
        add('Account access: '+(link.investigationPermission||'unknown'));
        add('Disconnect the Companion to choose another target.');
      }
      for (; next < checks.length; next++) {
        checkStopped();
        const [label, command] = checks[next];
        if(command==='ota bootloader status' && replies[command]) {
          add(label+': '+replies[command]);
          continue;
        }
        if(command==='get bootloader.ver' || command==='ota bootloader status') {
          if(esp32Target===undefined)esp32Target=await LoraOtaChecks.isKnownEsp32(replies['ota status']||'',replies['ota self']||'');
          if(esp32Target){add(label+': Skipped (ESP32)');continue;}
        }
        showInvestigationProgress('Checking '+label.toLowerCase()+'...');
        let reply;
        try { reply = String(await readCommand(command)).trim(); }
        catch(error) {
          if(exhaustedTimeout(error)) {
            allReplies=false; replies[command]=''; add(label+': No reply after '+error.attempts+' attempts; not checked'); continue;
          }
          throw error;
        }
        checkStopped();replies[command] = reply;
        const normalized = reply.replace(/^>\s*/, '');
        if (command === 'ota installhash') {
          catalogInstallSupported=normalized === 'ERR usage: ota installhash <64-hex-container-sha256>';
          add(label + ': ' + (catalogInstallSupported
            ? 'Full container hash verification supported' : 'Full container hash verification not confirmed (' + (normalized || 'empty response') + ')'));
          if(!catalogInstallSupported)add('Older firmware can use its legacy installer; the staged container cannot be bound to the full hash before installation.');
        } else {
          const unknown = !normalized || /^(?:err(?:or)?\b|unknown\b|unsupported\b)/i.test(normalized);
          add(label + ': ' + (unknown ? 'Unknown or unsupported (' + (normalized || 'empty response') + ')' : normalized));
        }
        if(command==='ota status' || command==='ota self') {
          // Idle adaptive RAKs can report an unprobed store as an error. Read
          // the image identity before deciding whether that report is real.
          // A browser may briefly have the preceding checks script cached.
          const hasColdRakChecks=typeof LoraOtaChecks.coldRakStatus==='function' &&
            typeof LoraOtaChecks.prepareColdRakStorage==='function';
          if(command==='ota status' && hasColdRakChecks && LoraOtaChecks.coldRakStatus(replies['ota status'])) {
            add('Checking whether update storage needs preparation.');
            continue;
          }
          if(command==='ota self' && hasColdRakChecks && LoraOtaChecks.coldRakStatus(replies['ota status'])) {
            showInvestigationProgress('Checking update storage...');
            const checked = await LoraOtaChecks.prepareColdRakStorage(async request => {
              checkStopped(); const result = await readCommand(request); checkStopped(); return result;
            }, replies['ota status'], replies['ota self']);
            if(checked.prepared) {
              replies['ota status']=checked.status; replies['ota self']=checked.self;
              replies['ota bootloader status']=checked.bootloader;
              add('Update storage is ready.');
            }
          }
          const assessment=LoraOtaChecks.availability(replies['ota status'],command==='ota self' ? replies['ota self'] : undefined);
          if(command==='ota self' || assessment.state==='unsupported' || assessment.state==='unavailable') {
            targetAvailability=assessment;showAvailability(assessment);
            if(assessment.state==='unsupported' || assessment.state==='unavailable') {
              next++;
              const error=Error(assessment.message);error.otaAvailability=assessment;throw error;
            }
          }
        }
      }
      if (/\bqspi\b/i.test((replies['ota status'] || '') + ' ' + (replies['ota self'] || ''))) {
        try {
          checkStopped();
          showInvestigationProgress('Checking external flash...');
          const qspi = String(await readCommand('ota qspi')).trim();
          replies['ota qspi'] = qspi;
          const normalized = qspi.replace(/^>\s*/, '');
          add('External flash: ' + (!normalized || /^(?:err(?:or)?\b|unknown\b|unsupported\b)/i.test(normalized)
            ? 'Unknown or unsupported (' + (normalized || 'empty response') + ')' : normalized));
        } catch(error) {
          if(!exhaustedTimeout(error))throw error;
          allReplies=false; replies['ota qspi']=''; add('External flash: No reply after '+error.attempts+' attempts; not checked');
        }
      }
      complete = allReplies;
      checkStopped();$('investigation-stop').hidden=true;
      if(verified && !updatesBlocked())try {
        catalogDest=LoraOtaChecks.destination(replies['ota status'],replies['ota self'],replies.ver);catalogVersion=replies.ver;
        if(link===investigationLink&&disconnectGeneration===investigationConnection&&
            typeof LoraOtaSession.rememberInstallCapability==='function')
          LoraOtaSession.rememberInstallCapability(link,target,password,catalogDest,replies['ota installhash']);
        catalogDest.explicitMaxBlock=LoraOtaChecks.explicitMaxBlock(replies['ota status']+' '+replies['ota self']);
        if(catalogDest.nrf && !catalogDest.external && !catalogDest.sd)
          add('Update method: this target needs an in-place differential.');
        const contact=contacts.find(c=>String(c.publicKey).toLowerCase()===target);
        const role=({2:'repeater',3:'room',4:'sensor'})[contact?.type];
        if(!role){
          $('firmware-role-wrap').hidden=false;
          $('firmware-catalog-status').textContent='Choose the target type to find matching firmware.';
        }else if(!globalThis.LoraOtaCatalog){
          $('firmware-catalog-status').textContent='Firmware catalog is unavailable. Choose a file manually.';
        }else { investigationErrorContext=null;operationErrorStep='Find and download matching firmware';showInvestigationProgress('Finding matching firmware...'); await discoverCatalog(role); }
      } catch(error) {
        $('firmware-catalog-status').textContent=failureMessage(error)+'\nYou can choose a file manually.';
        openDetails($('file'));
      } else $('firmware-catalog-status').textContent=updatesBlocked() ? $('ota-availability').textContent : 'Target identity is not fully verified; firmware selection and updates are disabled.';
    } catch (error) {
      stopped=investigationStopping && /^(?:Investigation canceled\.|Investigation stopped by user)/.test(error?.message||'');
      failed=!stopped;
      if(!stopped && !targetAvailability && !sourceOtaUnsupported) {
        targetAvailability={state:'unknown',message:'LoRa update support could not be confirmed. Check the target\'s access and connectivity, then select Find matching firmware.'};
        showAvailability(targetAvailability);
      }
      if(updatesBlocked())$('firmware-catalog-status').textContent=$('ota-availability').textContent;
      if(stopped)add(standard ? 'Investigation stopped. Unrelated messages already received are saved in this browser.' : 'Investigation stopped. Disconnect the Companion before trying again.');
      else add((verified ? 'Check interrupted: ' : 'Identity not verified: ') + failureMessage(error));
      if(/(?:login|remote command|target reply|request).*timed out/i.test(error?.message||''))
        add(noPassword
          ? 'No reply received. Check that the target is awake and reachable, the radio settings, path and source clock are correct, and the source radio key is in the target ACL; or enter an admin password and retry.'
          : 'No reply received. Check that the target is awake and reachable, and verify the radio settings, path, admin password and Companion clock before trying again.');
      if(standard && link?.investigationEnabled)add('Disconnect the Companion before choosing another target or retrying the login.');
      for (; next < checks.length; next++) add(checks[next][0] + ': Not checked');
      await fail(error);
    } finally {
      if(restoreRepeaterTimeout) {
        try {
          showInvestigationProgress('Restoring the source reply timeout...');
          await sourceTimeout(repeaterTimeout);
          if(await sourceTimeout()!==repeaterTimeout)throw Error('Timeout verification failed');
        } catch(error) {
          failed=true;complete=false;invalidate();
          add('Could not restore the source timeout to '+repeaterTimeout+' seconds. Reconnect and check `ota.peer timeout` before updating.');
        }
      }
      if(standard) {
        refreshPreservedMessages();
      }
      add(updatesBlocked() ? 'Firmware selection and updates are disabled until LoRa update support is confirmed.' : verified ? 'Next: select firmware, then Prepare update. A successful check enables Update remote firmware.' : 'Firmware compatibility: not checked because the target identity was not fully verified.');
      finishInvestigationProgress(stopped ? 'Investigation stopped.' : updatesBlocked() ? (targetAvailability?.state==='unknown' ? 'LoRa update support not confirmed.' : 'LoRa updates unavailable.') : failed ? 'Investigation stopped with an error.' : complete ? 'Investigation finished.' : 'Investigation finished with incomplete checks.');
      $('investigation-stop').hidden=true;
      investigationActive=false;
      investigationErrorContext=null;
      // Announce completion only after the action releases its UI ownership.
      const finished=()=>ui.dispatchEvent(new CustomEvent('lora-ota-investigated', {detail:{target,replies,complete,verified}}));
      if(activeTask)activeTask.then(finished);else finished();
      if(!failed && !stopped && !updatesBlocked())status(catalogFile ? 'Matching firmware loaded. Select Prepare update.' : $('firmware-catalog-status').textContent, 'ready');
      if(stopped)status(link?.failure ? 'Device check stopped. Select Reconnect USB source to continue.' : 'Device check stopped.');
      busy(false); $('inspect').disabled = !link || updatesBlocked();
    }
  }
  async function findRoute() {
    invalidate(); busy(true); $('find-route').disabled = true; $('inspect').disabled = true;
    let aclLink=null,previousAclOnly;
    try {
      if (!link || link.relays || !repeaterAclIntent() || !link.supportsRoutedPath) throw Error('Route search requires existing ACL access. Leave the password blank or choose the ACL-only override.');
      const target = selectTarget().toLowerCase();
      const candidates = $('candidates').value.trim() ? $('candidates').value.trim().split(/[\s,]+/).map(k => k.toLowerCase()) : [];
      const allowlist = [link.sourceKey, target, ...candidates];
      if (allowlist.some(k => !/^[0-9a-f]{64}$/.test(k)) || new Set(allowlist).size !== allowlist.length) throw Error('Enter unique full keys for candidate repeaters.');
      const minSnrDb = Number($('snr').value), hours = Number($('age').value);
      if (!Number.isFinite(minSnrDb) || !Number.isFinite(hours) || hours <= 0 || hours > 168) throw Error('Enter a valid SNR floor and observation age.');
      log('Checking cached neighbor observations and ACL access...');
      aclLink=link;previousAclOnly=link.aclOnly;link.aclOnly=true;
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
    } catch (error) { await fail(error); }
    finally {
      if(aclLink&&link===aclLink)aclLink.aclOnly=previousAclOnly;
      busy(false); $('inspect').disabled = !link || updatesBlocked();
    }
  }
  const autoCacheKey=(target,goalHash)=>link.sourceKey.toLowerCase()+':'+target.toLowerCase()+':'+goalHash;
  const autoJournalKey=(key,kind)=>'meshcore-browser-lora-ota-auto-'+kind+':'+key;
  const autoRead=(key,kind)=>{
    if(kind==='step')return readStepRecord(autoJournalKey(key,kind));
    try{return JSON.parse(localStorage.getItem(autoJournalKey(key,kind)));}catch(_){return null;}
  };
  const autoWrite=(key,kind,value)=>localStorage.setItem(autoJournalKey(key,kind),JSON.stringify(value));
  function autoSession(step,target,password,key){
    const Session=link.relays?.length?LoraOtaRoutedSession.RoutedSession:LoraOtaSession.DirectSession;
    return new Session(link,target,password,step.package.info,{image:step.targetImage,inplacePlan:step.edge.inplacePlan,deferSecondaryRestoration:true,
      allowSameVersion:step.edge.allowSameVersion===true,compress:supportedCompression(),progress:log,onStage:sessionStage,
      journal:state=>autoWrite(key,'step',state),readJournal:()=>autoRead(key,'step')});
  }
  async function readyAutomatic(prepared,target,password,key,file,cached=false){
    const record=autoRead(key,'chain'),resume=!!record&&record.phase!=='complete';
    const index=resume?record.index:0;
    if(!Number.isInteger(index)||!prepared.steps?.[index])throw Error('The saved intermediate update step is invalid');
    const step=prepared.steps[index];session=autoSession(step,target,password,key);
    let disposition;
    try{disposition=await session.inspect({allowExisting:true,allowReplacement:true});}
    catch(error){
      if(!error.radioSetupRequired||!canPrepareRadioChannel())throw error;
      if(preparing?.signal.aborted)throw Error('Update preparation cancelled. No firmware was uploaded.');
      await ensureRadioChannel(target,password);disposition=await session.inspect({allowExisting:true,allowReplacement:true});
    }
    if(preparing?.signal.aborted)throw Error('Update preparation cancelled. No firmware was uploaded.');
    if(resume&&index===0&&record.phase==='step starting'&&autoRead(key,'step')==null&&disposition.download!=='idle')
      throw Object.assign(Error('The first update step has no saved transfer record, and the target is not idle. Its staged update was left unchanged; no pull or install was sent.'),
        {otaLocalCheck:true,otaLocalStateCheck:true,otaEndpoint:'Browser',otaStep:'Check initial update-step recovery state'});
    if(disposition.download!=='installed')await pauseQuietForwarding(target,password,session.verifiedRadio);
    autoChain={prepared,target,key,resume,replacement:disposition.download==='replace'?disposition.existingDownload:null};
    preparedDownload='chain';
    const totals=prepared.route.totals,estimate=Math.ceil(totals.radioDataBytes/1024);
    $('update-summary').textContent=file.name+': '+prepared.steps.length+' automatic update steps'+
      (Number.isFinite(estimate)?', about '+estimate+' KiB of radio data':'')+'. The browser handles each transfer, reboot and verification.';
    const a=prepared.alternatives;
    if(a&&a.data.installs!==a.steps.installs)log('Path comparison: fewest steps = '+a.steps.installs+' installs / '+
      Math.ceil(a.steps.bytes/1024)+' KiB; least data = '+a.data.installs+' installs / '+Math.ceil(a.data.bytes/1024)+' KiB. These estimates exclude retries.');
    log((cached?'Recovered':'Prepared')+' automatic update path: '+prepared.steps.length+' verified differential packages.');
    if(autoChain.replacement)$('update-summary').textContent+=' Starting will replace the unfinished update on the target.';
    status('Ready. Select '+(resume?'Continue firmware update':'Update remote firmware')+'.','ready');
    // Separate transfer/install controls apply to one package; the main action
    // owns the whole sequence, including reboots and saved recovery journals.
    for(const name of ['transfer','resume','install'])$(name).disabled=true;
  }
  async function prepareAutomatic(base,input,destination,target,password,file,signal,diagnostics){
    if(!destination.nrf||destination.external||destination.sd){
      diagnostics.search.result='Not applicable to this target storage';return false;
    }
    diagnostics.search.result='Loading the published intermediate catalog';
    status('Looking for a smaller update path...');
    const index=await LoraOtaAutoChain.lookup(base,input,{signal});
    if(!index){
      diagnostics.search.result='No published intermediates for these exact installed and selected image hashes';
      diagnostics.search.candidates=0;return false;
    }
    const route=index.routes.find(r=>r.from===diagnostics.installed.imageHash&&r.to===diagnostics.selected.imageHash);
    diagnostics.search.candidates=route?.images.length;
    const caps=await LoraOtaChainUi.readLive(link,target,password);
    diagnostics.ram={...caps.runtimeHybridRamDiagnostics,confirmed:!!caps.runtimeHybridRam,reason:caps.runtimeHybridRam?
      'Confirmed by the live target RAM or pinned bootloader check':caps.runtimeHybridRamError||'Retained RAM support was not confirmed'};
    diagnostics.search.result='Comparing published intermediate images';
    const prepared=await LoraOtaAutoChain.prepare(base,input,caps,{signal,objective:$('auto-objective').value,deep:$('auto-depth').value==='deep',
      index,relays:link.relays?.length||0,progress:message=>{status(message);log(message);}});
    if(!prepared)return false;
    diagnostics.search.result='Found '+prepared.steps.length+' fitting update steps';
    await readyAutomatic(prepared,target,password,autoCacheKey(target,prepared.goalHash),file);
    return true;
  }
  async function runAutomatic(){
    const context=autoChain;if(!context)return;
    const {prepared,target,key,resume}=context,password=$('acl').checked?'':$('password').value;
    const connection=link,controller=new AbortController();preparing=controller;busy(true);preparedDownload=null;
    status('Checking the saved update path...');
    try{
      // Saving the exact prepared path precedes all firmware writes. Passwords
      // are never persisted with the images, packages or phase journals.
      await LoraOtaAutoChain.cache('put',key,{source:link.sourceKey.toLowerCase(),target:target.toLowerCase(),prepared});
      const readStepJournal=()=>autoRead(key,'step'),writeStepJournal=state=>autoWrite(key,'step',state);
      const runner=new LoraOtaChain.ChainRunner({prepared,link,targetKey:target,
        readLive:()=>LoraOtaChainUi.readLive(link,target,password),
        readStepJournal,writeStepJournal,readChainJournal:()=>autoRead(key,'chain'),writeChainJournal:state=>autoWrite(key,'chain',state),
        createSession:async(step,{index})=>{
          autoStep='Step '+(index+1)+' of '+prepared.steps.length+': ';updatePhase='transfer';transferStage='';
          status('Preparing firmware transfer...');log('Starting update step '+(index+1)+' of '+prepared.steps.length+'.');
          const job=autoSession(step,target,password,key);
          job.readJournal=readStepJournal;job.journal=writeStepJournal;
          if(index===0&&context.replacement){const start=job.start.bind(job);job.start=options=>start({...options,replace:context.replacement});}
          const install=job.install.bind(job);job.install=()=>{
            updatePhase='install';operationErrorStep='Install and verify update step '+(index+1)+' of '+prepared.steps.length;
            status('Installing and checking the rebooted device...');return install();
          };
          session=job;return job;
        },
        rearm:async()=>{
          autoStep='';status('Preparing the radio channel for the next update step...');
          await ensureRadioChannel(target,password);
        },
        confirmRearm:async()=>{try{await ensureRadioChannel(target,password);return true;}catch(_){return false;}}
      });
      const result=await runner.run({resume,signal:controller.signal});
      await finishQuietForwarding();
      autoStep='';autoChain=null;
      status('Update complete. The remote device is running the verified firmware.','complete');
      $('update-summary').textContent='Installed and verified on '+result.live.hwId+' in '+result.steps+' automatic steps.'+completionDisconnectTip();
      log('Automatic update complete; all '+result.steps+' installations verified.');
    }catch(error){
      if(transferStopJob===session&&!link?.failure&&!link?.folderUnknown)return;
      await fail(error);
      $('update-summary').textContent='Update paused. Select Prepare update to continue automatically from the saved step.';
    }finally{
      autoStep='';updatePhase='';preparing=null;
      if(link===connection){busy(false);$('inspect').disabled=!link||updatesBlocked();}
    }
  }
  async function inspect() {
    const generation=disconnectGeneration;
    const diagnostics={search:{enabled:$('auto-search').checked,depth:$('auto-depth').value,
      objective:$('auto-objective').value,result:$('auto-search').checked?'Not started':'Disabled in Update path options'}};
    const imageDetails=(name,image)=>({name,hwId:image.hwId,version:image.fwVersion,
      imageHash:LoraOtaChecks.hex(image.imageHash),bodyHash:LoraOtaChecks.hex(image.bodyHash)});
    const checkConnected=()=>{
      if(generation!==disconnectGeneration)throw Error('Preparation stopped because the USB source is disconnecting.');
      if(preparing?.signal.aborted)throw Object.assign(Error('Update preparation cancelled. No firmware was uploaded.'),{otaLocalCheck:true,otaEndpoint:'Browser'});
    };
    status('Preparing the update: checking firmware, access and the radio channel...');
    invalidate(); busy(true); $('inspect').disabled = true;
    try {
      if (!link) throw Error('Connect a source radio first.');
      requireUpdatesAvailable();
      const targetKey=selectTarget(), password=$('acl').checked ? '' : $('password').value;
      const standard=sourceKind()==='companion' && link.otaSupported===false;
      // A fresh USB connection must prove the target again. Preparation can
      // do that itself, including after reconnecting an older Companion.
      const recovered=await recoverUpdateChannel(targetKey);
      if(standard && !link.investigationEnabled)await link.enableStandardInvestigation();
      let peerVerified=false, liveTargetOtaStatus=null;
      const verify=async()=>{
        if(peerVerified)return;
        const result=await link.verifyPeer(targetKey,password);await confirmUpdateChannel(recovered,targetKey,password);
        peerVerified=true;return result;
      };
      if(recovered)await verify();
      const remote=command=>standard ? link.investigateCommand(targetKey,password,command) : link.remoteCommand(targetKey,password,command);
      let file = catalogFile || $('file').files[0];
      if(!file && $('firmware-choice').value && catalogDest) {
        const candidate=catalogCandidates[Number($('firmware-choice').value)];
        if(candidate) { await loadCatalogCandidate(candidate,catalogGeneration);file=catalogFile;status('Preparing the update: checking the device and radio channel...'); }
      }
      if (!file) { openDetails($('file'));throw Error('Find matching firmware first, or choose a local firmware file.'); }
      const isFirmware = /\.(?:bin|zip)$/i.test(file.name);
      // Internal-stage nRF targets cannot install a full package. Keep their
      // selected differential even if the full-package recovery option was set.
      const forceFull = isFirmware && $('full-rebuild').checked && !(catalogDest?.nrf && !catalogDest.external);
      const baseFile = forceFull ? null : ($('base').files[0] || (isFirmware ? catalogBaseFile : null));
      diagnostics.selected={name:file.name};
      if(baseFile)diagnostics.installed={name:baseFile.name};
      const input = isFirmware ? await firmwareBytes(file, baseFile ? 4 * 1024 * 1024 : undefined) : await bytes(file);
      let cachedAuto=null;
      if(isFirmware&&typeof indexedDB!=='undefined'){
        const goal=await MeshCoreMotaPackage.parseEndF(input);
        try{cachedAuto=await LoraOtaAutoChain.cache('get',autoCacheKey(targetKey,LoraOtaChecks.hex(goal.imageHash)));}catch(_){/* ordinary updates do not need recovery storage */}
      }
      checkConnected();
      const signerText = $('signer').value.trim();
      let expectedImage = null, inplacePlan = null;
      if (signerText && !/^[0-9a-f]{64}$/i.test(signerText)) throw Error('The trusted signing key must be 64 hexadecimal characters.');
      expectedSigner = signerText ? Uint8Array.from(signerText.match(/../g), h => parseInt(h, 16)) : null;
      const buildCompatibleFull=async image=>{
        const nativeFolder=link?.browserRawSource===true && link.usesUsbFolderFallback===true;
        const ownership={sourceKey:link.sourceKey,targetKey,recovery:forceFull ? null : readJournal()};
        if(ownership.recovery) {
          const saved=await MeshCoreMotaPackage.buildFullForSource(image,ownership);
          if(saved.recovered)return {...saved,nativeFolder,imageBytes:image.length};
        }
        let sourceMaxBlock=null,targetMaxBlock=null;
        if(nativeFolder && image.length>1024*1024) {
          // Both live applications must explicitly support the larger block;
          // unknown replies never become an assumed capability.
          if(liveTargetOtaStatus)targetMaxBlock=LoraOtaChecks.explicitMaxBlock(liveTargetOtaStatus);
          else if(catalogDest)targetMaxBlock=catalogDest.explicitMaxBlock;
          else { await verify();targetMaxBlock=LoraOtaChecks.explicitMaxBlock(await remote('ota status')); }
          if(targetMaxBlock>=2048) {
            try { sourceMaxBlock=LoraOtaChecks.explicitMaxBlock(await link.localCommand('ota status')); }
            catch(error) { if(link.failure)throw error; }
          }
          checkConnected();
        }
        const built=await MeshCoreMotaPackage.buildFullForSource(image,{
          nativeFolder,sourceMaxBlock,targetMaxBlock,sourceKey:link.sourceKey,targetKey
        });
        return {...built,nativeFolder,imageBytes:image.length};
      };
      const logFullSelection=built=>{
        if(built.recovered)log('Keeping the original full package and block size for recovery. To prepare a new full package, open local firmware options and select “Prepare a new full package instead of resuming.”');
        else if(built.blockSize===2048)log('Using 2 KiB blocks to reduce the source proof table.');
        else if(built.reason==='unconfirmed')log('Could not confirm 2 KiB support on both radios; using the original 1 KiB blocks. Large packages may exceed an older source’s proof capacity.');
        if(built.nativeFolder && Math.ceil(built.imageBytes/built.blockSize)>1024)
          log('This package has more than 1024 blocks; older native sources may be unable to serve it. If transfer fails, update the source firmware or use a smaller compatible image.');
      };
      if (isFirmware) {
        if (expectedSigner) throw Error('A package created here is unsigned. Select a signed .mota file instead.');
        if (baseFile || cachedAuto) {
          await verify();
          const {status:receiverStatus,self} = await LoraOtaChecks.readReceiver(remote);
          liveTargetOtaStatus=receiverStatus+' '+self;
          const version = await remote('ver');
          const destination = LoraOtaChecks.destination(receiverStatus, self, version);
          // After a successful differential, the old base is deliberately no
          // longer running. Verify the selected goal before requiring that base.
          const goal = await MeshCoreMotaPackage.parseEndF(input);
          diagnostics.selected=imageDetails(file.name,goal);
          checkConnected();
          if(goal.targetId===destination.targetId && goal.hwId===destination.hwId &&
              goal.fwVersion===destination.version && LoraOtaChecks.hex(goal.bodyHash)===destination.baseHash) {
            await finishQuietForwarding();
            status('The target already reports the selected firmware. No update is needed.', 'complete');
            $('update-summary').textContent=destination.hwId+': '+file.name+' is already running.';
            log('Target identity, firmware version and reported body hash match the selected image.');
            return;
          }
          if(cachedAuto?.source===link.sourceKey.toLowerCase()&&cachedAuto.target===targetKey.toLowerCase()&&
              cachedAuto.prepared?.goalHash===LoraOtaChecks.hex(goal.imageHash)){
            const liveNode=cachedAuto.prepared.nodes?.some(n=>n.targetId===destination.targetId&&n.hwId===destination.hwId&&
              n.version===destination.version&&n.bodyHash===destination.baseHash);
            if(liveNode){await readyAutomatic(cachedAuto.prepared,targetKey,password,autoCacheKey(targetKey,cachedAuto.prepared.goalHash),file,true);return;}
          }
          if(!baseFile)throw Error('Find matching firmware again to identify the exact installed image.');
          const base = await firmwareBytes(baseFile, 4 * 1024 * 1024);
          const current = await MeshCoreMotaPackage.parseEndF(base);
          diagnostics.installed=imageDetails(baseFile.name,current);
          if (current.targetId !== destination.targetId || current.hwId !== destination.hwId ||
              current.fwVersion !== destination.version || LoraOtaChecks.hex(current.bodyHash) !== destination.baseHash)
            throw Error('The installed firmware image does not exactly match the target.');
          let deltaOptions = {mode:'sequential'};
          if (destination.nrf) {
            const layout = MeshCoreLoraDelta.nrfLayout(current.image);
            if (destination.external) {
              const diagnostics = await remote('ota qspi');
              const capacity = LoraOtaChecks.qspiCapacity(receiverStatus, self, diagnostics, destination);
              deltaOptions = {mode:'in-place', runtimeStorage:'qspi', runtimeQspiApply:true, runtimeStorageBytes:capacity};
              inplacePlan = {storage:'qspi', app:layout.app, layoutEnd:layout.end};
            } else {
              const stage = LoraOtaChecks.internalStage(receiverStatus, self, destination);
              deltaOptions = {mode:'in-place', runtimeStorage:'internal', runtimeStageCeiling:stage};
              if(layout.flags&16){
                try{
                  deltaOptions.runtimeHybridRam=await LoraOtaChecks.probeHybridRam(async command=>{
                    const reply=await remote(command);
                    if(command==='get bootloader.ver')diagnostics.ram={...diagnostics.ram,bootloaderVersion:reply};
                    if(command==='ota bootloader status')diagnostics.ram={...diagnostics.ram,bootloaderStatus:reply};
                    return reply;
                  });
                  diagnostics.ram={...diagnostics.ram,confirmed:true,reason:'Confirmed by the live target RAM, bootloader version or pinned identity check'};
                }catch(error){
                  // Keep the entirely-flash budget, but explain why the SRAM
                  // allowance was unavailable rather than hiding the cause.
                  diagnostics.ram={...diagnostics.ram,confirmed:false,reason:error?.message||String(error)};
                }
              }
              inplacePlan = {storage:'internal', app:layout.app, layoutCeiling:layout.ceiling, layoutFlags:layout.flags};
            }
          } else await LoraOtaChecks.ensureSequentialTarget(receiverStatus, self, () => remote('get bootloader.ver'));
          checkConnected();
          preparing = new AbortController();
          $('prepare-stop').hidden=false;$('prepare-stop').disabled=false;
          log('Preparing the differential package in this browser...');
          let delta,sizingFailure=null;
          try{
            delta = await MeshCoreLoraDelta.buildInWorker(base, input, {...deltaOptions,
              searchWorkspaces:$('auto-search').checked?($('auto-depth').value==='deep'?'deep':$('auto-objective').value==='data'?'data':'fit'):undefined,
              progress:state=>{if(state?.phase==='workspace')status('Preparing differential: workspace '+state.index+' of '+state.total+'...');},signal:preparing.signal});
          }catch(error){
            if(error?.code!=='OTA_DELTA_SPACE'||!$('auto-search').checked)throw error;
            sizingFailure=error;diagnostics.space=error.space;
            log('The direct update exceeds available flash. Searching intermediate updates automatically...');
            status('The direct update does not fit. Looking for an intermediate update path...');
          }
          if($('auto-search').checked){
            try{if(await prepareAutomatic(base,input,destination,targetKey,password,file,preparing.signal,diagnostics))return;}
            catch(error){
              diagnostics.search.result='Search stopped: '+(error?.message||String(error));
              checkConnected();if(!delta)throw error;log('Could not compare intermediate paths: '+error.message+'. Keeping the verified direct package.');
            }
          }
          if(!delta){
            sizingFailure.message+=' No fitting automatic update path is published for these exact images. Use USB/Bluetooth DFU or another compatible release.';
            throw sizingFailure;
          }
          if (inplacePlan) inplacePlan.memorySize = delta.memorySize;
          preparing = null;
          const full = (!destination.nrf || destination.external) ? await buildCompatibleFull(input) : null;
          const recovery={sourceKey:link.sourceKey,targetKey,recovery:forceFull ? null : readJournal()};
          const matchingDelta=await MeshCoreMotaPackage.matchesRecovery(delta.blob,recovery);
          let matchingStagedFull=false;
          if(full?.recovered && !matchingDelta && delta.blob.length < full.blob.length) {
            let staged;
            try { staged=LoraOtaSession.download(receiverStatus); }
            catch(_) { staged=LoraOtaSession.download(receiverStatus,await remote('ota stats')); }
            matchingStagedFull=staged.id===String(recovery.recovery?.id||'').toLowerCase() && ['active','failed','ready'].includes(staged.state);
          }
          if (full && !matchingDelta && (matchingStagedFull || delta.blob.length >= full.blob.length)) {
            info = await MeshCoreMotaPackage.parseMota(full.blob);logFullSelection(full);
            if(!full.recovered)log('The full package is smaller, so it will be used.');
          } else {
            info = await MeshCoreMotaPackage.parseMota(delta.blob); expectedImage = input;
            log('Differential payload: ' + delta.payloadBytes + ' bytes.');
          }
        } else {
          const full=await buildCompatibleFull(input);info=await MeshCoreMotaPackage.parseMota(full.blob);logFullSelection(full);
        }
      } else {
        if (baseFile) throw Error('An installed firmware image is only used when creating a differential from a .bin.');
        info = await MeshCoreMotaPackage.parseMota(input, expectedSigner ? {expectedSigner} : {});
      }
      const targetImage = $('image').files[0];
      if (!info.isFull && !targetImage && !expectedImage) throw Error('Select the matching new .bin image for the differential package.');
      if (!expectedImage && targetImage) expectedImage = await firmwareBytes(targetImage);
      checkConnected();
      const Session = link.relays?.length ? LoraOtaRoutedSession.RoutedSession : LoraOtaSession.DirectSession;
      session = new Session(link, $('key').value.trim(), $('acl').checked ? '' : $('password').value, info, {
        signerVerified:!!expectedSigner && info.signed, image:expectedImage, inplacePlan,deferSecondaryRestoration:true,
        compress:supportedCompression(), progress:log, onStage:sessionStage, journal, readJournal
      });
      // Reject a wrong/corrupt image before changing either radio. A completed
      // installation can also be verified without reopening an update channel.
      let dest;
      try { dest = await session.inspect({allowExisting:true,allowReplacement:true}); }
      catch(error) {
        if(link.relays?.length || !error.radioSetupRequired || !canPrepareRadioChannel())throw error;
        await applyTargetRadioHardware(info.hwId);
        await ensureRadioChannel(targetKey,password);
        dest = await session.inspect({allowExisting:true,allowReplacement:true});
      }
      if(dest.download!=='installed')await applyTargetRadioHardware(dest.hwId);
      if(dest.download!=='installed'&&!link.relays?.length&&explicitRadioChoice()&&!chosenRadioMatches(session)){
        await ensureRadioChannel(targetKey,password);
        dest=await session.inspect({allowExisting:true,allowReplacement:true});
      }
      if(dest.download==='installed')await finishQuietForwarding();
      else await pauseQuietForwarding(targetKey,password,session.verifiedRadio);
      preparedDownload=['idle','active','failed','ready','replace'].includes(dest.download)?dest.download:null;
      preparedReplacement=dest.download==='replace' ? dest.existingDownload : null;
      const targetName=contacts.find(c=>String(c.publicKey).toLowerCase()===targetKey.toLowerCase())?.name || dest.hwId;
      $('update-summary').textContent=targetName+' ('+dest.hwId+'): '+file.name+' / '+(info.isFull?'full':'differential')+' update, '+Math.ceil(info.blob.length/1024)+' KB. Keep this page open until verification finishes.';
      const action=dest.download==='replace' ? 'Replace and install update' : dest.download==='failed' ? 'Restart and install update' :
        dest.download==='active' ? 'Resume and install update' : dest.download==='ready' ? 'Install and verify update' : 'Update remote firmware';
      status(dest.download==='installed' ? 'This update is already installed and verified.' : dest.download==='replace' ?
        'An unfinished update is staged on the target. Select Replace and install update to discard it and install your selected firmware.' : 'Ready. Select '+action+'.', dest.download==='installed'?'complete':'ready');
      log('Verified ' + dest.hwId + '; ' + (info.isFull ? 'full' : 'differential') + ' package ' + LoraOtaChecks.hex(info.merkleRoot).toUpperCase() + '.');
      log(info.signed ? (expectedSigner ? 'Package signature matches your trusted key.' : 'Package signature is valid; no trusted signing key was supplied.') : 'This package is unsigned.');
      $('transfer').disabled = dest.download !== 'idle';
      $('resume').disabled = !['active','failed'].includes(dest.download);
      $('install').disabled = dest.download !== 'ready';
      if (dest.download === 'active') log('A matching transfer is in progress. Select Resume.');
      if (dest.download === 'failed') log('The interrupted download matches this browser update record. Restart will request the same package again; reusable staged data is retained when supported by the target.');
      if (dest.download === 'replace') {
        const message='Staged update '+dest.existingDownload.id.toUpperCase()+' ('+dest.existingDownload.state+') will be discarded only when you select Replace and install update.';
        $('update-summary').textContent=message+' Selected firmware: '+file.name+'.';log(message);
      }
      if (dest.download === 'ready') log('The matching package is ready to install.');
      if (dest.download === 'installed') log('The previously requested install is verified.');
    } catch (error) {
      const failure={...error,message:error?.message||String(error),otaPreparation:diagnostics};
      if(error?.space)diagnostics.space=error.space;
      invalidate(); await fail(failure);
      if(link && sourceKind()==='companion' && link.otaSupported===false)
        $('firmware-catalog-status').textContent=failureMessage(failure);
    }
    finally { preparing = null;$('prepare-stop').hidden=true; busy(false); $('inspect').disabled = !link || updatesBlocked(); }
  }
  async function transfer(resume) {
    const job=session;transferStopJob=transferStopTask=null;
    preparedDownload=null;
    updatePhase='transfer';transferStage='';sourceBytesRead=0;sourceActivityAt=0;sourceManifestReadLogged=false;lastTransferPercent=null;
    $('transfer').disabled = true; $('resume').disabled = true; $('inspect').disabled = true; busy(true);
    try { await job.start({resume}); if(transferStopJob===job)return;preparedDownload='ready';log('Transfer complete. Check the target and select Install.'); $('install').disabled = false;status('Transfer complete. Select Install and verify update.', 'ready'); }
    catch (error) { if(transferStopJob!==job||link?.failure||link?.folderUnknown)await fail(error); }
    finally { updatePhase='';busy(false); $('inspect').disabled = !link || updatesBlocked(); }
  }
  async function updateRemote() {
    if(!uiBusy&&autoChain&&preparedDownload&&!updatesBlocked())return runAutomatic();
    if(uiBusy || !session || !preparedDownload || updatesBlocked())return;
    const job=session, download=preparedDownload, replacement=preparedReplacement;
    transferStopJob=transferStopTask=null;
    preparedDownload=preparedReplacement=null;busy(true);
    for(const name of ['inspect','transfer','resume','install'])$(name).disabled=true;
    try {
      if(download!=='ready') {
        updatePhase='transfer';
        transferStage='';sourceBytesRead=0;sourceActivityAt=0;sourceManifestReadLogged=false;lastTransferPercent=null;
        status(link?.relays?.length ? 'Checking the route, target and radio settings...' : 'Checking the target and radio settings...');
        await job.start({resume:['active','failed'].includes(download),...(download==='replace' ? {replace:replacement} : {})});
        if(transferStopJob===job)return;
        log('Transfer complete. Installing the verified package.');
      }
      // Use the session's fresh identity, staged-package and journal checks.
      // An uncertain write is never repeated by this UI action.
      updatePhase='install';
      operationErrorStep='Install firmware and verify the remote device after reboot';
      status('Installing on the remote device, then checking it after reboot...');
      const result=await job.install();
      await finishQuietForwarding(job);
      log('Installed and verified: '+result.hwId+' '+result.baseHash.toUpperCase());
      status('Update complete. The remote device is running the verified firmware.', 'complete');
      $('update-summary').textContent='Installed and verified on '+result.hwId+'.'+completionDisconnectTip();
    } catch(error) {
      if(transferStopJob===job&&!link?.failure&&!link?.folderUnknown)return;
      await fail(error);
      $('update-summary').textContent=(link?.failure ? 'Update stopped. Select Reconnect USB source, then Prepare update.' :
        'Update stopped. Select Prepare update to read the device state.')+
        ' Keep the same firmware to recover it, or choose different firmware to replace the unfinished update.';
    } finally {
      updatePhase='';busy(false);$('inspect').disabled=!link || updatesBlocked();
    }
  }
  async function stopTransfer(){
    if(transferStopTask)return transferStopTask;
    if(updatePhase!=='transfer'||!session)return;
    const job=session,connection=link,generation=disconnectGeneration,updating=activeTask;
    transferStopJob=job;status('Stopping transfer...');$('transfer-stop').disabled=true;
    const stopping=(async()=>{
      await job.stop();
      if(updating)await updating;
      if(link!==connection||disconnectGeneration!==generation)return;
      if(link?.failure)throw link.failure;
      if(link?.folderUnknown)throw Error('Transfer stopped, but the USB source did not confirm release. Disconnect and reconnect it before preparing another update.');
      preparedDownload=preparedReplacement=null;
      $('update-progress').hidden=true;
      $('update-summary').textContent='Transfer stopped. Keep this firmware to resume, or choose different firmware to replace it.';
      log('Transfer stopped. The remote download is kept.');
      await restoreQuietForwarding();
      status('Transfer stopped. Select Prepare update to continue.', 'ready');
    })();
    transferStopTask=stopping;
    try{await stopping;}catch(error){if(link===connection&&disconnectGeneration===generation)await fail(error,'Stop transfer and release the USB source');}
    finally{if(transferStopTask===stopping)transferStopTask=null;refreshTransferStop();}
  }
  function run(operation,step='LoRa update') {
    // Reserve the UI before starting async work; a second action must not
    // replace its session or its cleanup promise while the first is pending.
    if(activeTask)return activeTask;
    failureSerial++;
    operationErrorStep=step;
    let finish;
    const task=new Promise(resolve=>{finish=resolve;});
    activeTask=task;
    (async()=>{
      try { await operation(); }
      catch(error) { await fail(error);busy(false); }
      finally { if(activeTask===task){activeTask=null;operationErrorStep='LoRa update';}finish(); }
    })();
    return task;
  }
  $('update').addEventListener('click',()=>run(()=>updateRemote(),'Update remote firmware'));
  $('transfer-stop').addEventListener('click',()=>stopTransfer());
  $('firmware-role').addEventListener('change',()=>run(async()=>{
    catalogGeneration++;catalogFile=null;catalogBaseFile=null;catalogCandidates=[];invalidate();
    $('firmware-base-status').textContent='';
    $('firmware-choice-wrap').hidden=true;$('firmware-download').hidden=true;
    if(!$('firmware-role').value){$('firmware-catalog-status').textContent='Choose the target type to find matching firmware.';return;}
    busy(true);$('inspect').disabled=true;
    try{await discoverCatalog($('firmware-role').value);}
    catch(error){$('firmware-catalog-status').textContent=failureMessage(error)+'\nYou can choose a file manually.';await fail(error);}
    finally{busy(false);$('inspect').disabled=!link || updatesBlocked();}
  },'Find compatible firmware'));
  $('firmware-choice').addEventListener('change',()=>{
    catalogFile=null;$('file').value='';invalidate();
    $('firmware-download').disabled=!$('firmware-choice').value;
    if($('firmware-choice').value)$('firmware-catalog-status').textContent='Select Prepare update to load and check this firmware.';
  });
  $('firmware-download').addEventListener('click',()=>run(async()=>{
    const index=Number($('firmware-choice').value),generation=catalogGeneration;
    if(!$('firmware-choice').value || !Number.isInteger(index) || !catalogCandidates[index])return;
    busy(true);$('inspect').disabled=true;
    try{await loadCatalogCandidate(catalogCandidates[index],generation);}
    catch(error){$('firmware-catalog-status').textContent=failureMessage(error)+'\nChoose another matching image or use a local file.';await fail(error);openDetails($('file'));}
    finally{busy(false);$('inspect').disabled=!link || updatesBlocked();}
  },'Download and validate selected firmware'));
  $('connect').addEventListener('click', () => run(()=>connect(),'Connect USB source'));
  $('reconnect').addEventListener('click', () => run(()=>reconnect(),'Reconnect USB source'));
  $('investigate').addEventListener('click', () => run(()=>investigate(),'Find matching firmware: check remote device'));
  $('radio-prepare').addEventListener('click',()=>run(()=>checkRadioChannel(true),'Prepare the temporary update channel'));
  $('radio-check').addEventListener('click',()=>run(()=>checkRadioChannel(false),'Check the update channel'));
  for(const name of radioFields)$(name).addEventListener('input',()=>{
    radioOverride=true;$('radio-reset').hidden=false;invalidate();showRadioCommands();$('radio-region-status').textContent='Your manually selected radio settings take precedence.';
  });
  $('radio-region').addEventListener('change',()=>run(async()=>{
    invalidate();busy(true);
    try{await suggestRadioChannel();}catch(error){await fail(error);}finally{busy(false);}
  },'Select regional update-channel defaults'));
  $('radio-reset').addEventListener('click',()=>run(async()=>{
    radioOverride=false;$('radio-reset').hidden=true;invalidate();busy(true);
    try{await suggestRadioChannel();}catch(error){await fail(error);}finally{busy(false);}
  },'Restore automatic update-channel defaults'));
  $('quiet-forwarding')?.addEventListener('change',()=>run(async()=>{
    quietPlan=null;invalidate();
    if(!$('quiet-forwarding').checked){
      busy(true);try{await restoreQuietForwarding({required:true});}
      catch(error){await fail(error,'Restore forwarding after disabling the update pause');}
      finally{busy(false);}
    }
  },'Change forwarding pause preference'));
  $('restore-forwarding')?.addEventListener('click',()=>run(async()=>{
    busy(true);try{await restoreQuietForwarding({required:true,retry:true});}
    catch(error){await fail(error,'Restore the target forwarding setting');}
    finally{busy(false);}
  },'Restore the target forwarding setting'));
  showRadioCommands();
  $('investigation-stop').addEventListener('click',()=>{
    if(!link || typeof link.cancelInvestigation!=='function')return;
    investigationStopping=true;$('investigation-stop').disabled=true;
    showInvestigationProgress('Stopping safely...');
    try { link.cancelInvestigation(); } catch(error) { fail(error,'Stop the remote device check'); }
  });
  $('inspect').addEventListener('click', () => run(()=>inspect(),'Prepare update: validate firmware and device'));
  $('prepare-stop').addEventListener('click',()=>{if(preparing&&!updatePhase){$('prepare-stop').disabled=true;preparing.abort();status('Stopping update preparation...');}});
  $('find-route').addEventListener('click', () => run(()=>findRoute(),'Find an admin route'));
  $('transfer').addEventListener('click', () => run(()=>transfer(false),'Transfer firmware to the remote device'));
  $('resume').addEventListener('click', () => run(()=>transfer(true),'Resume the existing firmware transfer'));
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
    } catch (error) { await fail(error); }
    finally { busy(false); $('inspect').disabled = !link || updatesBlocked(); }
  }
  $('repeat-read').addEventListener('click', () => run(()=>forwarding('read'),'Check repeater forwarding'));
  $('repeat-off').addEventListener('click', () => run(()=>forwarding('off'),'Turn repeater forwarding off'));
  $('repeat-on').addEventListener('click', () => run(()=>forwarding('on'),'Turn repeater forwarding on'));
  async function install() {
    preparedDownload=null;
    $('install').disabled = true; $('inspect').disabled = true; busy(true);
    try { const result = await session.install(); await finishQuietForwarding();preparedDownload=null;log('Installed and verified: ' + result.hwId + ' ' + result.baseHash.toUpperCase());status('Update complete. The remote device is running the verified firmware.', 'complete'); }
    catch (error) { await fail(error); }
    finally { busy(false); $('inspect').disabled = !link || updatesBlocked(); }
  }
  async function planChain() {
    invalidate(); busy(true); $('inspect').disabled = true;
    try {
      requireUpdatesAvailable();
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
      if(installProbe!=='ERR usage: ota installhash <64-hex-container-sha256>') {
        if(!/^(?:(?:err(?:or)?\s*:?\s*)?(?:unknown|unsupported)\b)/i.test(installProbe))
          throw Error('Cannot determine the target installation capability');
        log('Older target: legacy installation cannot bind the full container hash before installation. Each reboot must still verify the reported firmware hash.');
      }
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
      log('Comparing update paths in this browser...');
      chainPrepared=await LoraOtaCandidates.prepare({images,current,goal:'goal',objective,radio,kinds:['delta'],
        relays:link.relays?.length||0,restartSeconds:60,signal:preparing.signal,progress:log},
        {compressBlock:supportedCompression()});
      preparing=null;
      log('Simulating each differential with the matching bootloader...');
      await LoraOtaSim.simulate(bootProfile,chainPrepared,caps.runtimeStageCeiling);
      chainConfig={source:link.sourceKey,target,current,caps,baseImageHash:LoraOtaChecks.hex(first.imageHash),objective,preamble,minutes,sourceRadio,targetPreamble,bootProfile};
      const route=chainPrepared.route;
      log('Selected path: '+route.steps.map(s=>s.from+' -> '+s.to+' ('+s.kind+')').join('; ')+'.');
      log('Estimate: '+route.totals.rfTransmissions+' radio transmissions'+
        (route.totals.rfAirtimeSeconds===null?'':', '+route.totals.rfAirtimeSeconds.toFixed(1)+' seconds of radio airtime')+
        '; '+route.totals.installs+' installation(s)'+
        (Number.isFinite(route.totals.estimatedSeconds)?', '+route.totals.estimatedSeconds.toFixed(1)+' seconds including an assumed 60 seconds per reboot':'')+
        '. Polls, retries, duty pacing and congestion are excluded.');
      for(const warning of new Set([...chainPrepared.warnings,...(route.assumptions||[])]))log(warning);
      if(!chainPrepared.complete)log('Some candidates could not be prepared; the selected path is only the best among those checked.');
      log('Every selected differential passed the bootloader model. The model does not replace testing the intermediate firmware on this hardware.');
    }catch(error){chainPrepared=chainConfig=null;await fail(error);}
    finally{preparing=null;busy(false);$('inspect').disabled=!link || updatesBlocked();}
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
        createSession:async step=>(session=new Session(link,target,password,step.package.info,{
          image:step.targetImage,inplacePlan:step.edge.inplacePlan,compress:supportedCompression(),deferSecondaryRestoration:true,
          progress:log,journal:writeStepJournal,readJournal:readStepJournal
        })),
        rearm:async ({signal})=>LoraOtaChainUi.rearm(link,target,password,sourceRadio,minutes,{signal,preamble:targetPreamble}),
        confirmRearm:async()=>LoraOtaChainUi.activeRadio(link,target,password,sourceRadio,targetPreamble)
      });
      const result=await runner.run({resume,signal:signalController.signal});
      await finishQuietForwarding();
      log('Update sequence complete; verified '+result.steps+' installation(s).');
    } catch(error) { await fail(error); }
    finally { preparing=null;busy(false);$('inspect').disabled=!link || updatesBlocked(); }
  }
  $('install').addEventListener('click', () => run(()=>install(),'Install firmware and verify the remote device after reboot'));
  $('chain-plan').addEventListener('click', () => run(()=>planChain(),'Plan multi-version firmware update'));
  $('chain-run').addEventListener('click', () => run(()=>runChain(false),'Run multi-version firmware update'));
  $('chain-resume').addEventListener('click', () => run(()=>runChain(true),'Resume multi-version firmware update'));
  $('disconnect').addEventListener('click', () => close().then(() => log('Disconnected.')).catch(error=>fail(error,'Disconnect USB source')));
})();
