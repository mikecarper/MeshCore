'use strict';
const assert = require('node:assert/strict');
const {test} = require('node:test');
const {relayRoute, RoutedRepeaterTransport} = require('../docs/_javascript/lora_ota_route.js');

const source = 'a'.repeat(64), first = 'b'.repeat(64), second = 'c'.repeat(64), target = 'd'.repeat(64);
class FakeLink {
  constructor() { this.aclOnly=true; this.knownRepeater=target; this.sourceKey=source; this.supportsRoutedPath=true; this.running=false; this.path='off'; this.trace=[]; this.admin=Promise.resolve(); this.ownsPeer=false; }
  async open() { this.running=true; }
  async close() { this.running=false; }
  async checkClock() { this.trace.push('clock'); }
  withAdmin(fn) { const p=this.admin.then(fn); this.admin=p.catch(()=>{}); return p; }
  async job(s) { this.ownsPeer=true; this.trace.push(s+' @'+this.path); if (s.startsWith('ota.peer cmd.acl ') && this.block) { this.started(); await this.block; } return 'OK'; }
  async localCommand(s) {
    this.trace.push(s);
    if (s==='ota.peer path') return 'OTApeer path='+this.path;
    if (s.startsWith('ota.peer path ')) {
      this.path=s.slice('ota.peer path '.length);
      if (this.loseAck) { this.loseAck=false; throw Error('timeout'); }
      return 'OTApeer path='+this.path;
    }
    if (s==='ota.peer close') return 'OTApeer closed';
    throw Error('unexpected '+s);
  }
  async serve() { this.trace.push('serve'); }
  async stopServing() { this.trace.push('stop'); }
}

test('full identity routing chooses nearest-first paths', () => {
  const r=relayRoute(target,[first,second]);
  assert.deepEqual(r.paths.get(first),[]); assert.deepEqual(r.paths.get(second),['BBBBBB']);
  assert.deepEqual(r.paths.get(target),['BBBBBB','CCCCCC']);
  assert.throws(()=>relayRoute(target,[first,first]),/distinct/);
  assert.throws(()=>relayRoute(target,['b'.repeat(6)+'f'.repeat(58),first]),/distinct/);
  assert.throws(()=>relayRoute(target,[]),/one to eight/);
});

test('proves every full key on the correct route and restores its own path', async () => {
  const raw=new FakeLink(), link=new RoutedRepeaterTransport(raw,target,[first,second]);
  await link.open(); await link.proveAll();
  assert.ok(raw.trace.includes('ota.peer acl '+first+' @off'));
  assert.ok(raw.trace.includes('ota.peer acl '+second+' @BBBBBB'));
  assert.ok(raw.trace.includes('ota.peer acl '+target+' @BBBBBB,CCCCCC'));
  assert.equal(await link.remoteCommand(target,'','get tx'),'OK');
  assert.ok(raw.trace.includes('ota.peer cmd.acl '+target+' get tx @BBBBBB,CCCCCC'));
  assert.equal(raw.trace.filter(x=>x.startsWith('ota.peer acl '+target)).length,1);
  await link.close(); assert.equal(raw.path,'off'); assert.equal(raw.running,false);
});

test('route mutation with lost reply uses readback rather than retry', async () => {
  const raw=new FakeLink(), link=new RoutedRepeaterTransport(raw,target,[first,second]);
  await link.open(); raw.loseAck=true; await link.verifyPeer(second,'');
  assert.equal(raw.trace.filter(x=>x==='ota.peer path BBBBBB').length,1);
  assert.ok(raw.trace.includes('ota.peer acl '+second+' @BBBBBB'));
  await link.close(); assert.equal(raw.path,'off');
});

test('does not overwrite another operator’s changed path on cleanup', async () => {
  const raw=new FakeLink(), link=new RoutedRepeaterTransport(raw,target,[first,second]);
  await link.open(); await link.verifyPeer(target,''); raw.path='A1B2C3';
  await assert.rejects(link.close(),/path changed/); assert.equal(raw.path,'A1B2C3');
  assert.equal(raw.trace.includes('ota.peer path off'),false);
});

test('refuses a command or path mutation after another operator changes the path', async () => {
  const raw=new FakeLink(), link=new RoutedRepeaterTransport(raw,target,[first,second]);
  await link.open(); await link.verifyPeer(target,''); raw.path='A1B2C3';
  await assert.rejects(link.remoteCommand(target,'','get tx'),/path changed/);
  await assert.rejects(link.verifyPeer(first,''),/path changed/);
  assert.equal(raw.trace.some(s=>s.startsWith('ota.peer cmd.acl ')),false);
  assert.equal(raw.path,'A1B2C3');
  await assert.rejects(link.close(),/path changed/);
});

test('close waits for in-flight routed command and rejects later commands', async () => {
  const raw=new FakeLink(), link=new RoutedRepeaterTransport(raw,target,[first,second]);
  await link.open(); await link.verifyPeer(target,'');
  let release, started; raw.block=new Promise(r=>release=r); const running=new Promise(r=>started=r); raw.started=started;
  const job=link.remoteCommand(target,'','get tx'); await running;
  const close=link.close();
  await assert.rejects(link.remoteCommand(target,'','get tx'),/closing/);
  assert.equal(raw.path,'BBBBBB,CCCCCC'); assert.equal(raw.running,true);
  release(); assert.equal(await job,'OK'); await close;
  assert.equal(raw.path,'off'); assert.equal(raw.running,false);
});

test('after reboot the target ACL proof is reacquired', async () => {
  const raw=new FakeLink(), link=new RoutedRepeaterTransport(raw,target,[first]);
  await link.open(); await link.verifyPeer(target,''); await link.resetRemoteSession(target);
  assert.equal(raw.trace.filter(x=>x.startsWith('ota.peer acl '+target)).length,2);
  assert.ok(raw.trace.includes('ota.peer close')); await link.close();
});

test('does not reset a peer session this updater has not started', async () => {
  const raw=new FakeLink(), link=new RoutedRepeaterTransport(raw,target,[first]);
  await link.open();
  await assert.rejects(link.resetRemoteSession(target),/does not own/);
  assert.equal(raw.trace.includes('ota.peer close'),false);
  await link.close();
});

test('rejects external destinations, passwords and source prefix collisions', async () => {
  const raw=new FakeLink(), link=new RoutedRepeaterTransport(raw,target,[first]); await link.open();
  await assert.rejects(link.verifyPeer('e'.repeat(64),''),/outside/);
  await assert.rejects(link.verifyPeer(target,'password'),/ACL/);
  await assert.rejects(link.remoteCommand(target,'','reboot\nget tx'),/one nonempty/);
  await link.close();
  const raw2=new FakeLink(); raw2.sourceKey=first;
  await assert.rejects(new RoutedRepeaterTransport(raw2,target,[first]).open(),/distinct/);
});

test('a pre-existing path is never replaced', async () => {
  const raw=new FakeLink(); raw.path='EEEEEE';
  await assert.rejects(new RoutedRepeaterTransport(raw,target,[first]).open(),/existing OTA path/);
  assert.equal(raw.path,'EEEEEE');
});
