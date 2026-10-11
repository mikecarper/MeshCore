#pragma once

#include <Arduino.h>

// Offline browser room client, served by the existing WebConfig listener.
// No radio private key is imported; a separate random browser token identifies
// posts and moderation. Keep untrusted room text out of HTML interpolation.
static const char ROOM_WEB_HTML[] PROGMEM = R"roomhtml(<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MeshCore Room</title><style>
:root{color-scheme:dark}body{font:16px system-ui;background:#111820;color:#eee;max-width:1000px;margin:auto;padding:20px}
input,textarea,button,select{font:inherit;border:1px solid #6b7786;border-radius:6px;padding:9px;background:#202b38;color:inherit}
input,textarea{box-sizing:border-box;width:100%;margin:5px 0 14px}textarea{min-height:85px}button{cursor:pointer;margin:4px 6px 4px 0}
button:disabled{opacity:.45;cursor:default}label{display:block}.muted{color:#b5beca}#error{color:#ffb9b9;white-space:pre-wrap}
#topic,#article,.post{white-space:pre-wrap;overflow-wrap:anywhere}#article{border:1px solid #6b7786;padding:14px}
.post{padding:10px 0;border-bottom:1px solid #364351}.post small{display:block;color:#b5beca}
details{margin:18px 0}summary{cursor:pointer}code{overflow-wrap:anywhere}a{color:#a8d5ff}[hidden]{display:none!important}
.panel{border:1px solid #526273;border-radius:10px;padding:18px;margin:20px 0}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}.card{background:#202b38;padding:12px;border-radius:6px}.card strong{display:block;font-size:1.3em}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:.92em}th,td{text-align:left;padding:9px 7px;border-bottom:1px solid #364351;vertical-align:top}th{color:#b5beca}.key{font-family:monospace;overflow-wrap:anywhere}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px}.notice{padding:10px;background:#273947;border-radius:6px}.warning{color:#ffd3a3}.success{color:#a5e1b1}dl{display:grid;grid-template-columns:minmax(110px,1fr) 2fr;gap:8px}dt{color:#b5beca}dd{margin:0;overflow-wrap:anywhere}select{max-width:100%}.inline{display:flex;flex-wrap:wrap;align-items:center;gap:8px}.inline label{display:inline}.inline input{width:auto;margin:0}fieldset{border:1px solid #526273;border-radius:6px;margin:14px 0;padding:12px}.small{font-size:.9em}
#mail-panel fieldset,#mail-panel .grid>form{min-width:0}#mail-panel select{width:100%}
</style></head><body>
<h1 id="room-name">MeshCore Room</h1>
<p class="muted">Chat, personal mail and selected notices over local Wi-Fi. This page also works without internet access.</p>
<form id="join"><label for="password">Room password (leave empty for public read-only access)</label>
<input id="password" type="password" autocomplete="current-password" maxlength="64">
<label for="name">Your display name</label><input id="name" autocomplete="nickname" value="Web">
<button type="submit">Connect</button></form>
<p id="error" role="alert"></p><p id="state" class="muted" role="status"></p>
<section id="room" hidden><p id="topic"></p>
<details><summary>Access and history</summary><p id="history"></p>
<p>Your browser identity for room moderation: <code id="web-key"></code></p>
<p class="muted">This browser identity is separate from a radio's public key. The password stays in memory for this session.</p></details>
<button id="refresh" type="button">Refresh chat</button><button id="disconnect" type="button">Disconnect</button><div id="posts" aria-live="polite"></div>
<details id="browser-catchup"><summary>Choose chat shown in this browser</summary>
<p class="muted">Filter the room's retained history in this browser. This changes only your local view and does not delete history or change a radio's delivery cursor.</p>
<form id="browser-catchup-form" class="grid"><div><label for="browser-skip-date">Show messages from this date and time (local time)</label><input id="browser-skip-date" type="datetime-local"><button id="browser-skip-before" type="button">Show from date</button></div>
<div><label for="browser-skip-count">Show the newest number of retained messages</label><input id="browser-skip-count" type="number" min="1" max="32" value="10"><button id="browser-skip-number" type="button">Show newest messages</button></div></form>
<p id="browser-catchup-result" class="muted" role="status"></p></details>
<form id="compose"><label for="message">Message</label><textarea id="message"></textarea>
<p id="budget" class="muted"></p><button id="send" type="submit">Send</button></form>
<section class="panel" aria-labelledby="mail-heading"><h2 id="mail-heading">Personal mailbox</h2>
<p class="muted">Mail waits on this room until you collect it. Your browser mailbox uses its own identity, separate from your radio.</p>
<p id="mail-summary" class="notice" hidden></p>
<button id="mail-open" type="button">Open my mailbox</button>
<section id="mail-panel" hidden><p>Your complete mailbox address: <code id="mail-key" class="key"></code> <button id="mail-copy" type="button">Copy address</button></p>
<p id="mail-state" class="notice" role="status"></p>
<div class="grid"><form id="mail-policy"><fieldset><legend>Who can send you mail</legend>
<label for="mail-mode">Mailbox access</label><select id="mail-mode"><option value="closed">Closed - receive no new mail</option><option value="public">Public - room writers may send</option><option value="private">Private - approved senders only</option></select>
<label for="mail-allowlist">Approved sender addresses (up to 8 complete public keys, one per line)</label><textarea id="mail-allowlist" class="key" spellcheck="false" autocomplete="off"></textarea>
<label class="inline"><input id="mail-only" type="checkbox"> Mailbox only: pause automatic chat downloads</label>
<p class="muted small">A closed mailbox is the default. Reading mail leaves it queued; confirm receipt after a complete download to remove it from the server.</p>
<button id="mail-policy-save" type="submit">Save mailbox settings</button><button id="mail-policy-reload" type="button">Reload settings</button><p id="mail-policy-result" role="status"></p></fieldset></form>
<form id="mail-compose"><fieldset><legend>Send personal mail</legend><label for="mail-recipient">Recipient's complete mailbox address</label><input id="mail-recipient" class="key" maxlength="64" spellcheck="false" autocomplete="off">
<label for="mail-text">Message (up to 512 UTF-8 bytes)</label><textarea id="mail-text"></textarea><p id="mail-budget" class="muted"></p>
<button id="mail-send" type="submit">Send mail</button><p id="mail-send-result" role="status"></p></fieldset></form></div>
<h3>Inbox</h3><button id="mail-refresh" type="button">Check and refresh inbox</button><p id="mail-count" role="status"></p>
<div class="table-wrap"><table><thead><tr><th scope="col">Sender</th><th scope="col">Sent</th><th scope="col">Receipt</th><th scope="col">Message</th></tr></thead><tbody id="mail-rows"></tbody></table></div>
<div class="inline"><button id="mail-previous" type="button" disabled>Previous mail</button><button id="mail-next" type="button" disabled>Next mail</button><span id="mail-page" class="muted"></span></div>
<section id="mail-selected" hidden><h3>Selected mail</h3><p id="mail-selected-meta" class="key"></p><pre id="mail-body" class="notice" style="white-space:pre-wrap;overflow-wrap:anywhere"></pre>
<p id="mail-read-state" role="status"></p><button id="mail-resume" type="button">Read or resume download</button><button id="mail-ack" type="button" disabled>Confirm received and remove</button><button id="mail-delete" type="button">Delete this mail</button></section></section></section>
<h2>Information board</h2><button id="refresh-board" type="button">Refresh notices</button>
<div id="notices"></div><h3 id="article-title" hidden></h3><pre id="article" hidden></pre>
<section id="editor" hidden><h3>Publish or edit a notice</h3><form id="publish">
<label for="article-id">Article slot (1-8)</label><select id="article-id">
<option>1</option><option>2</option><option>3</option><option>4</option><option>5</option><option>6</option><option>7</option><option>8</option></select>
<label for="title">Title (up to 63 UTF-8 bytes)</label><input id="title">
<label for="body">Article (up to 2,048 UTF-8 bytes)</label><textarea id="body"></textarea>
<button type="submit">Save notice</button><button id="delete" type="button">Delete selected notice</button></form></section>
<section id="admin-panel" class="panel" hidden><h2>Room administration</h2>
<p class="muted">Manage radio users, their room-to-user routes, and chat delivery. Activity and delivery details are available only with admin access.</p>
<details id="admin-mail"><summary>Mailbox owners and delivery</summary><p class="muted">Review mailbox policies and counts. Private message bodies are available only to their recipients.</p><button id="admin-mail-refresh" type="button">Load mailbox owners</button><p id="admin-mail-state" role="status"></p>
<label for="admin-mail-owner">Manage a radio or browser mailbox by its complete public key</label><input id="admin-mail-owner" class="key" maxlength="64" spellcheck="false" autocomplete="off"><button id="admin-mail-load-owner" type="button">Load mailbox by address</button><p class="muted small">A new address starts closed. Save its policy to create the mailbox.</p>
<div class="table-wrap"><table><thead><tr><th scope="col">Complete owner address</th><th scope="col">Access</th><th scope="col">Mail</th><th scope="col">Manage</th></tr></thead><tbody id="admin-mail-rows"></tbody></table></div>
<div class="inline"><button id="admin-mail-previous" type="button" disabled>Previous mailboxes</button><button id="admin-mail-next" type="button" disabled>Next mailboxes</button><span id="admin-mail-page" class="muted"></span></div>
<form id="admin-mail-policy" hidden><fieldset><legend>Selected mailbox policy</legend><p id="admin-mail-key" class="key"></p><label for="admin-mail-mode">Mailbox access</label><select id="admin-mail-mode"><option value="closed">Closed</option><option value="public">Public</option><option value="private">Private</option></select>
<label for="admin-mail-allowlist">Approved senders (up to 8 complete public keys, one per line)</label><textarea id="admin-mail-allowlist" class="key" spellcheck="false" autocomplete="off"></textarea>
<label class="inline"><input id="admin-mail-only" type="checkbox"> Mailbox only</label><button id="admin-mail-save" type="submit">Save selected policy</button><button id="admin-mail-reload" type="button">Reload selected policy</button><button id="admin-mail-block" type="button">Block owner</button>
<label for="admin-mail-delete-id">Message ID to delete</label><input id="admin-mail-delete-id" type="number" min="1" max="4294967295"><button id="admin-mail-delete" type="button">Review message deletion</button><button id="admin-mail-purge" type="button">Review inbox purge</button><p id="admin-mail-result" role="status"></p></fieldset></form></details>
<div class="inline"><button id="admin-refresh" type="button">Load users and delivery</button><label><input id="admin-live" type="checkbox"> Refresh every 15 seconds</label></div>
<p id="admin-state" role="status" class="muted">Load users to begin.</p>
<div id="admin-cards" class="cards" hidden></div>
<div class="table-wrap"><table id="admin-users" hidden><thead><tr><th scope="col">User</th><th scope="col">Access</th><th scope="col">Activity</th><th scope="col">Unread retained</th><th scope="col">Delivery</th><th scope="col">Manage</th></tr></thead><tbody id="admin-user-rows"></tbody></table></div>
<div class="inline"><button id="admin-previous" type="button" disabled>Previous users</button><button id="admin-next" type="button" disabled>Next users</button><span id="admin-page" class="muted"></span></div>
<section id="admin-selected" hidden><h3>Selected radio user</h3><p id="admin-selected-key" class="key"></p><button id="admin-selected-refresh" type="button">Reload selected user</button><dl id="admin-details"></dl>
<div class="grid"><form id="admin-outpath"><fieldset><legend>Primary route (outpath)</legend><label for="admin-out-mode">Route mode</label><select id="admin-out-mode"><option value="path">Explicit path</option><option value="direct">Direct</option><option value="flood">Flood</option><option value="clear">Clear saved route</option></select><label for="admin-out-value">Repeater path</label><input id="admin-out-value" spellcheck="false" autocomplete="off" placeholder="A1,B2 or A100,B200"><button type="submit">Save primary route</button></fieldset></form>
<form id="admin-altpath"><fieldset><legend>Alternate route (altpath)</legend><label for="admin-alt-mode">Route mode</label><select id="admin-alt-mode"><option value="path">Explicit path</option><option value="direct">Direct</option><option value="clear">No secondary copy (clear)</option></select><label for="admin-alt-value">Repeater path</label><input id="admin-alt-value" spellcheck="false" autocomplete="off" placeholder="C1,D2 or C100,D200"><button type="submit">Save alternate route</button></fieldset></form></div>
<p class="muted small">These are routes from the room server to this user. A route change does not grant access or change the user's role. Clear on the primary route allows normal route discovery. Clear on the alternate route disables the secondary copy; an alternate route is an additional copy, not a second delivery queue.</p>
<fieldset><legend>Advance this user's chat catch-up</legend><p class="warning">Skipped messages remain in room history but will no longer be delivered as unread chat to this user. Other users are unchanged.</p>
<div class="grid"><div><label for="admin-skip-date">Skip chat before this date and time (local time)</label><input id="admin-skip-date" type="datetime-local"><button id="admin-skip-before" type="button">Review date skip</button></div><div><label for="admin-skip-count">Keep the newest number of unread retained messages (0 skips all)</label><input id="admin-skip-count" type="number" min="0" max="32" value="10"><button id="admin-skip-number" type="button">Review older-message skip</button></div></div></fieldset>
<fieldset><legend>User access and moderation</legend><p class="muted small">Saved access uses this complete public key. Blocking also stops an existing room session. Password-based access remains separate from a saved ACL role.</p>
<label for="admin-access-role">Saved role</label><select id="admin-access-role"><option value="0">Remove saved access</option><option value="1">Read only</option><option value="2">Read and write</option><option value="3">Admin</option></select><button id="admin-access-save" type="button">Review access change</button><button id="admin-ban-selected" type="button">Block selected user</button></fieldset>
<p id="admin-result" role="status"></p></section>
<details><summary>Room settings and blocked identities</summary><button id="admin-settings-load" type="button">Load room settings</button>
<section id="admin-settings-panel" hidden><div class="grid"><form id="admin-topic"><label for="admin-topic-value">Room topic (up to 151 UTF-8 bytes)</label><textarea id="admin-topic-value"></textarea><button type="submit">Save topic</button></form>
<div><label><input id="admin-history-enabled" type="checkbox"> Keep the newest 32 chat posts across reboots</label><button id="admin-history-save" type="button">Save history setting</button><p class="muted small">Turning this off keeps the existing archive; it does not erase messages.</p></div>
<form id="admin-rates"><label for="admin-post-rate">Posts per user per minute (0 disables this limit)</label><input id="admin-post-rate" type="number" min="0" max="65535"><label for="admin-poll-rate">Polls per user per minute (0 disables this limit)</label><input id="admin-poll-rate" type="number" min="0" max="65535"><button type="submit">Save rate limits</button></form></div><p id="admin-settings-result" role="status"></p></section>
<fieldset><legend>Block or unblock a complete identity</legend><label for="admin-ban-key">Radio or browser public identity (64 hexadecimal characters)</label><input id="admin-ban-key" class="key" spellcheck="false" autocomplete="off" maxlength="64"><button id="admin-ban-add" type="button">Review block</button><button id="admin-ban-remove" type="button">Review unblock</button><p id="admin-ban-result" role="status"></p></fieldset></details></section>
</section><p><a href="/">Radio configuration</a></p>
<script>
'use strict';
const $=id=>document.getElementById(id), encoder=new TextEncoder();
let token='', sequence=0, password='', boot=0, role=0, after=0, joined=false, polling=false, connectionGeneration=0;
let chain=Promise.resolve(), notices=new Map(), transfers=new Map();
let sending=false, editId=1, editVersion=0;
let adminLoaded=false, adminLoading=false, adminBusy=false, adminCursor=0, adminNext=null, adminPages=[];
let selectedKey='', selectedUser=null, adminSettings=null;
let mailPolicy=null,mailDirty=false,mailBusy=false,mailCursor=0,mailNext=null,mailPages=[],mailItems=new Map(),mailTransfers=new Map(),mailSelected=null,mailGeneration=0,mailboxOnly=false,mailSnapshot=0;
let adminMailPolicy=null,adminMailDirty=false,adminMailBusy=false,adminMailCursor=0,adminMailNext=null,adminMailPages=[],adminMailSnapshot=0;
const routeDirty={outpath:false,altpath:false},routeBaseline={outpath:'',altpath:''};
function saved(key,fallback){try{return localStorage.getItem(key)||fallback}catch(e){return fallback}}
function remember(key,value){try{localStorage.setItem(key,String(value))}catch(e){}}
function makeIdentity(){
  token=saved('mc-room-token','');
  if(!/^[0-9a-f]{64}$/.test(token)||/^0+$/.test(token)){
    const bytes=new Uint8Array(32);crypto.getRandomValues(bytes);
    token=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');remember('mc-room-token',token);
  }
  sequence=Number(saved('mc-room-sequence','0'));
  if(!Number.isSafeInteger(sequence)||sequence<0||sequence>=4294967295) throw Error("Browser sequence exhausted. Clear this site's stored room identity to reconnect.");
}
function message(error){$('error').textContent=error?String(error.message||error):''}
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function fetchBounded(url,options){
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),5000);
  try{return await fetch(url,Object.assign({},options,{signal:controller.signal,cache:'no-store'}))}
  finally{clearTimeout(timer)}
}
function api(op,values={}){
  const intent=Object.assign({},values,{boot:boot}),generation=connectionGeneration,secret=password;
  const run=()=>request(op,intent,generation,secret), result=chain.then(run,run);
  chain=result.catch(()=>{});return result;
}
async function request(op,values,generation,secret){
  if(generation!==connectionGeneration)throw Error('Room connection changed. Reconnect before trying again.');
  if(++sequence>4294967295)throw Error('Browser sequence exhausted.');
  remember('mc-room-sequence',sequence);
  const headers={'Content-Type':'application/json','X-Room-Token':token,'X-Room-Seq':String(sequence)};
  const body=JSON.stringify(Object.assign({},values,{op:op,password:secret}));
  if(encoder.encode(body).length>4096)throw Error("Request exceeds the radio's 4 KB limit.");
  const deadline=Date.now()+20000;
  let accepted=false;
  // An interrupted submission retries the identical sequence and body. Once
  // admitted, only poll its result. Never silently resend with a new identity.
  while(Date.now()<deadline){
    if(generation!==connectionGeneration)throw Error('Room connection changed. Reconnect before trying again.');
    let response;
    try{response=await fetchBounded(accepted?'/api/room/result':'/api/room',
      {method:'POST',headers:headers,body:accepted?undefined:body})}
    catch(e){await pause(400);continue}
    if(!accepted&&response.status===429){await pause(500);continue}
    if(response.status===202){accepted=true;await pause(250);continue}
    let result;
    try{result=await response.json()}catch(e){throw Error('Invalid response; refresh to check before retrying.')}
    if(generation!==connectionGeneration)throw Error('Room connection changed. Reconnect before trying again.');
    if(!response.ok||result.error)throw Error(result.error||('Room request failed ('+response.status+').'));
    return result;
  }
  throw Error('No confirmation received. Refresh and check the room before trying the operation again.');
}
function updateBudget(){
  const size=encoder.encode($('name').value+': '+$('message').value).length;
  $('budget').textContent=size+' / 151 UTF-8 bytes (including your name)';
  $('send').disabled=sending||!joined||!(role===2||role===3)||size>151||!$('message').value.trim();
}
async function status(){
  const result=await api('status');
  if(boot&&boot!==result.boot){after=0;$('posts').replaceChildren();transfers.clear();notices.clear();clearAdmin();clearMail()}
  if(role>result.role)clearMail();
  boot=result.boot;role=result.role;
  mailboxOnly=result.mailbox_only===true;updateMailSummary();
  $('room-name').textContent=result.name;$('topic').textContent=result.topic;
  $('web-key').textContent=result.web_key;
  $('history').textContent=result.persistent_history?'Radio retains its newest 32 posts across reboots.':'Radio history is in RAM and is lost on reboot.';
  $('state').textContent=role===3?'Admin access':role===2?'Read and write access':'Read-only access';
  $('editor').hidden=role!==3;$('admin-panel').hidden=role!==3;
  if(role!==3)clearAdmin();updateAdminButtons();updateBudget();
}
async function refreshChat(automatic=false){
  if(polling||!joined)return;polling=true;
  try{
    await status();
    if(automatic&&mailboxOnly)return;
    for(let i=0;i<32;i++){
      const result=await api('posts',{after:after}), post=result.post;if(!post)break;
      if(!Number.isInteger(post.timestamp)||post.timestamp<=after)throw Error('Invalid message cursor.');
      const row=document.createElement('div'),stamp=document.createElement('small'),text=document.createElement('div');
      row.className='post';stamp.textContent=new Date(post.timestamp*1000).toLocaleString()+' - '+post.author.slice(0,12);
      text.textContent=post.text;row.append(stamp,text);$('posts').append(row);
      while($('posts').childElementCount>32)$('posts').firstElementChild.remove();
      after=post.timestamp;
    }
  }finally{polling=false}
}
async function refreshBoard(){
  let cursor=0,revision=0,next=new Map();
  for(let page=0;page<8;page++){
    const result=await api('board.index',{revision:revision,cursor:cursor});
    if(revision&&result.revision!==revision)throw Error('Board changed; refresh notices.');
    revision=result.revision;
    for(const item of result.articles)next.set(item.id,item);
    if(result.next===255){notices=next;renderNotices();return}
    if(!Number.isInteger(result.next)||result.next<=cursor||result.next>8)throw Error('Invalid board cursor.');
    cursor=result.next;
  }
  throw Error('Board index did not finish.');
}
function renderNotices(){
  $('notices').replaceChildren();
  if(!notices.size){$('notices').textContent='No notices published.';return}
  for(const item of notices.values()){
    const button=document.createElement('button');button.type='button';button.textContent=item.title;
    button.addEventListener('click',()=>openArticle(item).catch(message));$('notices').append(button);
  }
}
function retainedCount(id,minimum=1){
  const value=$(id).value;
  if(!/^\d+$/.test(value)||Number(value)<minimum||Number(value)>32)throw Error('Choose a number from '+minimum+' to 32 retained messages.');
  return Number(value);
}
function dateSeconds(id){
  const value=$(id).value,seconds=Math.floor(new Date(value).getTime()/1000);
  if(!value||!Number.isInteger(seconds)||seconds<1||seconds>4294967295)throw Error('Choose a valid date and time.');
  return seconds;
}
async function browserCatchup(mode){
  if(!joined)throw Error('Connect to the room first.');
  if(polling)throw Error('Chat is refreshing. Try again when it finishes.');
  const before=mode==='before'?dateSeconds('browser-skip-date'):0;
  const count=mode==='keep'?retainedCount('browser-skip-count'):32;
  let skipped=0;polling=true;
  try{
    const unread=[];let cursor=0;
    for(let index=0;index<32;index++){
      const result=await api('posts',{after:cursor}),post=result.post;
      if(!post)break;
      if(!Number.isInteger(post.timestamp)||post.timestamp<=cursor)throw Error('Invalid message cursor.');
      if(mode==='before'&&post.timestamp>=before)break;
      // Advance only to an actual retained message. A future date must not
      // make this browser miss posts that arrive after this request.
      unread.push(post.timestamp);cursor=post.timestamp;
    }
    skipped=mode==='keep'?Math.max(0,unread.length-count):unread.length;
    after=skipped?unread[skipped-1]:0;
    $('posts').replaceChildren();$('browser-catchup-result').textContent='Hidden '+skipped+' older retained message'+(skipped===1?'':'s')+' in this browser view.';
  }finally{polling=false}
  await refreshChat();
}
function requireAdmin(){if(!joined||role!==3)throw Error('Admin permission required. Reconnect with the room admin password.');}
function clearAdmin(){
  clearAdminMail();
  adminLoaded=false;adminCursor=0;adminNext=null;adminPages=[];selectedKey='';selectedUser=null;adminSettings=null;
  routeDirty.outpath=false;routeDirty.altpath=false;routeBaseline.outpath='';routeBaseline.altpath='';
  $('admin-user-rows').replaceChildren();$('admin-details').replaceChildren();$('admin-cards').replaceChildren();
  $('admin-selected-key').textContent='';$('admin-users').hidden=true;$('admin-selected').hidden=true;$('admin-cards').hidden=true;
  $('admin-live').checked=false;$('admin-state').textContent='Load users to begin.';$('admin-page').textContent='';$('admin-result').textContent='';
  $('admin-out-value').value='';$('admin-alt-value').value='';$('admin-settings-panel').hidden=true;
  $('admin-topic-value').value='';$('admin-ban-key').value='';$('admin-settings-result').textContent='';$('admin-ban-result').textContent='';updateAdminButtons();
}
function roleName(value){return({0:'Guest',1:'Read only',2:'Read and write',3:'Admin',4:'Region manager',5:'Filter manager'})[value]||('Role '+value);}
function age(value){
  if(value===null||value===undefined)return 'Not heard this boot';
  if(value<60)return value+' seconds ago';if(value<3600)return Math.floor(value/60)+' minutes ago';
  return Math.floor(value/3600)+' hours ago';
}
function stamp(value){return value?new Date(value*1000).toLocaleString():'None';}
function deliveryName(user){
  if(user.banned)return 'Blocked';if(user.failures>=3)return 'Paused after failures';
  if(user.delivery==='topic')return 'Waiting for topic ACK';if(user.delivery==='post')return 'Waiting for post ACK';
  return user.pending_count?'Ready to deliver':'Caught up';
}
function updateAdminButtons(){
  const locked=adminBusy||adminLoading||role!==3;
  for(const button of $('admin-panel').querySelectorAll('button'))button.disabled=locked;
  $('admin-previous').disabled=locked||!adminPages.length;$('admin-next').disabled=locked||adminNext===null;
  for(const input of $('admin-panel').querySelectorAll('input,select,textarea'))if(input.id!=='admin-live')input.disabled=locked;
  for(const prefix of ['admin-out','admin-alt'])$(prefix+'-value').disabled=locked||$(prefix+'-mode').value!=='path';
  updateMailButtons();
}
function validKey(key){return typeof key==='string'&&/^[0-9a-fA-F]{64}$/.test(key)&&!/^0+$/.test(key);}
function renderAdminCards(result){
  $('admin-cards').replaceChildren();
  for(const [label,value] of [['Users',result.total],['Heard this boot',result.active],['Unread retained',result.backlog],['Awaiting ACK',result.pending],['Users with failures',result.failed]]){
    const card=document.createElement('div'),number=document.createElement('strong'),title=document.createElement('span');
    card.className='card';number.textContent=String(value);title.textContent=label;card.append(number,title);$('admin-cards').append(card);
  }
  $('admin-cards').hidden=false;
}
async function loadAdmin(cursor=adminCursor){
  requireAdmin();if(adminLoading||adminBusy)return;adminLoading=true;updateAdminButtons();
  try{
    const result=await api('admin.users',{cursor:cursor});requireAdmin();
    if(result.boot!==boot)throw Error('Radio restarted; refresh the room before managing users.');
    if(!Array.isArray(result.users)||result.users.length>2||result.users.some(user=>!validKey(user.key))
      ||(result.next!==null&&(!Number.isInteger(result.next)||result.next<=cursor||result.next>65535)))throw Error('Invalid user page.');
    adminCursor=cursor;adminNext=result.next;adminLoaded=true;renderAdminCards(result);$('admin-user-rows').replaceChildren();
    for(const user of result.users){
      const row=document.createElement('tr');
      for(const text of [user.key.slice(0,12)+(user.retained?' (saved)':''),roleName(user.role),age(user.heard_ago),String(user.pending_count),deliveryName(user)]){
        const cell=document.createElement('td');cell.textContent=text;row.append(cell);
      }
      const cell=document.createElement('td'),button=document.createElement('button');button.type='button';button.textContent='Select';
      button.addEventListener('click',()=>selectAdminUser(user.key,true).catch(message));cell.append(button);row.append(cell);$('admin-user-rows').append(row);
    }
    $('admin-users').hidden=false;$('admin-page').textContent='Page '+(adminPages.length+1);
    $('admin-state').textContent=result.users.length?'Updated '+new Date().toLocaleTimeString()+'. Heard this boot means recorded room activity, not guaranteed current radio reachability.':'No radio users retained or connected.';
    if(selectedKey)await selectAdminUser(selectedKey,false);
  }finally{adminLoading=false;updateAdminButtons()}
}
function addDetail(label,value){const term=document.createElement('dt'),description=document.createElement('dd');term.textContent=label;description.textContent=String(value);$('admin-details').append(term,description);}
function routeEditor(which,value,force){
  const prefix=which==='outpath'?'admin-out':'admin-alt';
  if(routeDirty[which]&&!force){
    if(routeBaseline[which]!==value)$('admin-result').textContent='A route changed on the radio. Your draft is preserved; refresh the selected user before replacing it.';
    return;
  }
  const mode=value==='direct'?'direct':value==='flood'?(which==='altpath'?'clear':'flood'):value==='unknown'||value==='clear'?'clear':'path';
  $(prefix+'-mode').value=mode;$(prefix+'-value').value=mode==='path'?value:'';$(prefix+'-value').disabled=mode!=='path'||adminBusy;
  routeBaseline[which]=value;routeDirty[which]=false;
}
async function selectAdminUser(key,force=false){
  requireAdmin();if(!validKey(key))throw Error('Select a complete radio public key.');
  const previous=selectedKey;selectedKey=key;
  try{
    const result=await api('admin.user',{key:key});requireAdmin();if(selectedKey!==key)return;
    if(result.boot!==boot||!result.user||result.user.key!==key)throw Error('User changed or radio restarted; refresh users.');
    selectedUser=result.user;$('admin-selected-key').textContent=key;$('admin-selected').hidden=false;$('admin-details').replaceChildren();
    const user=selectedUser;
    for(const [label,value] of [['Access',roleName(user.role)+(user.retained?' (saved ACL)':' (session only)')],['Activity',user.active?'Heard this boot':'No allowed room activity this boot'],['Last activity',age(user.heard_ago)],['Delivery',deliveryName(user)],['Unread retained posts',user.pending_count],['Chat cursor',stamp(user.sync_since)],['Pending post',stamp(user.pending_post)],['Topic revision waiting',user.topic_pending||'None'],['ACK wait',user.ack_wait_ms===null?'None':user.ack_wait_ms+' ms'],['Delivery failures',user.failures],['Primary route',user.outpath],['Alternate route',user.altpath],['Observed return path',user.observed_pending?'Awaiting path discovery':user.observed]])addDetail(label,value);
    routeEditor('outpath',user.outpath,force||previous!==key);routeEditor('altpath',user.altpath,force||previous!==key);
    $('admin-access-role').value=String(user.role<=3?user.role:1);$('admin-ban-selected').textContent=user.banned?'Unblock selected user':'Block selected user';
    updateAdminButtons();
  }catch(error){if(selectedKey===key&&previous!==key){selectedKey='';selectedUser=null;$('admin-selected').hidden=true}throw error}
}
async function adminMutation(action,success){
  requireAdmin();if(adminBusy)return;adminBusy=true;updateAdminButtons();$('admin-result').textContent='';
  try{await action();requireAdmin();$('admin-result').textContent=typeof success==='function'?success():success;}
  finally{adminBusy=false;updateAdminButtons()}
}
async function saveAdminRoute(which){
  if(!selectedUser)throw Error('Select a radio user first.');
  const prefix=which==='outpath'?'admin-out':'admin-alt',mode=$(prefix+'-mode').value;
  const value=mode==='path'?$(prefix+'-value').value.trim():mode;
  if(mode==='path'&&(!value||value.length>191||!/^[0-9a-fA-F, \t]+$/.test(value)))throw Error('Use a hexadecimal repeater path separated by commas or spaces.');
  const key=selectedKey,expected=routeBaseline[which];
  await adminMutation(async()=>{await api('admin.route',{key:key,which:which,value:value,expected:expected});
    if(selectedKey===key){routeDirty[which]=false;await selectAdminUser(key,false);}},'Saved '+which+' for '+key.slice(0,12)+'.');
}
async function adminCatchup(mode){
  requireAdmin();if(!selectedUser)throw Error('Select a radio user first.');
  const key=selectedKey,values={key:key,mode:mode,expected_sync:selectedUser.sync_since};
  let explanation;
  if(mode==='before'){values.before=dateSeconds('admin-skip-date');explanation='Skip unread chat before '+new Date(values.before*1000).toLocaleString();}
  else{values.count=retainedCount('admin-skip-count',0);explanation=values.count===0?'Skip all unread retained chat messages':'Keep only the newest '+values.count+' unread retained messages and skip the older backlog';}
  if(!confirm(explanation+' for radio '+key+'?\nThis advances only this user and does not delete room history.'))return;
  await adminMutation(async()=>{const result=await api('admin.catchup',values);if(selectedKey===key)await selectAdminUser(key,false);
    $('admin-state').textContent='Skipped '+result.skipped+' retained messages for '+key.slice(0,12)+'.';},'Chat catch-up updated.');
}
async function saveAdminAccess(){
  requireAdmin();if(!selectedUser)throw Error('Select a radio user first.');
  const key=selectedKey,selectedRole=Number($('admin-access-role').value),expected=selectedUser.role;
  if(!confirm('Set saved access to '+(selectedRole===0?'none':roleName(selectedRole))+' for radio '+key+'?'))return;
  let pendingSave=false;
  await adminMutation(async()=>{const result=await api('admin.access',{key:key,role:selectedRole,expected_role:expected});pendingSave=result.pending_save===true;
    if(selectedKey===key){
      if(selectedRole===0){selectedKey='';selectedUser=null;$('admin-selected').hidden=true;$('admin-selected-key').textContent='';$('admin-details').replaceChildren();routeDirty.outpath=false;routeDirty.altpath=false;}
      else await selectAdminUser(key,false);
    }},()=>pendingSave?'Access updated; keep power on for at least 5 seconds while it is saved.':'Access updated.');
  if(adminLoaded)await loadAdmin();
}
async function setAdminBan(key,banned){
  requireAdmin();if(!validKey(key))throw Error('Use a complete nonzero 64-character public identity.');
  if(!confirm((banned?'Block ':'Unblock ')+key+' for room access?'))return;
  await adminMutation(async()=>{await api('admin.ban',{key:key,banned:banned});
    if(selectedKey===key)await selectAdminUser(key,false);},banned?'Identity blocked.':'Identity unblocked.');
  $('admin-ban-result').textContent=banned?'Identity blocked.':'Identity unblocked.';
}
async function loadAdminSettings(){
  requireAdmin();const result=await api('admin.settings');requireAdmin();
  if(result.boot!==boot)throw Error('Radio restarted; refresh the room before changing settings.');
  adminSettings=result;$('admin-topic-value').value=result.topic;$('admin-history-enabled').checked=result.persistent_history;
  $('admin-post-rate').value=result.post_rate;$('admin-poll-rate').value=result.poll_rate;$('admin-settings-panel').hidden=false;
}
function rateValue(id){const value=$(id).value;if(!/^\d+$/.test(value)||Number(value)>65535)throw Error('Rate limits must be whole numbers from 0 to 65535.');return Number(value);}
async function saveAdminSetting(which){
  requireAdmin();if(!adminSettings)throw Error('Load room settings before editing.');
  let values;
  if(which==='topic'){
    const value=$('admin-topic-value').value;
    if(encoder.encode(value).length>151)throw Error('The topic must fit in 151 UTF-8 bytes.');
    values={value:value,expected:adminSettings.topic};
  }else if(which==='history')values={enabled:$('admin-history-enabled').checked,expected:adminSettings.persistent_history};
  else values={post_rate:rateValue('admin-post-rate'),poll_rate:rateValue('admin-poll-rate')};
  await adminMutation(async()=>{await api('admin.'+which,values);await loadAdminSettings();},'Room '+which+' saved.');
  $('admin-settings-result').textContent='Saved. Changes are confirmed by the radio.';
}
function clearMail(){
  mailGeneration++;
  mailboxOnly=false;updateMailSummary();
  mailPolicy=null;mailDirty=false;mailCursor=0;mailNext=null;mailPages=[];mailSnapshot=0;mailItems.clear();mailTransfers.clear();mailSelected=null;
  $('mail-panel').hidden=true;$('mail-selected').hidden=true;$('mail-rows').replaceChildren();$('mail-key').textContent='';$('mail-body').textContent='';$('mail-selected-meta').textContent='';
  for(const id of ['mail-state','mail-policy-result','mail-send-result','mail-count','mail-page','mail-read-state'])$(id).textContent='';
  $('mail-mode').value='closed';$('mail-allowlist').value='';$('mail-only').checked=false;$('mail-recipient').value='';$('mail-text').value='';updateMailButtons();
}
function clearAdminMail(){
  adminMailPolicy=null;adminMailDirty=false;adminMailCursor=0;adminMailNext=null;adminMailPages=[];adminMailSnapshot=0;
  $('admin-mail-rows').replaceChildren();$('admin-mail-policy').hidden=true;$('admin-mail-key').textContent='';$('admin-mail-owner').value='';$('admin-mail-allowlist').value='';$('admin-mail-delete-id').value='';
  for(const id of ['admin-mail-state','admin-mail-page','admin-mail-result'])$(id).textContent='';updateMailButtons();
}
function requireMail(){if(!joined||![1,2,3].includes(role))throw Error('Connect to the room before opening mail.');}
function updateMailSummary(){$('mail-summary').hidden=!mailboxOnly;$('mail-summary').textContent=mailboxOnly?'Mailbox only: automatic chat downloads are paused. Refresh chat downloads once.':'';}
function mailApi(op,values={}){const generation=mailGeneration;return api(op,values).then(result=>{requireMail();if(generation!==mailGeneration)throw Error('Mailbox session changed; open your mailbox again.');return result;});}
function policyValues(prefix){
  const mode=$(prefix+'mode').value,raw=$(prefix+'allowlist').value.trim();
  const allowlist=raw?raw.split(/[\s,]+/).map(key=>key.toLowerCase()):[];
  if(!['closed','public','private'].includes(mode)||allowlist.length>8||allowlist.some(key=>!validKey(key))||new Set(allowlist).size!==allowlist.length)throw Error('Choose up to 8 different complete nonzero public keys.');
  return {mode:mode,mailbox_only:$(prefix+'only').checked,allowlist:allowlist};
}
function checkedMailPolicy(result,owner){
  if(!result||!validKey(result.owner)||(owner&&result.owner.toLowerCase()!==owner.toLowerCase())||!Number.isInteger(result.revision)||result.revision<0
      ||!['closed','public','private'].includes(result.mode)||!Array.isArray(result.allowlist)||result.allowlist.length>8||result.allowlist.some(key=>!validKey(key)))throw Error('Invalid mailbox settings; reload before editing.');
  return result;
}
function updateMailButtons(){
  const locked=!joined||mailBusy,writer=role===2||role===3,size=encoder.encode($('mail-text').value).length;
  $('mail-budget').textContent=size+' / 512 UTF-8 bytes'+(writer?'':' - read-only access cannot send mail');
  for(const button of $('mail-panel').querySelectorAll('button'))button.disabled=locked;
  $('mail-open').disabled=!joined||mailBusy;$('mail-send').disabled=locked||!writer||!validKey($('mail-recipient').value.trim())||size>512||!$('mail-text').value.trim();
  $('mail-previous').disabled=locked||!mailPages.length;$('mail-next').disabled=locked||mailNext===null;
  const transfer=mailSelected&&mailTransfers.get(mailSelected.id);
  $('mail-ack').disabled=locked||!mailSelected||!transfer||transfer.next!==mailSelected.length||!transfer.complete;
  $('mail-resume').disabled=locked||!mailSelected;$('mail-delete').disabled=locked||!mailSelected;
  const adminLocked=!joined||role!==3||adminBusy||adminLoading||adminMailBusy;
  for(const button of $('admin-mail').querySelectorAll('button'))button.disabled=adminLocked;
  for(const field of $('admin-mail').querySelectorAll('input,select,textarea'))field.disabled=adminLocked;
  $('admin-mail-previous').disabled=adminLocked||!adminMailPages.length;$('admin-mail-next').disabled=adminLocked||adminMailNext===null;
}
function renderMailPolicy(result,force=false){
  const previous=mailPolicy;checkedMailPolicy(result,$('web-key').textContent);
  mailboxOnly=result.mailbox_only===true;updateMailSummary();
  $('mail-key').textContent=result.owner;$('mail-panel').hidden=false;
  $('mail-state').textContent='Mailbox '+result.mode+'. '+(result.mailbox_only?'Automatic chat downloads are paused; Refresh chat still works.':'Automatic chat downloads are enabled.')+' Settings revision '+result.revision+'.';
  if(!mailDirty||force){mailPolicy=result;$('mail-mode').value=result.mode;$('mail-allowlist').value=result.allowlist.join('\n');$('mail-only').checked=result.mailbox_only;mailDirty=false;}
  else if(previous&&previous.revision!==result.revision)$('mail-policy-result').textContent='Settings changed on the radio. Your draft is preserved; reload before replacing them.';
  updateMailButtons();
}
async function loadMailSettings(force=false){
  requireMail();if(force&&mailDirty&&!confirm('Replace your unsaved mailbox settings with the current radio settings?'))return;
  const result=await mailApi('mail.settings');requireMail();renderMailPolicy(result,force);
}
async function openMailbox(){
  requireMail();if(mailBusy)return;mailBusy=true;updateMailButtons();
  try{await loadMailSettings();await refreshMail();}finally{mailBusy=false;updateMailButtons();}
}
async function saveMailPolicy(){
  requireMail();if(!mailPolicy)throw Error('Load your mailbox settings first.');if(mailBusy)return;
  const values=Object.assign(policyValues('mail-'),{expected_revision:mailPolicy.revision}),generation=connectionGeneration;
  mailBusy=true;updateMailButtons();$('mail-policy-result').textContent='Saving mailbox settings...';
  try{const result=await mailApi('mail.policy',values);requireMail();if(generation!==connectionGeneration)return;renderMailPolicy(result,true);$('mail-policy-result').textContent='Mailbox settings saved and confirmed.';}
  catch(error){$('mail-policy-result').textContent=String(error.message||error);throw error;}
  finally{mailBusy=false;updateMailButtons();}
}
async function sendMail(){
  requireMail();if(role!==2&&role!==3)throw Error('Read and write room access is required to send mail.');if(mailBusy)return;
  const recipient=$('mail-recipient').value.trim().toLowerCase(),text=$('mail-text').value;
  if(!validKey(recipient)||!text.trim()||encoder.encode(text).length>512)throw Error('Use a complete recipient address and a message up to 512 UTF-8 bytes.');
  mailBusy=true;updateMailButtons();$('mail-send-result').textContent='Waiting for the radio to save your mail...';
  try{const result=await mailApi('mail.send',{recipient:recipient,text:text});requireMail();
    if(result.ok!==true||!Number.isInteger(result.id)||result.id<1)throw Error('Mail was not confirmed; check before retrying.');
    if($('mail-text').value===text)$('mail-text').value='';$('mail-send-result').textContent='Mail '+result.id+' saved for '+recipient+'.';}
  catch(error){$('mail-send-result').textContent=String(error.message||error);throw error;}
  finally{mailBusy=false;updateMailButtons();}
}
function checkedMailItem(item){
  if(!item||!Number.isInteger(item.id)||item.id<1||!validKey(item.sender)||!Number.isInteger(item.created)||item.created<0||!Number.isInteger(item.length)||item.length<1||item.length>512)throw Error('Invalid inbox entry; refresh mail.');
  return item;
}
async function refreshMail(cursor=mailCursor){
  requireMail();const count=await mailApi('mail.check');requireMail();
  if(!Number.isInteger(count.total)||!Number.isInteger(count.unread))throw Error('Invalid mailbox counts.');
  $('mail-count').textContent=count.unread+' waiting for receipt; '+count.total+' queued messages.';
  const result=await mailApi('mail.list',{cursor:cursor,revision:cursor?mailSnapshot:0});requireMail();
  if(!Number.isInteger(result.revision)||result.revision<0||!Array.isArray(result.messages)||result.messages.length>16||(result.next!==null&&(!Number.isInteger(result.next)||result.next<=cursor)))throw Error('Invalid inbox page.');
  if(cursor&&result.revision!==mailSnapshot)throw Error('Mailbox changed; check and refresh the inbox before continuing.');
  const items=result.messages.map(checkedMailItem);mailCursor=cursor;mailNext=result.next;mailSnapshot=result.revision;mailItems=new Map(items.map(item=>[item.id,item]));$('mail-rows').replaceChildren();
  if(!items.length){const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=4;cell.textContent='No mail on this page.';row.append(cell);$('mail-rows').append(row);}
  for(const item of items){const row=document.createElement('tr');
    for(const text of [item.sender,new Date(item.created*1000).toLocaleString(),'Waiting for receipt']){const cell=document.createElement('td');cell.textContent=text;if(text===item.sender)cell.className='key';row.append(cell);}
    const cell=document.createElement('td'),button=document.createElement('button');button.type='button';button.textContent='Read '+item.id+' ('+item.length+' bytes)';button.addEventListener('click',()=>openMail(item).catch(message));cell.append(button);row.append(cell);$('mail-rows').append(row);}
  $('mail-page').textContent='Page '+(mailPages.length+1);updateMailButtons();
}
async function openMail(item=mailSelected){
  requireMail();checkedMailItem(item);if(mailBusy)return;mailBusy=true;mailSelected=item;$('mail-selected').hidden=false;
  $('mail-selected-meta').textContent='Message '+item.id+' from '+item.sender+' - '+new Date(item.created*1000).toLocaleString();$('mail-body').textContent='';
  let transfer=mailTransfers.get(item.id);
  if(!transfer||transfer.sender!==item.sender||transfer.created!==item.created||transfer.bytes.length!==item.length){transfer={sender:item.sender,created:item.created,next:0,bytes:new Uint8Array(item.length),complete:false};mailTransfers.set(item.id,transfer);}
  updateMailButtons();
  try{while(transfer.next<item.length){
    $('mail-read-state').textContent='Downloaded '+transfer.next+' / '+item.length+' bytes. Receipt has not been confirmed.';
    const result=await mailApi('mail.read',{id:item.id,offset:transfer.next});requireMail();
    const bytes=Uint8Array.from(atob(result.data64),c=>c.charCodeAt(0));
    if(result.id!==item.id||result.sender!==item.sender||result.created!==item.created||result.length!==item.length||result.offset!==transfer.next||result.count!==bytes.length
        ||!bytes.length||bytes.length>128||result.next!==transfer.next+bytes.length||result.next>item.length)throw Error('Mail changed or its download is invalid; refresh the inbox.');
    transfer.bytes.set(bytes,transfer.next);transfer.next=result.next;
  }
  const text=new TextDecoder('utf-8',{fatal:true}).decode(transfer.bytes);transfer.complete=true;$('mail-body').textContent=text;
  $('mail-read-state').textContent='Complete. Confirm receipt to remove the server copy, or keep it queued for later.';
  }catch(error){$('mail-read-state').textContent='Downloaded '+transfer.next+' / '+item.length+' bytes. '+String(error.message||error)+' Click Read or resume download to continue.';throw error;}
  finally{mailBusy=false;updateMailButtons();}
}
async function removeMail(received){
  requireMail();if(!mailSelected||mailBusy)return;const item=mailSelected,transfer=mailTransfers.get(item.id);
  if(received&&(!transfer||!transfer.complete))throw Error('Read the complete message before confirming receipt.');
  if(!confirm((received?'Confirm receipt and remove':'Delete without confirming receipt')+' for message '+item.id+'?'))return;
  mailBusy=true;updateMailButtons();
  try{const result=await mailApi(received?'mail.ack':'mail.delete',{id:item.id});requireMail();if(result.ok!==true)throw Error('Mail removal was not confirmed.');
    mailTransfers.delete(item.id);mailSelected=null;$('mail-body').textContent='';$('mail-selected').hidden=true;mailPages=[];await refreshMail(0);
    $('mail-count').textContent+=(received?' Receipt confirmed.':' Message deleted.');}
  finally{mailBusy=false;updateMailButtons();}
}
async function loadAdminMail(cursor=adminMailCursor){
  requireAdmin();if(adminMailBusy)return;adminMailBusy=true;updateMailButtons();
  try{const result=await api('admin.mail.list',{cursor:cursor,revision:cursor?adminMailSnapshot:0});requireAdmin();
    if(!Number.isInteger(result.revision)||result.revision<0||!Array.isArray(result.mailboxes)||result.mailboxes.length>16||(result.next!==null&&(!Number.isInteger(result.next)||result.next<=cursor)))throw Error('Invalid mailbox owner page.');
    if(cursor&&result.revision!==adminMailSnapshot)throw Error('Mailboxes changed; load mailbox owners again before continuing.');
    adminMailCursor=cursor;adminMailNext=result.next;adminMailSnapshot=result.revision;$('admin-mail-rows').replaceChildren();
    for(const owner of result.mailboxes){if(!validKey(owner.owner))throw Error('Invalid mailbox identity.');const row=document.createElement('tr');
      for(const text of [owner.owner,(owner.blocked?'Blocked; ':'')+owner.mode+' (revision '+owner.revision+')',owner.unread+' unread / '+owner.total+' queued']){const cell=document.createElement('td');cell.textContent=text;if(text===owner.owner)cell.className='key';row.append(cell);}
      const cell=document.createElement('td'),button=document.createElement('button');button.type='button';button.textContent='Manage policy';button.addEventListener('click',()=>selectAdminMail(owner.owner).catch(message));cell.append(button);row.append(cell);$('admin-mail-rows').append(row);}
    $('admin-mail-page').textContent='Page '+(adminMailPages.length+1);$('admin-mail-state').textContent=result.mailboxes.length?'Mailbox policies and counts loaded. No message bodies were downloaded.':'No configured mailboxes.';
  }finally{adminMailBusy=false;updateMailButtons();}
}
async function selectAdminMail(owner,force=false){
  requireAdmin();if(!validKey(owner))throw Error('Use a complete mailbox owner key.');
  if(adminMailDirty&&!confirm('Replace the unsaved selected mailbox policy?'))return;
  const result=await api('admin.mail.settings',{owner:owner});requireAdmin();adminMailPolicy=checkedMailPolicy(result,owner);adminMailDirty=false;
  $('admin-mail-key').textContent=result.owner;$('admin-mail-mode').value=result.mode;$('admin-mail-allowlist').value=result.allowlist.join('\n');$('admin-mail-only').checked=result.mailbox_only;
  $('admin-mail-block').textContent=result.blocked?'Unblock owner':'Block owner';$('admin-mail-delete-id').value='';$('admin-mail-result').textContent='Loaded policy revision '+result.revision+'.';$('admin-mail-policy').hidden=false;updateMailButtons();
}
async function saveAdminMailPolicy(){
  requireAdmin();if(!adminMailPolicy||adminMailBusy)return;const owner=adminMailPolicy.owner,values=Object.assign(policyValues('admin-mail-'),{owner:owner,expected_revision:adminMailPolicy.revision});
  if(!confirm('Save '+values.mode+' mailbox policy for '+owner+'?'))return;
  adminMailBusy=true;updateMailButtons();
  try{const result=await api('admin.mail.policy',values);requireAdmin();adminMailPolicy=checkedMailPolicy(result,owner);adminMailDirty=false;
    $('admin-mail-result').textContent='Policy saved and confirmed at revision '+result.revision+'.';if(mailPolicy&&mailPolicy.owner===owner)renderMailPolicy(result);}
  catch(error){$('admin-mail-result').textContent=String(error.message||error);throw error;}
  finally{adminMailBusy=false;updateMailButtons();}
}
async function deleteAdminMail(all=false){
  requireAdmin();if(!adminMailPolicy||adminMailBusy)return;const owner=adminMailPolicy.owner,value=$('admin-mail-delete-id').value,id=all?0:Number(value);
  if(!all&&(!/^\d+$/.test(value)||!Number.isInteger(id)||id<1||id>4294967295))throw Error('Enter a valid message ID.');
  if(!confirm((all?'Purge every queued message':'Delete queued message '+id)+' for mailbox '+owner+'?\nThis permanently removes server copies without confirming receipt.'))return;
  adminMailBusy=true;updateMailButtons();
  try{const result=await api('admin.mail.delete',{owner:owner,id:id});requireAdmin();if(result.ok!==true)throw Error('Mailbox deletion was not confirmed.');$('admin-mail-result').textContent=all?'Inbox purge confirmed.':'Message deletion confirmed.';
    if(mailPolicy&&mailPolicy.owner===owner){mailTransfers.clear();mailSelected=null;$('mail-body').textContent='';$('mail-selected').hidden=true;await refreshMail(0);}}
  finally{adminMailBusy=false;updateMailButtons();}
  await loadAdminMail();
}
async function blockAdminMail(){
  requireAdmin();if(!adminMailPolicy||adminMailBusy)return;const owner=adminMailPolicy.owner,banned=!adminMailPolicy.blocked;
  if(!confirm((banned?'Block ':'Unblock ')+owner+' for room access and mailbox operations?'))return;
  adminMailBusy=true;updateMailButtons();
  try{await api('admin.ban',{key:owner,banned:banned});requireAdmin();adminMailPolicy.blocked=banned;$('admin-mail-block').textContent=banned?'Unblock owner':'Block owner';$('admin-mail-result').textContent=banned?'Owner blocked for room access.':'Owner unblocked.';}
  finally{adminMailBusy=false;updateMailButtons();}
  await loadAdminMail();
}
async function openArticle(item){
  if(!Number.isInteger(item.length)||item.length<0||item.length>2048)throw Error("Article exceeds the radio's limit.");
  let transfer=transfers.get(item.id);
  if(!transfer||transfer.version!==item.version){transfer={version:item.version,next:0,bytes:new Uint8Array(item.length)};transfers.set(item.id,transfer)}
  try{
    if(!item.length){const result=await api('board.read',{id:item.id,version:item.version,offset:0});
      if(result.version!==item.version||result.offset!==0||result.total!==0||result.next!==0||result.data64!=='')throw Error('Article changed; refresh notices.')}
    while(transfer.next<item.length){
      const result=await api('board.read',{id:item.id,version:item.version,offset:transfer.next});
      const bytes=Uint8Array.from(atob(result.data64),c=>c.charCodeAt(0));
      if(result.version!==item.version||result.offset!==transfer.next||result.total!==item.length
        ||!bytes.length||bytes.length>128||result.next!==transfer.next+bytes.length||result.next>item.length)throw Error('Article changed or its transfer is invalid.');
      transfer.bytes.set(bytes,transfer.next);transfer.next=result.next;
    }
    const text=new TextDecoder('utf-8',{fatal:true}).decode(transfer.bytes);
    $('article-title').textContent=item.title;$('article').textContent=text;$('article-title').hidden=false;$('article').hidden=false;
    if(role===3){$('article-id').value=String(item.id);$('title').value=item.title;$('body').value=text;editId=item.id;editVersion=item.version}
    message('');
  }catch(e){
    // Completed chunks stay available for another click after a lost link.
    // A version mismatch always starts a new transfer after the index refresh.
    message(e);throw e;
  }
}
$('join').addEventListener('submit',async event=>{
  event.preventDefault();message('');
  connectionGeneration++;joined=false;role=0;clearAdmin();$('editor').hidden=true;$('admin-panel').hidden=true;$('room').hidden=true;updateBudget();
  clearMail();
  try{makeIdentity();password=$('password').value;$('password').value='';await status();joined=true;$('room').hidden=false;updateBudget();await refreshChat(true);await refreshBoard()}
  catch(e){message(e)}
});
$('name').value=saved('mc-room-name','Web');
$('name').addEventListener('input',()=>{remember('mc-room-name',$('name').value);updateBudget()});
$('message').addEventListener('input',updateBudget);
$('compose').addEventListener('submit',async event=>{
  event.preventDefault();if($('send').disabled)return;message('');sending=true;updateBudget();
  const text=$('message').value;
  try{await api('post',{name:$('name').value,text:text});if($('message').value===text)$('message').value='';await refreshChat()}
  catch(e){message(e)}finally{sending=false;updateBudget()}
});
$('refresh').addEventListener('click',()=>refreshChat().catch(message));
$('disconnect').addEventListener('click',()=>{
  connectionGeneration++;joined=false;role=0;password='';clearAdmin();$('room').hidden=true;$('admin-panel').hidden=true;$('editor').hidden=true;$('state').textContent='Disconnected';updateBudget();
  clearMail();
});
$('mail-open').addEventListener('click',()=>openMailbox().catch(message));
$('mail-copy').addEventListener('click',async()=>{
  try{requireMail();const key=$('mail-key').textContent;if(!validKey(key))throw Error('Open your mailbox first.');
    if(navigator.clipboard&&window.isSecureContext)await navigator.clipboard.writeText(key);
    else{const range=document.createRange();range.selectNodeContents($('mail-key'));const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);if(!document.execCommand('copy'))throw Error('Select and copy the complete mailbox address above.');selection.removeAllRanges();}
    $('mail-state').textContent='Complete mailbox address copied.';
  }catch(error){message(error)}
});
for(const id of ['mail-mode','mail-only','mail-allowlist'])$(id).addEventListener(id==='mail-allowlist'?'input':'change',()=>{mailDirty=true});
for(const id of ['mail-recipient','mail-text'])$(id).addEventListener('input',updateMailButtons);
$('mail-policy').addEventListener('submit',event=>{event.preventDefault();saveMailPolicy().catch(message)});
$('mail-policy-reload').addEventListener('click',()=>loadMailSettings(true).catch(message));
$('mail-compose').addEventListener('submit',event=>{event.preventDefault();sendMail().catch(message)});
$('mail-refresh').addEventListener('click',()=>{if(mailBusy)return;mailPages=[];refreshMail(0).catch(message)});
$('mail-next').addEventListener('click',()=>{if(mailNext===null||mailBusy)return;const previous=mailCursor;mailPages.push(previous);refreshMail(mailNext).catch(error=>{mailPages.pop();message(error);updateMailButtons()})});
$('mail-previous').addEventListener('click',()=>{if(!mailPages.length||mailBusy)return;const cursor=mailPages.pop();refreshMail(cursor).catch(error=>{mailPages.push(cursor);message(error);updateMailButtons()})});
$('mail-resume').addEventListener('click',()=>openMail().catch(message));
$('mail-ack').addEventListener('click',()=>removeMail(true).catch(message));
$('mail-delete').addEventListener('click',()=>removeMail(false).catch(message));
$('admin-mail-refresh').addEventListener('click',()=>{adminMailPages=[];loadAdminMail(0).catch(message)});
$('admin-mail-load-owner').addEventListener('click',()=>selectAdminMail($('admin-mail-owner').value.trim().toLowerCase()).catch(message));
$('admin-mail-next').addEventListener('click',()=>{if(adminMailNext===null||adminMailBusy)return;const previous=adminMailCursor;adminMailPages.push(previous);loadAdminMail(adminMailNext).catch(error=>{adminMailPages.pop();message(error);updateMailButtons()})});
$('admin-mail-previous').addEventListener('click',()=>{if(!adminMailPages.length||adminMailBusy)return;const cursor=adminMailPages.pop();loadAdminMail(cursor).catch(error=>{adminMailPages.push(cursor);message(error);updateMailButtons()})});
for(const id of ['admin-mail-mode','admin-mail-only','admin-mail-allowlist'])$(id).addEventListener(id==='admin-mail-allowlist'?'input':'change',()=>{adminMailDirty=true});
$('admin-mail-policy').addEventListener('submit',event=>{event.preventDefault();saveAdminMailPolicy().catch(message)});
$('admin-mail-reload').addEventListener('click',()=>{if(adminMailPolicy)selectAdminMail(adminMailPolicy.owner,true).catch(message)});
$('admin-mail-delete').addEventListener('click',()=>deleteAdminMail(false).catch(message));
$('admin-mail-purge').addEventListener('click',()=>deleteAdminMail(true).catch(message));
$('admin-mail-block').addEventListener('click',()=>blockAdminMail().catch(message));
$('browser-catchup-form').addEventListener('submit',event=>event.preventDefault());
$('browser-skip-before').addEventListener('click',()=>browserCatchup('before').catch(message));
$('browser-skip-number').addEventListener('click',()=>browserCatchup('keep').catch(message));
$('admin-refresh').addEventListener('click',()=>loadAdmin().catch(message));
$('admin-next').addEventListener('click',()=>{
  if(adminNext===null)return;const previous=adminCursor;adminPages.push(previous);
  loadAdmin(adminNext).catch(error=>{adminPages.pop();message(error);updateAdminButtons()});
});
$('admin-previous').addEventListener('click',()=>{
  if(!adminPages.length)return;const cursor=adminPages.pop();
  loadAdmin(cursor).catch(error=>{adminPages.push(cursor);message(error);updateAdminButtons()});
});
$('admin-selected-refresh').addEventListener('click',()=>{
  if((routeDirty.outpath||routeDirty.altpath)&&!confirm('Replace the unsaved route drafts with the current radio routes?'))return;
  selectAdminUser(selectedKey,true).catch(message);
});
for(const [which,prefix] of [['outpath','admin-out'],['altpath','admin-alt']]){
  $(prefix+'-value').addEventListener('input',()=>{routeDirty[which]=true});
  $(prefix+'-mode').addEventListener('change',()=>{routeDirty[which]=true;updateAdminButtons()});
  $(which==='outpath'?'admin-outpath':'admin-altpath').addEventListener('submit',event=>{event.preventDefault();saveAdminRoute(which).catch(message)});
}
$('admin-skip-before').addEventListener('click',()=>adminCatchup('before').catch(message));
$('admin-skip-number').addEventListener('click',()=>adminCatchup('keep').catch(message));
$('admin-access-save').addEventListener('click',()=>saveAdminAccess().catch(message));
$('admin-ban-selected').addEventListener('click',()=>{if(selectedUser)setAdminBan(selectedKey,!selectedUser.banned).catch(message)});
$('admin-ban-add').addEventListener('click',()=>setAdminBan($('admin-ban-key').value.trim(),true).catch(message));
$('admin-ban-remove').addEventListener('click',()=>setAdminBan($('admin-ban-key').value.trim(),false).catch(message));
$('admin-settings-load').addEventListener('click',()=>loadAdminSettings().catch(message));
$('admin-topic').addEventListener('submit',event=>{event.preventDefault();saveAdminSetting('topic').catch(message)});
$('admin-history-save').addEventListener('click',()=>saveAdminSetting('history').catch(message));
$('admin-rates').addEventListener('submit',event=>{event.preventDefault();saveAdminSetting('rates').catch(message)});
$('refresh-board').addEventListener('click',()=>refreshBoard().catch(message));
$('article-id').addEventListener('change',()=>{editId=Number($('article-id').value);editVersion=0});
$('publish').addEventListener('submit',async event=>{
  event.preventDefault();message('');
  try{
    const id=Number($('article-id').value),bytes=encoder.encode($('body').value);
    if(encoder.encode($('title').value).length>63||!$('title').value||bytes.length>2048)throw Error('Use a title up to 63 UTF-8 bytes and an article up to 2,048 bytes.');
    const body64=btoa(Array.from(bytes,b=>String.fromCharCode(b)).join(''));
    const result=await api('board.save',{id:id,version:editId===id?editVersion:0,title:$('title').value,body64:body64});
    editId=id;editVersion=result.version;
    transfers.delete(id);await refreshBoard();message('');
  }catch(e){message(e)}
});
$('delete').addEventListener('click',async()=>{
  const id=Number($('article-id').value),entry=notices.get(id);
  if(!entry||!confirm('Delete '+entry.title+'?'))return;
  try{await api('board.delete',{id:id,version:entry.version});transfers.delete(id);await refreshBoard();$('article').hidden=true;$('article-title').hidden=true}
  catch(e){message(e)}
});
setInterval(()=>{if(joined)refreshChat(true).catch(message);if(joined&&role===3&&adminLoaded&&$('admin-live').checked)loadAdmin().catch(message)},15000);
updateBudget();
updateAdminButtons();
</script></body></html>)roomhtml";
