#pragma once

#include <Arduino.h>

// Offline browser room client, served by the existing WebConfig listener.
// No radio private key is imported; a separate random browser token identifies
// posts and moderation. Keep untrusted room text out of HTML interpolation.
static const char ROOM_WEB_HTML[] PROGMEM = R"roomhtml(<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MeshCore Room</title><style>
:root{color-scheme:dark}body{font:16px system-ui;background:#111820;color:#eee;max-width:720px;margin:auto;padding:20px}
input,textarea,button,select{font:inherit;border:1px solid #6b7786;border-radius:6px;padding:9px;background:#202b38;color:inherit}
input,textarea{box-sizing:border-box;width:100%;margin:5px 0 14px}textarea{min-height:85px}button{cursor:pointer;margin:4px 6px 4px 0}
button:disabled{opacity:.45;cursor:default}label{display:block}.muted{color:#b5beca}#error{color:#ffb9b9;white-space:pre-wrap}
#topic,#article,.post{white-space:pre-wrap;overflow-wrap:anywhere}#article{border:1px solid #6b7786;padding:14px}
.post{padding:10px 0;border-bottom:1px solid #364351}.post small{display:block;color:#b5beca}
details{margin:18px 0}summary{cursor:pointer}code{overflow-wrap:anywhere}a{color:#a8d5ff}[hidden]{display:none!important}
</style></head><body>
<h1 id="room-name">MeshCore Room</h1>
<p class="muted">Chat and selected notices over local Wi-Fi. This page also works without internet access.</p>
<form id="join"><label for="password">Room password (leave empty for public read-only access)</label>
<input id="password" type="password" autocomplete="current-password" maxlength="64">
<label for="name">Your display name</label><input id="name" autocomplete="nickname" value="Web">
<button type="submit">Connect</button></form>
<p id="error" role="alert"></p><p id="state" class="muted" role="status"></p>
<section id="room" hidden><p id="topic"></p>
<details><summary>Access and history</summary><p id="history"></p>
<p>Your browser identity for room moderation: <code id="web-key"></code></p>
<p class="muted">This browser identity is separate from a radio's public key. The password stays in memory for this session.</p></details>
<button id="refresh" type="button">Refresh chat</button><div id="posts" aria-live="polite"></div>
<form id="compose"><label for="message">Message</label><textarea id="message"></textarea>
<p id="budget" class="muted"></p><button id="send" type="submit">Send</button></form>
<h2>Information board</h2><button id="refresh-board" type="button">Refresh notices</button>
<div id="notices"></div><h3 id="article-title" hidden></h3><pre id="article" hidden></pre>
<section id="editor" hidden><h3>Publish or edit a notice</h3><form id="publish">
<label for="article-id">Article slot (1–8)</label><select id="article-id">
<option>1</option><option>2</option><option>3</option><option>4</option><option>5</option><option>6</option><option>7</option><option>8</option></select>
<label for="title">Title (up to 63 UTF-8 bytes)</label><input id="title">
<label for="body">Article (up to 2,048 UTF-8 bytes)</label><textarea id="body"></textarea>
<button type="submit">Save notice</button><button id="delete" type="button">Delete selected notice</button></form></section>
</section><p><a href="/">Radio configuration</a></p>
<script>
'use strict';
const $=id=>document.getElementById(id), encoder=new TextEncoder();
let token='', sequence=0, password='', boot=0, role=0, after=0, joined=false, polling=false;
let chain=Promise.resolve(), notices=new Map(), transfers=new Map();
let sending=false, editId=1, editVersion=0;
function saved(key,fallback){try{return localStorage.getItem(key)||fallback}catch(e){return fallback}}
function remember(key,value){try{localStorage.setItem(key,String(value))}catch(e){}}
function makeIdentity(){
  token=saved('mc-room-token','');
  if(!/^[0-9a-f]{64}$/.test(token)||/^0+$/.test(token)){
    const bytes=new Uint8Array(32);crypto.getRandomValues(bytes);
    token=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');remember('mc-room-token',token);
  }
  sequence=Number(saved('mc-room-sequence','0'));
  if(!Number.isSafeInteger(sequence)||sequence<0||sequence>=4294967295) throw Error('Browser sequence exhausted. Clear this site’s stored room identity to reconnect.');
}
function message(error){$('error').textContent=error?String(error.message||error):''}
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function fetchBounded(url,options){
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),5000);
  try{return await fetch(url,Object.assign({},options,{signal:controller.signal,cache:'no-store'}))}
  finally{clearTimeout(timer)}
}
function api(op,values={}){
  const intent=Object.assign({},values,{boot:boot});
  const run=()=>request(op,intent), result=chain.then(run,run);
  chain=result.catch(()=>{});return result;
}
async function request(op,values){
  if(++sequence>4294967295)throw Error('Browser sequence exhausted.');
  remember('mc-room-sequence',sequence);
  const headers={'Content-Type':'application/json','X-Room-Token':token,'X-Room-Seq':String(sequence)};
  const body=JSON.stringify(Object.assign({},values,{op:op,password:password}));
  if(encoder.encode(body).length>4096)throw Error('Request exceeds the radio’s 4 KB limit.');
  const deadline=Date.now()+20000;
  let accepted=false;
  // An interrupted submission retries the identical sequence and body. Once
  // admitted, only poll its result. Never silently resend with a new identity.
  while(Date.now()<deadline){
    let response;
    try{response=await fetchBounded(accepted?'/api/room/result':'/api/room',
      {method:'POST',headers:headers,body:accepted?undefined:body})}
    catch(e){await pause(400);continue}
    if(!accepted&&response.status===429){await pause(500);continue}
    if(response.status===202){accepted=true;await pause(250);continue}
    let result;
    try{result=await response.json()}catch(e){throw Error('Invalid response; refresh to check before retrying.')}
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
  if(boot&&boot!==result.boot){after=0;$('posts').replaceChildren();transfers.clear();notices.clear()}
  boot=result.boot;role=result.role;
  $('room-name').textContent=result.name;$('topic').textContent=result.topic;
  $('web-key').textContent=result.web_key;
  $('history').textContent=result.persistent_history?'Radio retains its newest 32 posts across reboots.':'Radio history is in RAM and is lost on reboot.';
  $('state').textContent=role===3?'Admin access':role===2?'Read and write access':'Read-only access';
  $('editor').hidden=role!==3;updateBudget();
}
async function refreshChat(){
  if(polling||!joined)return;polling=true;
  try{
    await status();
    for(let i=0;i<32;i++){
      const result=await api('posts',{after:after}), post=result.post;if(!post)break;
      if(!Number.isInteger(post.timestamp)||post.timestamp<=after)throw Error('Invalid message cursor.');
      const row=document.createElement('div'),stamp=document.createElement('small'),text=document.createElement('div');
      row.className='post';stamp.textContent=new Date(post.timestamp*1000).toLocaleString()+' · '+post.author.slice(0,12);
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
async function openArticle(item){
  if(!Number.isInteger(item.length)||item.length<0||item.length>2048)throw Error('Article exceeds the radio’s limit.');
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
  try{makeIdentity();password=$('password').value;joined=false;await status();joined=true;$('room').hidden=false;updateBudget();await refreshChat();await refreshBoard()}
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
setInterval(()=>{if(joined)refreshChat().catch(message)},15000);
updateBudget();
</script></body></html>)roomhtml";
