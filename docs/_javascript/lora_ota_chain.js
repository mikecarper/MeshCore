/* Sequential execution of an already prepared version chain; no radio I/O is implicit. */
(function (root, factory) {
  const candidates = typeof module === 'object' && module.exports ? require('./lora_ota_candidates.js') : root.LoraOtaCandidates;
  const checks = typeof module === 'object' && module.exports ? require('./lora_ota_checks.js') : root.LoraOtaChecks;
  const api = factory(candidates, checks);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.LoraOtaChain = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (candidates, checks) {
  'use strict';
  const KEY = /^[0-9a-f]{64}$/i;
  async function hash(value) {
    const api = globalThis.crypto?.subtle || (typeof require === 'function' ? require('node:crypto').webcrypto.subtle : null);
    if (!api) throw Error('Web Crypto is unavailable');
    return checks.hex(new Uint8Array(await api.digest('SHA-256',new TextEncoder().encode(value))));
  }
  function matchingLive(node, live) {
    return !!live && live.targetId === node.targetId && live.hwId === node.hwId && live.version === node.version &&
      String(live.bodyHash || '').toLowerCase() === node.bodyHash.toLowerCase() &&
      (!live.imageHash || String(live.imageHash).toLowerCase() === node.imageHash.toLowerCase());
  }
  class ChainRunner {
    constructor(options) {
      const o=options || {};
      if (!o.prepared || !Array.isArray(o.prepared.steps) || !o.prepared.steps.length || !Array.isArray(o.prepared.nodes) ||
          !o.link || !KEY.test(o.link.sourceKey || '') || !KEY.test(o.targetKey || '') ||
          typeof o.readLive !== 'function' || typeof o.createSession !== 'function' ||
          typeof o.readStepJournal !== 'function' || typeof o.writeStepJournal !== 'function' ||
          typeof o.readChainJournal !== 'function' || typeof o.writeChainJournal !== 'function')
        throw Error('A prepared chain, authenticated source and target, live reader, session factory and separate journals are required');
      if (o.readStepJournal === o.readChainJournal || o.writeStepJournal === o.writeChainJournal)
        throw Error('Chain and step recovery journals must be separate');
      Object.assign(this,o); this.running=false;
      this.sourceKey=o.link.sourceKey.toLowerCase(); this.targetKey=o.targetKey.toLowerCase();
      this.nodes=new Map(o.prepared.nodes.map(n=>[n.id,n]));
      for (let i=0; i<o.prepared.steps.length; i++) {
        const step=o.prepared.steps[i], from=this.nodes.get(step.from), to=this.nodes.get(step.to);
        if (!from || !to || !step.package || !KEY.test(step.packageHash || '') ||
            (i && o.prepared.steps[i-1].to !== step.from)) throw Error('The prepared update chain is incomplete');
      }
    }
    cancelCheck(signal) { if (signal?.aborted) throw Error('Chain stopped; inspect its saved recovery state'); }
    async fingerprint() {
      const identity = id => {
        const n=this.nodes.get(id);
        return {id:n.id,version:n.version,targetId:n.targetId,hwId:n.hwId,bodyHash:n.bodyHash,imageHash:n.imageHash};
      };
      return hash(JSON.stringify({source:this.sourceKey,target:this.targetKey,
        relays:Array.isArray(this.link.relays) ? this.link.relays.map(k=>String(k).toLowerCase()) : [],
        steps:this.prepared.steps.map(s=>({from:identity(s.from),to:identity(s.to),packageHash:s.packageHash.toLowerCase()}))}));
    }
    state(planHash,phase,index) { return {version:1,planHash,source:this.sourceKey,target:this.targetKey,phase,index}; }
    save(planHash,phase,index) { this.writeChainJournal(this.state(planHash,phase,index)); }
    readStep(step) {
      const record=this.readStepJournal(), id=checks.hex(step.package.info.merkleRoot);
      if (!record || String(record.source || '').toLowerCase() !== this.sourceKey ||
          String(record.target || '').toLowerCase() !== this.targetKey || record.packageHash !== step.packageHash || record.id !== id)
        throw Error('The saved step does not match this source, target and complete package');
      return record;
    }
    async live(stage,index) {
      const actual=await this.readLive({targetKey:this.targetKey,stage,index});
      if (!actual) throw Error('The target could not be authenticated and read');
      return actual;
    }
    async verifyInstalled(index,live) {
      const node=this.nodes.get(this.prepared.steps[index].to);
      if (!matchingLive(node,live)) throw Error('The rebooted target does not match the planned installed image');
      if (index + 1 < this.prepared.steps.length) await candidates.checkStep(this.prepared,index+1,live);
    }
    async run(options = {}) {
      if (this.running) throw Error('The update chain is already running');
      this.running=true;
      try { return await this.runOnce(options); } finally { this.running=false; }
    }
    async runOnce({resume=false,signal} = {}) {
      this.cancelCheck(signal);
      if (!this.link.sourceKey || this.link.sourceKey.toLowerCase() !== this.sourceKey) throw Error('The connected source identity changed');
      const planHash=await this.fingerprint(), saved=this.readChainJournal();
      let index=0, recovery=false, phase='step starting';
      if (resume) {
        if (!saved || saved.version!==1 || saved.planHash!==planHash || saved.source!==this.sourceKey || saved.target!==this.targetKey ||
            !Number.isInteger(saved.index) || saved.index<0 || saved.index>=this.prepared.steps.length ||
            !['step starting','step verified','rearm attempted','complete'].includes(saved.phase))
          throw Error('No matching chain recovery record is available');
        index=saved.index; phase=saved.phase; recovery=phase==='step starting';
        if (phase==='complete') {
          const live=await this.live('complete',index); await this.verifyInstalled(index,live);
          return {phase:'complete',steps:this.prepared.steps.length,live};
        }
      } else {
        if (saved && saved.phase!=='complete') throw Error('An unfinished update chain already has a recovery record');
        this.save(planHash,'step starting',0);
      }
      while (index < this.prepared.steps.length) {
        this.cancelCheck(signal);
        const step=this.prepared.steps[index];
        if (phase === 'step verified' || phase === 'rearm attempted') {
          const live=await this.live('post-install',index);
          await this.verifyInstalled(index,live); this.cancelCheck(signal);
          if (index+1 === this.prepared.steps.length) {
            this.save(planHash,'complete',index); return {phase:'complete',steps:index+1,live};
          }
          const context={previousStep:step,nextStep:this.prepared.steps[index+1],live,index,signal};
          if (phase === 'rearm attempted') {
            if (typeof this.confirmRearm !== 'function' || !(await this.confirmRearm(context)))
              throw Error('Prior radio rearm is unconfirmed; inspect it before resuming');
          } else {
            if (typeof this.rearm !== 'function') throw Error('A verified radio rearm is required before the next update');
            this.save(planHash,'rearm attempted',index); await this.rearm(context);
          }
          this.cancelCheck(signal);
          index++; phase='step starting'; recovery=false; this.save(planHash,phase,index); continue;
        }
        const before=await this.live('pre-install',index);
        const recoveringRecord = recovery ? this.readStep(step) : null;
        // When an install was already attempted the live image may already be
        // the destination, so only earlier phases require a source check.
        if (!recoveringRecord || !['install attempted','installed'].includes(recoveringRecord.phase))
          await candidates.checkStep(this.prepared,index,before);
        this.cancelCheck(signal);
        const session=await this.createSession(step,{index,targetKey:this.targetKey});
        if (!session || typeof session.start!=='function' || typeof session.install!=='function' || typeof session.inspect!=='function')
          throw Error('The OTA session factory did not provide a complete session');
        // Do not let an integration mistake send another package, target or
        // source before the independent step journal can detect the mismatch.
        if (session.link !== this.link || String(session.key || '').toLowerCase() !== this.targetKey ||
            session.info !== step.package.info || session.info.blob !== step.package.blob)
          throw Error('The OTA session does not match the selected source, target and package');
        if (session.readJournal !== this.readStepJournal || session.journal !== this.writeStepJournal)
          throw Error('The OTA session is not using the selected step recovery journal');
        const stop=()=>{ if (typeof session.stop === 'function') session.stop(); };
        if (signal?.aborted) stop();
        signal?.addEventListener('abort',stop,{once:true});
        try {
          if (recovery) {
            const record=recoveringRecord;
            if (record.phase==='install attempted') {
              // An uncertain install must never be reissued. The session can
              // prove it already booted, or the operator must resolve it.
              const inspected=await session.inspect({allowExisting:true});
              if (inspected.download!=='installed') throw Error('Prior install is unconfirmed; inspect the target before retrying');
            } else if (record.phase==='installed') {
              // A durable step record already proves install was attempted; the
              // fresh post-install live check below proves what is running now.
            } else if (record.phase==='pull attempted' || record.phase==='ready') {
              await session.start({resume:true});
            } else if (record.phase==='prepared') {
              await session.start({resume:false});
            } else throw Error('The saved step phase is not safe to resume');
          } else await session.start({resume:false});
          this.cancelCheck(signal);
          const record=this.readStep(step);
          if (record.phase==='ready') await session.install();
          else if (record.phase!=='installed') throw Error('The target update is not confirmed ready or installed');
        } finally { signal?.removeEventListener('abort',stop); }
        this.cancelCheck(signal);
        const live=await this.live('post-install',index);
        await this.verifyInstalled(index,live);
        this.save(planHash,'step verified',index); phase='step verified'; recovery=false;
      }
      throw Error('Update chain ended unexpectedly');
    }
  }
  return {ChainRunner,matchingLive};
});
