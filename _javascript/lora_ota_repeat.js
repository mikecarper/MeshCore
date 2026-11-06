/* Explicit forwarding control; never silently change a relay used by the route. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LoraOtaRepeat = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  function parse(reply) {
    const value = String(reply).trim().replace(/^>\s*/, '').toLowerCase();
    if (value !== 'on' && value !== 'off') throw Error('Could not read the repeater forwarding setting');
    return value;
  }
  async function control(link, key, password, routeRelays, action, setting = 'repeat') {
    if (!link) throw Error('Connect the source repeater first');
    if (!/^[0-9a-f]{64}$/i.test(key)) throw Error('Enter the complete repeater public key');
    if (!['repeat', 'radio.flood', 'radio2.flood'].includes(setting)) throw Error('Invalid forwarding setting');
    const local = key.toLowerCase() === link.sourceKey.toLowerCase();
    if (action === 'off' && routeRelays.some(k => k.toLowerCase() === key.toLowerCase()))
      throw Error('This repeater is part of the selected route. Turning forwarding off would interrupt the update.');
    const command = local ? c => link.localCommand(c) : c => link.remoteCommand(key, password, c);
    if (!local) await link.verifyPeer(key, password);
    let current = parse(await command('get ' + setting));
    if (action === 'read' || current === action) return current;
    if (action !== 'on' && action !== 'off') throw Error('Invalid forwarding setting');
    // A lost reply may mean the setting changed. Do not issue a second write.
    try { await command('set ' + setting + ' ' + action); }
    catch (error) { throw Error('Forwarding change was not confirmed. Check the repeater before retrying: ' + error.message); }
    current = parse(await command('get ' + setting));
    if (current !== action) throw Error('The repeater did not confirm the requested forwarding setting');
    return current;
  }
  return {parse, control};
});
