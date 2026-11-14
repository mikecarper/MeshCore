'use strict';
const assert = require('node:assert/strict');
const {test} = require('node:test');
const checks = require('../docs/_javascript/lora_ota_checks.js');

const STATUS = 'OTA hw=board | target:12345678 | maxblk:1024 | no download';
const SELF = 'self body=44 image=100 base_hash=1122334455667788 | maxblk:1024';
const NRF_STATUS = STATUS + ' | bl:internal';
const NRF_SELF = SELF + ' | bootloader: delta apply OK (abi=3 codecs=0x5)';

function state(status, self) {
  const result = checks.availability(status, self);
  assert.ok(result && typeof result === 'object');
  assert.ok(['unsupported', 'unavailable', 'unknown', 'supported'].includes(result.state));
  assert.equal(typeof result.message, 'string');
  assert.ok(result.message.trim(), 'Every result needs a human-readable explanation.');
  return result.state;
}

test('explicit unsupported OTA status is distinct from an unanswered request', () => {
  for (const reply of [
    'Unknown command', '> Unknown command', 'Error: unknown command',
    'ERR unsupported', 'Error: unsupported', 'Unsupported',
    'Unknown OTA command. Type `ota help`.',
    'LoRa OTA is not included in this build; tempradio cannot enable it. Use an OTA-enabled repeater firmware',
    'LoRa OTA absent; tempradio cannot enable it. Install OTA repeater firmware',
    'LoRa OTA absent; use OTA repeater; tempradio cannot enable it',
  ]) assert.equal(state(reply), 'unsupported', reply);
  for (const reply of ['', undefined, null, 'Remote command timed out; result is unknown',
    'request timed out; result is unknown', 'No reply after 11 attempts'])
    assert.equal(state(reply), 'unknown', String(reply));
});

test('permission failures and incidental unsupported words do not mean OTA is absent', () => {
  for (const reply of ['Error: permission denied', 'Access denied', 'ERR authentication required',
    'OTA error: request unsupported by this account', 'OTA | this fw unknown',
    'OTA | no download | unsupported optional command'])
    assert.equal(state(reply), 'unknown', reply);
});

test('a source-only seeder is not an update destination', () => {
  assert.equal(state('OTA seeder | install:disabled | target:00000000 | maxblk:1024 | serving:0 (source only)'), 'unsupported');
  assert.equal(state(STATUS, SELF + ' | source-only; local install disabled'), 'unsupported');
});

test('status-only bootloader and storage failures are immediately actionable', () => {
  for (const bad of ['bl:NONE', 'bl:NO-QSPI', 'bl:NO-SD', 'bl:storage-ERR',
    'QSPI store:ERR 0K', 'SD store:ERR', 'storage unsafe: hardware selection failed'])
    assert.equal(state(STATUS + ' | ' + bad), 'unavailable', bad);
});

test('self diagnostics distinguish missing apply support from missing replies', () => {
  for (const bad of ['bootloader: NO delta apply', 'bootloader: NO QSPI apply',
    'bootloader: NO SD mota-apply support (install will refuse)', 'storage unsafe: no usable store'])
    assert.equal(state(NRF_STATUS, SELF + ' | ' + bad), 'unavailable', bad);
  assert.equal(state(NRF_STATUS), 'unknown');
  assert.equal(state(NRF_STATUS, 'Error: permission denied'), 'unknown');
});

test('valid ESP32 and nRF52 evidence does not require a selected firmware package', () => {
  assert.equal(state(STATUS, SELF), 'supported');
  assert.equal(state(NRF_STATUS, NRF_SELF), 'supported');
  assert.equal(state(STATUS + ' | bl:QSPI', SELF + ' | bootloader: QSPI apply OK (abi=3 codecs=0x5)'), 'supported');
});

test('incomplete identity and old capability evidence stay unknown, not supported', () => {
  for (const status of ['OTA hw=board', 'OTA target:12345678',
    'OTA hw=? target:12345678', 'OTA hw=board target:00000000'])
    assert.equal(state(status, SELF), 'unknown', status);
  assert.equal(state(STATUS), 'unknown');
  assert.equal(state(STATUS, 'self body=44 image=100 base_hash=?'), 'unknown');
  assert.notEqual(state(NRF_STATUS, SELF + ' | bootloader: apply OK'), 'supported');
});

test('preflight availability cannot bypass the exact package compatibility checks', () => {
  assert.equal(state(STATUS, SELF), 'supported');
  const dest = checks.destination(STATUS, SELF, 'v1.17.1.1');
  const info = {targetId:0x12345679, hwId:'board', baseHash:new Uint8Array(8),
    fwVersion:checks.version('v1.18.0.1'), blockSize:1024, isFull:true, codecId:0};
  assert.throws(() => checks.validate(info, dest), /hardware or target/);
});

test('manual receiver check stops after a definitive first status reply', async () => {
  for (const [reply, expected] of [
    ['Unknown command', 'unsupported'],
    ['OTA seeder | install:disabled | target:00000000 (source only)', 'unsupported'],
    [STATUS + ' | bl:NONE', 'unavailable'],
    [STATUS + ' | storage unsafe: no usable backend', 'unavailable'],
  ]) {
    const commands = [];
    await assert.rejects(checks.readReceiver(async command => {
      commands.push(command);
      assert.equal(command, 'ota status', 'No second read is appropriate after a definitive rejection.');
      return reply;
    }), error => {
      assert.equal(error.otaAvailability?.state, expected);
      assert.equal(error.message, error.otaAvailability.message);
      return true;
    });
    assert.deepEqual(commands, ['ota status']);
  }
});

test('manual receiver check stops after self reports a storage failure', async () => {
  const commands = [];
  await assert.rejects(checks.readReceiver(async command => {
    commands.push(command);
    return command === 'ota status' ? STATUS : SELF + ' | storage unsafe: missing flash';
  }), error => {
    assert.equal(error.otaAvailability?.state, 'unavailable');
    return true;
  });
  assert.deepEqual(commands, ['ota status', 'ota self']);
});

test('manual receiver check returns supported identity after exactly two reads', async () => {
  const commands = [];
  const result = await checks.readReceiver(async command => {
    commands.push(command);
    return '> ' + (command === 'ota status' ? STATUS : SELF);
  });
  assert.deepEqual(commands, ['ota status', 'ota self']);
  assert.deepEqual(result, {status:STATUS, self:SELF});
});

test('manual receiver check preserves timeout and transport failures unchanged', async () => {
  for (const failAt of ['ota status', 'ota self']) {
    const timeout = Object.assign(Error('Remote command timed out; result is unknown'), {loraReplyTimeout:true});
    const commands = [];
    await assert.rejects(checks.readReceiver(async command => {
      commands.push(command);
      if (command === failAt) throw timeout;
      return STATUS;
    }), error => {
      assert.strictEqual(error, timeout);
      assert.equal(error.otaAvailability, undefined, 'A missing reply cannot prove the receiver is unsupported.');
      return true;
    });
    assert.deepEqual(commands, failAt === 'ota status' ? ['ota status'] : ['ota status', 'ota self']);
  }
});

function coldRakFixture({hw='RAK4631', backend='QSPI', auto=false}={}) {
  const header = `OTA | this fw 00112233 (743K) hw=${hw} | target:1234ABCD | maxblk:2048 | bl:`;
  const prefix = 'self body=100 image=156 base_hash=0011223344556677 | maxblk:2048 abi=3 codecs=0x5 | ';
  const model = hw === 'RAK_3401' ? '3401' : '4631';
  const target = ({'3401_DFU':'23818A80','3401_AUTO_DFU':'D04AB3AB',
    '4631_DFU':'2D0DF000','4631_AUTO_DFU':'FEEAFD1B'})[model+(auto?'_AUTO_DFU':'_DFU')];
  const tail = {internal:'internal store:52K | bootloader: delta apply OK',
    QSPI:'QSPI store:2048K | bootloader: QSPI apply OK',
    SD:'SD store | bootloader: authenticated SD apply OK'}[backend];
  return {
    beforeStatus:header+'storage-ERR blrc:00 | no download',
    beforeSelf:prefix+'storage unsafe: not probed',
    boot:`BL board=239A0029 target=${target} name=${model}${auto?'_AUTO':''}_DFU crc=FF374B1B abi=3 caps=${auto?'1E':'0A'} | staged:none mid=- hash=-`,
    afterStatus:header+backend+' blrc:00 | no download', afterSelf:prefix+tail
  };
}

function coldReader(fixture, calls) {
  let statuses=0, selves=0;
  return async command => {
    calls.push(command);
    if(command==='ota status') return ++statuses===1 ? fixture.beforeStatus : fixture.afterStatus;
    if(command==='ota self') return ++selves===1 ? fixture.beforeSelf : fixture.afterSelf;
    if(command==='ota bootloader status') return fixture.boot;
    throw Error('Unexpected command: '+command);
  };
}

test('cold RAK storage is prepared and rechecked for both board families', async () => {
  for(const [hw,backend,auto] of [['RAK4631','QSPI',false],['RAK4631','SD',true],
    ['RAK_3401','internal',false],['RAK_3401','QSPI',true]]) {
    const fixture=coldRakFixture({hw,backend,auto}), calls=[];
    const result=await checks.readReceiver(coldReader(fixture,calls));
    assert.deepEqual(calls,['ota status','ota self','ota bootloader status','ota status','ota self']);
    assert.deepEqual(result,{status:fixture.afterStatus,self:fixture.afterSelf});
    assert.equal(checks.availability(result.status,result.self).state,'supported');
  }
});

test('cold preparation does not hide real errors or touch other boards', async () => {
  const fixture=coldRakFixture(), calls=[];
  fixture.beforeSelf=fixture.beforeSelf.replace('not probed','no usable store');
  await assert.rejects(checks.readReceiver(coldReader(fixture,calls)), e=>e.otaAvailability?.state==='unavailable');
  assert.deepEqual(calls,['ota status','ota self']);
  assert.equal(checks.coldRakStatus(fixture.beforeStatus.replace('RAK4631','OtherBoard')),null);
  assert.equal(checks.coldRakStatus(fixture.beforeStatus.replace('no download','download: active')),null);
});

test('cold preparation requires matching bootloader, image, and storage evidence', async () => {
  for(const mutate of [
    f=>f.boot=f.boot.replace('2D0DF000','23818A80'),
    f=>f.boot=f.boot.replace('crc=FF374B1B','crc=00000000'),
    f=>f.boot=f.boot.replace('staged:none','staged:ready'),
    f=>f.afterSelf=f.afterSelf.replace('0011223344556677','A011223344556677'),
    f=>f.afterStatus=f.afterStatus.replace('target:1234ABCD','target:11223344'),
    f=>f.afterStatus=f.afterStatus.replace('bl:QSPI','bl:internal'),
    f=>f.afterSelf=f.afterSelf.replace('QSPI store:2048K','QSPI store:0K'),
  ]) {
    const fixture=coldRakFixture(), calls=[]; mutate(fixture);
    await assert.rejects(checks.readReceiver(coldReader(fixture,calls)));
  }
});

test('cold storage command timeouts remain unknown failures', async () => {
  const fixture=coldRakFixture(), calls=[], read=coldReader(fixture,calls);
  const timeout=Object.assign(Error('request timed out; result is unknown'),{loraReplyTimeout:true});
  await assert.rejects(checks.readReceiver(async command => {
    if(command==='ota bootloader status') throw timeout;
    return read(command);
  }), error=>error===timeout && error.otaAvailability===undefined);
});
