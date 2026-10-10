#!/usr/bin/env python3
"""Execute the actual offline room page in Chromium with bounded HTTP mocks."""

import html
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from test_webconfig_ui_runtime import BROWSER, run_browser

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "src/helpers/esp32/RoomWebHtml.h"
BOUNDARY = ROOT / "test/fixtures/room_web_service/boundary.json"

COMMON = r'''
const boundary=__BOUNDARY__,calls=[];
localStorage.setItem('mc-room-token','01'.repeat(32));localStorage.setItem('mc-room-sequence','0');
window.attack=()=>document.body.dataset.testAttacked='yes';
window.addEventListener('error',e=>document.body.dataset.testJsError=e.message);
window.addEventListener('unhandledrejection',e=>document.body.dataset.testJsError=String(e.reason));
const response=(value,status=200)=>Promise.resolve({ok:status>=200&&status<300,status:status,json:()=>Promise.resolve(value)});
const until=async predicate=>{for(let i=0;i<500;i++){if(predicate())return;await new Promise(r=>setTimeout(r,10))}throw Error('test condition timed out')};
const submit=id=>document.getElementById(id).dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
const record=(url,options)=>{let p=options.body?JSON.parse(options.body):null;calls.push({url:url,body:options.body||'',seq:options.headers['X-Room-Seq'],token:options.headers['X-Room-Token'],op:p?p.op:'result'});return p};
const mark=(key,value)=>document.body.setAttribute('data-test-'+key,String(value));
'''

ADMIN_COMMON = r'''
const radioKey='ab'.repeat(32),otherKey='ab'.repeat(6)+'cd'.repeat(26);
const radioUser=(key=radioKey,extra={})=>Object.assign({key:key,role:1,retained:true,active:true,heard_ago:12,pending_count:4,
  delivery:'post',failures:0,outpath:'A1',altpath:'B1',observed:'A2',observed_pending:false,sync_since:100,
  pending_post:101,topic_pending:0,ack_wait_ms:700,banned:false},extra);
const adminPage=(users,next=null)=>({users:users,next:next,total:3,active:2,backlog:6,pending:1,failed:1,boot:71});
const baseRoom=p=>p.op==='status'?response(Object.assign({},boundary.status,{role:3})):p.op==='posts'?response({post:null}):p.op==='board.index'?response({revision:0,articles:[],next:255}):null;
const joinedRoom=async()=>{submit('join');await until(()=>!$('room').hidden&&$('notices').textContent==='No notices published.')};
'''


class RoomWebUiTest(unittest.TestCase):
    def run_page(self, scenario, virtual_time=7000):
        if not BROWSER:
            if os.environ.get("MESHCORE_REQUIRE_ROOM_BROWSER") == "1":
                self.fail("Chromium is required for room browser validation")
            self.skipTest("Chromium-family browser is unavailable")
        source = HEADER.read_text().split('R"roomhtml(', 1)[1].split(')roomhtml";', 1)[0]
        transform = getattr(self, "page_transform", None)
        if transform:
            source = transform(source)
        fixture = json.loads(BOUNDARY.read_text())
        prelude = "<script>\n" + COMMON.replace("__BOUNDARY__", json.dumps(fixture)) + scenario + "\n</script>"
        self.assertIn("<script>\n'use strict';", source)
        source = source.replace("<script>\n'use strict';", prelude + "<script>\n'use strict';", 1)
        # Snap Chromium uses a private /tmp; workspace files are visible to
        # both the test process and the real browser.
        cache_root = ROOT / ".pio"
        cache_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="meshcore-room-ui-", dir=cache_root, ignore_cleanup_errors=True) as temporary:
            work = Path(temporary)
            page = work / "index.html"
            page.write_text(source)
            args = [BROWSER, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                    "--user-data-dir=" + str(work / "profile"), "--virtual-time-budget=" + str(virtual_time),
                    "--dump-dom", page.as_uri()]
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                args.insert(1, "--no-sandbox")
            checked = run_browser(args)
        self.assertEqual(checked.returncode, 0, checked.stderr.decode("utf-8", "replace"))
        dom = checked.stdout.decode("utf-8", "replace")
        self.assertTrue(dom, checked.stderr.decode("utf-8", "replace"))
        self.assertNotIn("data-test-js-error=", dom)
        self.assertNotIn('data-test-attacked="yes"', dom)
        self.assertIn('data-test-done="true"', dom)
        return dom

    def attribute(self, dom, name):
        match = re.search(r'data-test-' + re.escape(name) + r'="([^"]*)"', dom)
        self.assertIsNotNone(match, dom)
        return html.unescape(match.group(1))

    def test_shared_backend_contract_xss_and_selected_article_only(self):
        dom = self.run_page(r'''
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(p.op==='status')return response(boundary.status);
  if(p.op==='posts')return response({post:p.after===0?{timestamp:1001,author:'ab'.repeat(32),text:'<img src=x onerror=attack()> chat'}:null});
  if(p.op==='board.index')return response({revision:1,articles:[boundary.index.articles[0],{id:2,version:1,length:2048,title:'<img src=x onerror=attack()> title'}],next:255});
  if(p.op==='board.read'){if(p.id!==1)throw Error('unselected article fetched');return response(boundary.read)}
  throw Error('unexpected operation '+p.op);
};
window.addEventListener('load',()=>{(async()=>{
  submit('join');await until(()=>$('notices').childElementCount===2);
  mark('before-reads',calls.filter(c=>c.op==='board.read').length);
  mark('topic',$('topic').textContent);mark('post',$('posts').textContent);mark('title',$('notices').lastElementChild.textContent);
  mark('image-count',document.querySelectorAll('img').length);mark('role',$('state').textContent);mark('editor-hidden',$('editor').hidden);
  await openArticle(notices.get(1));await openArticle(notices.get(1));
  mark('article',$('article').textContent);mark('reads',calls.filter(c=>c.op==='board.read').length);
  mark('tokens',calls.every(c=>c.token==='01'.repeat(32)));mark('calls',JSON.stringify(calls));mark('done',true);
})().catch(e=>mark('js-error',e.message))});
''')
        self.assertEqual(self.attribute(dom, "before-reads"), "0")
        self.assertEqual(self.attribute(dom, "reads"), "1")
        self.assertEqual(self.attribute(dom, "image-count"), "0")
        self.assertIn("<img src=x onerror=attack()>", self.attribute(dom, "topic"))
        self.assertIn("<img src=x onerror=attack()>", self.attribute(dom, "post"))
        self.assertIn("<img src=x onerror=attack()>", self.attribute(dom, "title"))
        self.assertEqual(self.attribute(dom, "article"), "Read only this article")
        self.assertEqual(self.attribute(dom, "editor-hidden"), "true")
        self.assertEqual(self.attribute(dom, "tokens"), "true")
        requests = json.loads(self.attribute(dom, "calls"))
        self.assertEqual(sorted({json.loads(item["body"])["id"] for item in requests if item["op"] == "board.read"}), [1])

    def test_identical_sequence_retry_failed_post_retention_and_utf8_budget(self):
        dom = self.run_page(r'''
let postAttempts=0,effects=0,pending=null,first=null;
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(url==='/api/room/result')return response(pending);
  if(p.op==='status')return response(boundary.status);
  if(p.op==='posts')return response({post:null});
  if(p.op==='board.index')return response({revision:0,articles:[],next:255});
  if(p.op==='post'){
    postAttempts++;
    if(postAttempts===1){first=calls[calls.length-1];effects++;pending={ok:true,timestamp:1001};return Promise.reject(Error('lost submission response'))}
    if(postAttempts===2){const current=calls[calls.length-1];mark('same-seq',current.seq===first.seq);mark('same-body',current.body===first.body);return response({},202)}
    return response({error:'post not retained; storage unavailable'});
  }
  throw Error('unexpected operation');
};
window.addEventListener('load',()=>{(async()=>{
  submit('join');await until(()=>!$('room').hidden&&$('notices').textContent==='No notices published.');
  $('name').value='A';$('name').dispatchEvent(new Event('input'));$('message').value='x'.repeat(148);$('message').dispatchEvent(new Event('input'));
  mark('maximum-disabled',$('send').disabled);mark('maximum-budget',$('budget').textContent);
  $('message').value='📡'.repeat(38);$('message').dispatchEvent(new Event('input'));mark('utf8-disabled',$('send').disabled);mark('utf8-budget',$('budget').textContent);
  $('message').value='first';$('message').dispatchEvent(new Event('input'));submit('compose');
  await until(()=>postAttempts===2&&$('message').value==='');mark('first-cleared',$('message').value==='');
  $('message').value='keep this draft';$('message').dispatchEvent(new Event('input'));submit('compose');
  await until(()=>postAttempts===3&&$('error').textContent.includes('not retained'));
  mark('draft',$('message').value);mark('effects',effects);mark('attempts',postAttempts);mark('done',true);
})().catch(e=>mark('js-error',e.message))});
''')
        self.assertEqual(self.attribute(dom, "maximum-disabled"), "false")
        self.assertIn("151 / 151", self.attribute(dom, "maximum-budget"))
        self.assertEqual(self.attribute(dom, "utf8-disabled"), "true")
        self.assertIn("155 / 151", self.attribute(dom, "utf8-budget"))
        self.assertEqual(self.attribute(dom, "same-seq"), "true")
        self.assertEqual(self.attribute(dom, "same-body"), "true")
        self.assertEqual(self.attribute(dom, "effects"), "1")
        self.assertEqual(self.attribute(dom, "attempts"), "3")
        self.assertEqual(self.attribute(dom, "draft"), "keep this draft")

    def test_chunk_resume_version_restart_and_full_utf8_publication(self):
        dom = self.run_page(r'''
let currentVersion=1,lostChunk=true,oldOffsets=[],newOffsets=[],publications=[],indexCount=0;
const oldText='a'.repeat(256),newText='📡'.repeat(64),oldBytes=new TextEncoder().encode(oldText),newBytes=new TextEncoder().encode(newText);
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(p.op==='status')return response(Object.assign({},boundary.status,{role:3}));
  if(p.op==='posts')return response({post:null});
  if(p.op==='board.index'){indexCount++;return response({revision:currentVersion,articles:[{id:1,version:currentVersion,length:256,title:'Article v'+currentVersion}],next:255})}
  if(p.op==='board.read'){
    (p.version===1?oldOffsets:newOffsets).push(p.offset);
    if(p.version===1&&p.offset===128&&lostChunk){lostChunk=false;return response({error:'lost link; retry selected article'})}
    const bytes=(p.version===1?oldBytes:newBytes).slice(p.offset,p.offset+128),data64=btoa(Array.from(bytes,b=>String.fromCharCode(b)).join(''));
    return response({version:p.version,offset:p.offset,next:p.offset+bytes.length,total:256,data64:data64});
  }
  if(p.op==='board.save'){publications.push(p);return response({ok:true,version:3})}
  throw Error('unexpected operation');
};
window.addEventListener('load',()=>{(async()=>{
  submit('join');await until(()=>$('notices').childElementCount===1);
  try{await openArticle(notices.get(1))}catch(e){}
  mark('resume-next',transfers.get(1).next);await openArticle(notices.get(1));mark('old-article',$('article').textContent===oldText);
  currentVersion=2;await refreshBoard();await openArticle(notices.get(1));
  mark('new-article',$('article').textContent===newText);mark('old-offsets',JSON.stringify(oldOffsets));mark('new-offsets',JSON.stringify(newOffsets));
  $('title').value='📡'.repeat(15)+'abc';$('body').value='📡'.repeat(512);submit('publish');
  await until(()=>publications.length===1&&indexCount>=3);
  mark('title-bytes',new TextEncoder().encode(publications[0].title).length);
  mark('body-bytes',atob(publications[0].body64).length);mark('publish-version',publications[0].version);mark('publish-boot',publications[0].boot);
  $('body').value='📡'.repeat(513);submit('publish');await until(()=>$('error').textContent.includes('2,048'));
  mark('oversized-publications',publications.length);
  $('body').value='ok';$('title').value='📡'.repeat(16);submit('publish');await new Promise(r=>setTimeout(r,20));
  mark('oversized-title-publications',publications.length);mark('editor-hidden',$('editor').hidden);mark('done',true);
})().catch(e=>mark('js-error',e.message))});
''')
        self.assertEqual(self.attribute(dom, "resume-next"), "128")
        self.assertEqual(self.attribute(dom, "old-article"), "true")
        self.assertEqual(self.attribute(dom, "new-article"), "true")
        self.assertEqual(json.loads(self.attribute(dom, "old-offsets")), [0, 128, 128])
        self.assertEqual(json.loads(self.attribute(dom, "new-offsets")), [0, 128])
        self.assertEqual(self.attribute(dom, "title-bytes"), "63")
        self.assertEqual(self.attribute(dom, "body-bytes"), "2048")
        self.assertEqual(self.attribute(dom, "publish-version"), "2")
        self.assertEqual(self.attribute(dom, "publish-boot"), "71")
        self.assertEqual(self.attribute(dom, "oversized-publications"), "1")
        self.assertEqual(self.attribute(dom, "oversized-title-publications"), "1")
        self.assertEqual(self.attribute(dom, "editor-hidden"), "false")

    def test_empty_article_version_handshake_and_reboot_draft_preservation(self):
        dom = self.run_page(r'''
let serverBoot=71,validEmpty=false,emptyReads=0,postEffects=0;
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(p.op==='status')return response(Object.assign({},boundary.status,{boot:serverBoot}));
  if(p.op==='posts')return response({post:null});
  if(p.op==='board.index')return response({revision:1,articles:[{id:1,version:1,length:0,title:'Empty notice'}],next:255});
  if(p.op==='board.read'){emptyReads++;return response(validEmpty?{version:1,offset:0,next:0,total:0,data64:''}:{error:'article changed; refresh notices'})}
  if(p.op==='post'){
    if(p.boot!==serverBoot)return response({error:'radio restarted; refresh before sending'});
    postEffects++;return response({ok:true,timestamp:1001});
  }
  throw Error('unexpected operation');
};
window.addEventListener('load',()=>{(async()=>{
  submit('join');await until(()=>$('notices').childElementCount===1);
  try{await openArticle(notices.get(1))}catch(e){}
  mark('stale-empty-hidden',$('article').hidden);mark('stale-empty-reads',emptyReads);
  validEmpty=true;await openArticle(notices.get(1));mark('valid-empty-hidden',$('article').hidden);mark('valid-empty-reads',emptyReads);
  $('name').value='A';$('message').value='draft across reboot';$('message').dispatchEvent(new Event('input'));
  serverBoot=72;submit('compose');await until(()=>$('error').textContent.includes('restarted'));
  mark('reboot-draft',$('message').value);mark('reboot-effects',postEffects);
  await status();mark('boot',boot);mark('transfer-count',transfers.size);mark('notice-count',notices.size);mark('done',true);
})().catch(e=>mark('js-error',e.message))});
''')
        self.assertEqual(self.attribute(dom, "stale-empty-hidden"), "true")
        self.assertEqual(self.attribute(dom, "stale-empty-reads"), "1")
        self.assertEqual(self.attribute(dom, "valid-empty-hidden"), "false")
        self.assertEqual(self.attribute(dom, "valid-empty-reads"), "2")
        self.assertEqual(self.attribute(dom, "reboot-draft"), "draft across reboot")
        self.assertEqual(self.attribute(dom, "reboot-effects"), "0")
        self.assertEqual(self.attribute(dom, "boot"), "72")
        self.assertEqual(self.attribute(dom, "transfer-count"), "0")
        self.assertEqual(self.attribute(dom, "notice-count"), "0")

    def test_write_queued_behind_reboot_status_keeps_original_boot(self):
        dom = self.run_page(r'''
let serverBoot=71,delayedStatus=false,effects=0,writeBoot=0;
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(p.op==='status'){
    const value=Object.assign({},boundary.status,{boot:serverBoot});
    return delayedStatus?new Promise(resolve=>setTimeout(()=>resolve({ok:true,status:200,json:()=>Promise.resolve(value)}),80)):response(value);
  }
  if(p.op==='posts')return response({post:null});
  if(p.op==='board.index')return response(boundary.index);
  if(p.op==='post'){writeBoot=p.boot;if(p.boot!==serverBoot)return response({error:'radio restarted; refresh before sending'});effects++;return response({ok:true,timestamp:1001})}
  throw Error('unexpected operation');
};
window.addEventListener('load',()=>{(async()=>{
  submit('join');await until(()=>$('notices').childElementCount===1);
  $('name').value='A';$('message').value='queued before reboot';$('message').dispatchEvent(new Event('input'));
  serverBoot=72;delayedStatus=true;const updating=status();submit('compose');
  await updating;await until(()=>$('error').textContent.includes('restarted'));
  mark('write-boot',writeBoot);mark('current-boot',boot);mark('effects',effects);mark('draft',$('message').value);mark('done',true);
})().catch(e=>mark('js-error',e.message))});
''')
        self.assertEqual(self.attribute(dom, "write-boot"), "71")
        self.assertEqual(self.attribute(dom, "current-boot"), "72")
        self.assertEqual(self.attribute(dom, "effects"), "0")
        self.assertEqual(self.attribute(dom, "draft"), "queued before reboot")

    def test_editor_uses_opened_version_and_then_exact_save_response_version(self):
        dom = self.run_page(r'''
let currentVersion=1,body='original version one',publications=[],effects=0;
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(p.op==='status')return response(Object.assign({},boundary.status,{role:3}));
  if(p.op==='posts')return response({post:null});
  if(p.op==='board.index')return response({revision:currentVersion,articles:[{id:1,version:currentVersion,length:new TextEncoder().encode(body).length,title:'Article '+currentVersion}],next:255});
  if(p.op==='board.read'){const data=new TextEncoder().encode(body);return response({version:currentVersion,offset:0,next:data.length,total:data.length,data64:btoa(Array.from(data,b=>String.fromCharCode(b)).join(''))})}
  if(p.op==='board.save'){
    publications.push(p);
    if(p.version!==currentVersion)return response({error:'article changed; reload index before editing'});
    effects++;currentVersion=7;body=atob(p.body64);return response({ok:true,version:7});
  }
  throw Error('unexpected operation');
};
window.addEventListener('load',()=>{(async()=>{
  submit('join');await until(()=>$('notices').childElementCount===1);await openArticle(notices.get(1));
  currentVersion=2;body='another admin updated this';await refreshBoard();submit('publish');
  await until(()=>publications.length===1&&$('error').textContent.includes('changed'));
  mark('stale-version',publications[0].version);mark('stale-draft',$('body').value);mark('stale-effects',effects);
  await openArticle(notices.get(1));$('body').value='my new draft';submit('publish');
  await until(()=>publications.length===2&&notices.get(1).version===7);
  mark('valid-version',publications[1].version);mark('save-effects',effects);
  currentVersion=9;body='later external edit';await refreshBoard();submit('publish');
  await until(()=>publications.length===3&&$('error').textContent.includes('changed'));
  mark('after-refresh-version',publications[2].version);mark('retained-draft',$('body').value);mark('final-effects',effects);mark('done',true);
})().catch(e=>mark('js-error',e.message))});
''')
        self.assertEqual(self.attribute(dom, "stale-version"), "1")
        self.assertEqual(self.attribute(dom, "stale-draft"), "original version one")
        self.assertEqual(self.attribute(dom, "stale-effects"), "0")
        self.assertEqual(self.attribute(dom, "valid-version"), "2")
        self.assertEqual(self.attribute(dom, "save-effects"), "1")
        self.assertEqual(self.attribute(dom, "after-refresh-version"), "7")
        self.assertEqual(self.attribute(dom, "retained-draft"), "my new draft")
        self.assertEqual(self.attribute(dom, "final-effects"), "1")

    def test_admin_pages_full_identities_safe_rendering_and_permission_downgrade(self):
        dom = self.run_page(ADMIN_COMMON + r'''
let currentRole=2;
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(p.op==='status')return response(Object.assign({},boundary.status,{role:currentRole}));
  if(p.op==='admin.users')return response(p.cursor===0?adminPage([radioUser(),radioUser(otherKey)],7):adminPage([radioUser('ef'.repeat(32))]));
  if(p.op==='admin.user')return response({user:radioUser(p.key,{outpath:'<img src=x onerror=attack()>',observed:'<svg onload=attack()>'}),boot:71});
  const result=baseRoom(p);if(result)return result;throw Error('unexpected '+p.op);
};
window.addEventListener('load',()=>{(async()=>{
  await joinedRoom();mark('reader-hidden',$('admin-panel').hidden);
  try{await loadAdmin()}catch(e){}mark('reader-admin-calls',calls.filter(c=>c.op.startsWith('admin.')).length);
  currentRole=3;await status();await loadAdmin();mark('rows',$('admin-user-rows').childElementCount);
  $('admin-user-rows').children[1].querySelector('button').click();await until(()=>selectedUser&&selectedUser.key===otherKey);
  mark('selected-key',$('admin-selected-key').textContent);mark('safe-path',$('admin-out-value').value);mark('safe-observed',$('admin-details').textContent);
  $('admin-next').click();await until(()=>adminCursor===7&&!adminLoading);mark('next-cursor',adminCursor);mark('last-disabled',$('admin-next').disabled);
  $('admin-previous').click();await until(()=>adminCursor===0&&!adminLoading);mark('previous-cursor',adminCursor);
  currentRole=2;await status();mark('downgraded-hidden',$('admin-panel').hidden);mark('cleared-rows',$('admin-user-rows').childElementCount);
  mark('cleared-key',$('admin-selected-key').textContent);mark('cleared-details',$('admin-details').textContent);mark('image-count',document.querySelectorAll('img,svg').length);mark('done',true);
})().catch(e=>mark('js-error',e.message))});
''')
        self.assertEqual(self.attribute(dom, "reader-hidden"), "true")
        self.assertEqual(self.attribute(dom, "reader-admin-calls"), "0")
        self.assertEqual(self.attribute(dom, "rows"), "2")
        self.assertEqual(self.attribute(dom, "selected-key"), "ab" * 6 + "cd" * 26)
        self.assertIn("<img", self.attribute(dom, "safe-path"))
        self.assertIn("<svg", self.attribute(dom, "safe-observed"))
        self.assertEqual(self.attribute(dom, "next-cursor"), "7")
        self.assertEqual(self.attribute(dom, "last-disabled"), "true")
        self.assertEqual(self.attribute(dom, "previous-cursor"), "0")
        self.assertEqual(self.attribute(dom, "downgraded-hidden"), "true")
        self.assertEqual(self.attribute(dom, "cleared-rows"), "0")
        self.assertEqual(self.attribute(dom, "cleared-key"), "")
        self.assertEqual(self.attribute(dom, "cleared-details"), "")
        self.assertEqual(self.attribute(dom, "image-count"), "0")

    def test_admin_routes_preserve_drafts_stale_baselines_and_identical_write_retry(self):
        dom = self.run_page(ADMIN_COMMON + r'''
let route='A1',effects=0,attempts=0,first=null,pending=null;
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(url==='/api/room/result')return response(pending);
  if(p.op==='admin.user')return response({user:radioUser(p.key,{outpath:route}),boot:71});
  if(p.op==='admin.route'){
    if(p.expected!==route&&pending===null)return response({error:'route changed; reload before editing'});
    attempts++;
    if(attempts===1){first=calls[calls.length-1];effects++;route=p.value;pending={ok:true};return Promise.reject(Error('response lost'))}
    mark('retry-seq',calls[calls.length-1].seq===first.seq);mark('retry-body',calls[calls.length-1].body===first.body);return response({},202);
  }
  const result=baseRoom(p);if(result)return result;throw Error('unexpected '+p.op);
};
window.addEventListener('load',()=>{(async()=>{
  await joinedRoom();await selectAdminUser(radioKey,true);
  $('admin-out-value').value='C1';$('admin-out-value').dispatchEvent(new Event('input'));route='D1';await selectAdminUser(radioKey,false);
  mark('draft',$('admin-out-value').value);mark('old-baseline',routeBaseline.outpath);
  try{await saveAdminRoute('outpath')}catch(e){mark('stale-error',e.message)}mark('stale-effects',effects);
  await selectAdminUser(radioKey,true);$('admin-out-value').value='E1';$('admin-out-value').dispatchEvent(new Event('input'));
  $('admin-alt-value').value='F1';$('admin-alt-value').dispatchEvent(new Event('input'));
  await saveAdminRoute('outpath');mark('effects',effects);mark('attempts',attempts);mark('route',route);mark('other-draft',$('admin-alt-value').value);
  mark('saved-baseline',routeBaseline.outpath);mark('expected',JSON.parse(first.body).expected);mark('target',JSON.parse(first.body).key);mark('write-boot',JSON.parse(first.body).boot);mark('done',true);
})().catch(e=>mark('js-error',e.message))});
''')
        self.assertEqual(self.attribute(dom, "draft"), "C1")
        self.assertEqual(self.attribute(dom, "old-baseline"), "A1")
        self.assertIn("route changed", self.attribute(dom, "stale-error"))
        self.assertEqual(self.attribute(dom, "stale-effects"), "0")
        self.assertEqual(self.attribute(dom, "retry-seq"), "true")
        self.assertEqual(self.attribute(dom, "retry-body"), "true")
        self.assertEqual(self.attribute(dom, "effects"), "1")
        self.assertEqual(self.attribute(dom, "attempts"), "2")
        self.assertEqual(self.attribute(dom, "route"), "E1")
        self.assertEqual(self.attribute(dom, "other-draft"), "F1")
        self.assertEqual(self.attribute(dom, "saved-baseline"), "E1")
        self.assertEqual(self.attribute(dom, "expected"), "D1")
        self.assertEqual(self.attribute(dom, "target"), "ab" * 32)
        self.assertEqual(self.attribute(dom, "write-boot"), "71")

    def test_admin_catchup_confirmation_count_date_and_optimistic_cursor(self):
        dom = self.run_page(ADMIN_COMMON + r'''
let cursor=100,approve=false,changes=[];window.confirm=()=>approve;
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(p.op==='admin.user')return response({user:radioUser(p.key,{sync_since:cursor}),boot:71});
  if(p.op==='admin.catchup'){
    changes.push(p);if(p.expected_sync!==cursor)return response({error:'delivery cursor changed; refresh selected user'});
    cursor=p.mode==='keep'?102:104;return response({ok:true,skipped:2,sync_since:cursor});
  }
  const result=baseRoom(p);if(result)return result;throw Error('unexpected '+p.op);
};
window.addEventListener('load',()=>{(async()=>{
  await joinedRoom();await selectAdminUser(radioKey,true);$('admin-skip-count').value='2';await adminCatchup('keep');mark('cancel-effects',changes.length);
  approve=true;await adminCatchup('keep');mark('keep',JSON.stringify(changes[0]));
  $('admin-skip-count').value='33';try{await adminCatchup('keep')}catch(e){mark('bad-count',e.message)}mark('invalid-effects',changes.length);
  $('admin-skip-date').value='2030-01-01T12:00';const requested=Math.floor(new Date($('admin-skip-date').value).getTime()/1000);
  await adminCatchup('before');mark('date',JSON.stringify(changes[1]));mark('date-seconds',requested);mark('cursor',selectedUser.sync_since);
  cursor=105;try{await adminCatchup('before')}catch(e){mark('stale',e.message)}mark('stale-cursor',selectedUser.sync_since);mark('done',true);
})().catch(e=>mark('js-error',e.message))});
''')
        self.assertEqual(self.attribute(dom, "cancel-effects"), "0")
        keep = json.loads(self.attribute(dom, "keep"))
        self.assertEqual((keep["mode"], keep["count"], keep["expected_sync"], keep["boot"]), ("keep", 2, 100, 71))
        self.assertEqual(keep["key"], "ab" * 32)
        self.assertIn("0 to 32", self.attribute(dom, "bad-count"))
        self.assertEqual(self.attribute(dom, "invalid-effects"), "1")
        before = json.loads(self.attribute(dom, "date"))
        self.assertEqual(before["mode"], "before")
        self.assertEqual(before["before"], int(self.attribute(dom, "date-seconds")))
        self.assertEqual(before["expected_sync"], 102)
        self.assertEqual(self.attribute(dom, "cursor"), "104")
        self.assertIn("cursor changed", self.attribute(dom, "stale"))
        self.assertEqual(self.attribute(dom, "stale-cursor"), "104")

    def test_alternate_route_has_no_flood_choice_and_existing_sentinel_maps_to_clear(self):
        dom = self.run_page(ADMIN_COMMON + r'''
let alternate='flood',changes=[];
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(p.op==='admin.user')return response({user:radioUser(p.key,{outpath:'flood',altpath:alternate}),boot:71});
  if(p.op==='admin.route'){changes.push(p);if(p.which!=='altpath'||p.value!=='clear'||p.expected!=='flood')throw Error('wrong alternate disable request');alternate='unknown';return response({ok:true})}
  const result=baseRoom(p);if(result)return result;throw Error('unexpected '+p.op);
};
window.addEventListener('load',()=>{(async()=>{
  await joinedRoom();await selectAdminUser(radioKey,true);
  mark('primary-flood',Boolean($('admin-out-mode').querySelector('option[value="flood"]')));
  mark('alternate-flood',Boolean($('admin-alt-mode').querySelector('option[value="flood"]')));mark('alternate-mode',$('admin-alt-mode').value);
  mark('alternate-input-disabled',$('admin-alt-value').disabled);await saveAdminRoute('altpath');
  mark('expected',changes[0].expected);mark('value',changes[0].value);mark('primary-mode',$('admin-out-mode').value);mark('alternate-after',$('admin-alt-mode').value);mark('done',true);
})().catch(e=>mark('js-error',e.message))});
''')
        self.assertEqual(self.attribute(dom, "primary-flood"), "true")
        self.assertEqual(self.attribute(dom, "alternate-flood"), "false")
        self.assertEqual(self.attribute(dom, "alternate-mode"), "clear")
        self.assertEqual(self.attribute(dom, "alternate-input-disabled"), "true")
        self.assertEqual(self.attribute(dom, "expected"), "flood")
        self.assertEqual(self.attribute(dom, "value"), "clear")
        self.assertEqual(self.attribute(dom, "primary-mode"), "flood")
        self.assertEqual(self.attribute(dom, "alternate-after"), "clear")

    def test_long_valid_comma_route_and_failure_summary_label(self):
        dom = self.run_page(ADMIN_COMMON + r'''
const longPath=Array.from({length:63},(_,i)=>(i+1).toString(16).padStart(2,'0')).join(',');let route='A1',writes=[];
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(p.op==='admin.users')return response(adminPage([radioUser()]));
  if(p.op==='admin.user')return response({user:radioUser(p.key,{outpath:route}),boot:71});
  if(p.op==='admin.route'){if(p.value!==longPath||p.expected!=='A1')throw Error('wrong long route request');writes.push(p);route=p.value.replaceAll(',','');return response({ok:true})}
  const result=baseRoom(p);if(result)return result;throw Error('unexpected '+p.op);
};
window.addEventListener('load',()=>{(async()=>{
  await joinedRoom();await loadAdmin();await selectAdminUser(radioKey,true);$('admin-out-value').value=longPath;$('admin-out-value').dispatchEvent(new Event('input'));
  await saveAdminRoute('outpath');mark('writes',writes.length);mark('bytes',writes[0].value.length);mark('route-bytes',$('admin-out-value').value.length);mark('cards',$('admin-cards').textContent);mark('done',true);
})().catch(e=>mark('js-error',e.message))});
''')
        self.assertEqual(self.attribute(dom, "writes"), "1")
        self.assertEqual(self.attribute(dom, "bytes"), "188")
        self.assertEqual(self.attribute(dom, "route-bytes"), "126")
        self.assertIn("Users with failures", self.attribute(dom, "cards"))
        self.assertNotIn("Paused delivery", self.attribute(dom, "cards"))

    def test_browser_catchup_keeps_newest_and_never_sets_future_cursor(self):
        dom = self.run_page(r'''
let available=[];
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(p.op==='status')return response(boundary.status);
  if(p.op==='posts')return response({post:available.find(post=>post.timestamp>p.after)||null});
  if(p.op==='board.index')return response({revision:0,articles:[],next:255});
  throw Error('unexpected '+p.op);
};
window.addEventListener('load',()=>{(async()=>{
  submit('join');await until(()=>$('notices').textContent==='No notices published.');
  available=[101,102,103,104,105].map(timestamp=>({timestamp:timestamp,author:'ab'.repeat(32),text:'Message '+timestamp}));
  await refreshChat();mark('initial-after',after);
  $('browser-skip-count').value='2';const start=calls.length;await browserCatchup('keep');
  mark('keep-posts',$('posts').textContent);mark('keep-skipped',$('browser-catchup-result').textContent);mark('keep-cursors',JSON.stringify(calls.slice(start).filter(c=>c.op==='posts').map(c=>JSON.parse(c.body).after)));
  available.push({timestamp:106,author:'ab'.repeat(32),text:'Next unread'});$('browser-skip-date').value='2030-01-01T12:00';await browserCatchup('before');mark('future-after',after);
  available.push({timestamp:107,author:'ab'.repeat(32),text:'Arrived after future-date skip'});await refreshChat();mark('new-post',$('posts').textContent);
  mark('admin-calls',calls.filter(c=>c.op.startsWith('admin.')).length);mark('future-requests',calls.filter(c=>c.op==='posts').some(c=>JSON.parse(c.body).after>107));mark('done',true);
})().catch(e=>mark('js-error',e.message))});
''')
        self.assertIn("Message 104", self.attribute(dom, "keep-posts"))
        self.assertIn("Message 105", self.attribute(dom, "keep-posts"))
        self.assertNotIn("Message 103", self.attribute(dom, "keep-posts"))
        self.assertIn("Hidden 3", self.attribute(dom, "keep-skipped"))
        self.assertEqual(self.attribute(dom, "initial-after"), "105")
        self.assertEqual(json.loads(self.attribute(dom, "keep-cursors")), [0, 101, 102, 103, 104, 105, 103, 104, 105])
        self.assertEqual(self.attribute(dom, "future-after"), "106")
        self.assertIn("Arrived after future-date skip", self.attribute(dom, "new-post"))
        self.assertEqual(self.attribute(dom, "admin-calls"), "0")
        self.assertEqual(self.attribute(dom, "future-requests"), "false")

    def test_admin_settings_access_and_block_are_independent_confirmed_operations(self):
        dom = self.run_page(ADMIN_COMMON + r'''
let topic='Original',history=false,postRate=0,pollRate=0,userRole=1,banned=false,updates=[];window.confirm=()=>true;
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(p.op==='admin.user')return response({user:radioUser(p.key,{role:userRole,banned:banned}),boot:71});
  if(p.op==='admin.settings')return response({topic:topic,persistent_history:history,post_rate:postRate,poll_rate:pollRate,boot:71});
  if(p.op==='admin.topic'){updates.push(p);if(p.expected!==topic)return response({error:'topic changed; reload settings'});topic=p.value;return response({ok:true})}
  if(p.op==='admin.history'){updates.push(p);if(p.expected!==history)return response({error:'history setting changed'});history=p.enabled;return response({ok:true})}
  if(p.op==='admin.rates'){updates.push(p);postRate=p.post_rate;pollRate=p.poll_rate;return response({ok:true})}
  if(p.op==='admin.access'){updates.push(p);if(p.expected_role!==userRole)return response({error:'role changed'});userRole=p.role;return response({ok:true})}
  if(p.op==='admin.ban'){updates.push(p);banned=p.banned;return response({ok:true})}
  const result=baseRoom(p);if(result)return result;throw Error('unexpected '+p.op);
};
window.addEventListener('load',()=>{(async()=>{
  $('password').value='fixture-secret';await joinedRoom();mark('password-cleared',$('password').value==='');
  await loadAdminSettings();$('admin-topic-value').value='My draft';topic='Changed by other admin';try{await saveAdminSetting('topic')}catch(e){mark('stale-topic',e.message)}mark('topic-draft',$('admin-topic-value').value);
  await loadAdminSettings();$('admin-topic-value').value='New topic';await saveAdminSetting('topic');
  $('admin-history-enabled').checked=true;await saveAdminSetting('history');$('admin-post-rate').value='12';$('admin-poll-rate').value='65535';await saveAdminSetting('rates');
  await selectAdminUser(radioKey,true);$('admin-access-role').value='3';await saveAdminAccess();await setAdminBan(radioKey,true);await setAdminBan(radioKey,false);
  mark('updates',JSON.stringify(updates));mark('topic',topic);mark('history',history);mark('post-rate',postRate);mark('poll-rate',pollRate);mark('role',userRole);mark('banned',banned);
  mark('stored-secret',Object.keys(localStorage).some(key=>localStorage.getItem(key).includes('fixture-secret')));mark('done',true);
})().catch(e=>mark('js-error',e.message))});
''')
        self.assertEqual(self.attribute(dom, "password-cleared"), "true")
        self.assertIn("topic changed", self.attribute(dom, "stale-topic"))
        self.assertEqual(self.attribute(dom, "topic-draft"), "My draft")
        self.assertEqual(self.attribute(dom, "topic"), "New topic")
        self.assertEqual(self.attribute(dom, "history"), "true")
        self.assertEqual(self.attribute(dom, "post-rate"), "12")
        self.assertEqual(self.attribute(dom, "poll-rate"), "65535")
        self.assertEqual(self.attribute(dom, "role"), "3")
        self.assertEqual(self.attribute(dom, "banned"), "false")
        self.assertEqual(self.attribute(dom, "stored-secret"), "false")
        changes = json.loads(self.attribute(dom, "updates"))
        self.assertEqual([item["op"] for item in changes], ["admin.topic", "admin.topic", "admin.history", "admin.rates", "admin.access", "admin.ban", "admin.ban"])
        self.assertTrue(all(item["boot"] == 71 for item in changes))
        self.assertEqual(changes[4]["expected_role"], 1)

    def test_disconnect_discards_late_admin_response_and_clears_private_view(self):
        dom = self.run_page(ADMIN_COMMON + r'''
let release=null;
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(p.op==='admin.users')return new Promise(resolve=>{release=()=>resolve({ok:true,status:200,json:()=>Promise.resolve(adminPage([radioUser()]))})});
  const result=baseRoom(p);if(result)return result;throw Error('unexpected '+p.op);
};
window.addEventListener('load',()=>{(async()=>{
  await joinedRoom();const pending=loadAdmin().catch(()=>{});await until(()=>release!==null);$('disconnect').click();release();await pending;
  mark('hidden',$('admin-panel').hidden);mark('room-hidden',$('room').hidden);mark('role',role);mark('password-empty',password==='');mark('rows',$('admin-user-rows').childElementCount);
  mark('loaded',adminLoaded);mark('identity-secret',localStorage.getItem('mc-room-token')===token);mark('done',true);
})().catch(e=>mark('js-error',e.message))});
''')
        self.assertEqual(self.attribute(dom, "hidden"), "true")
        self.assertEqual(self.attribute(dom, "room-hidden"), "true")
        self.assertEqual(self.attribute(dom, "role"), "0")
        self.assertEqual(self.attribute(dom, "password-empty"), "true")
        self.assertEqual(self.attribute(dom, "rows"), "0")
        self.assertEqual(self.attribute(dom, "loaded"), "false")
        self.assertEqual(self.attribute(dom, "identity-secret"), "true")

    def test_remove_saved_access_does_not_read_deleted_client_or_report_failed_write(self):
        dom = self.run_page(ADMIN_COMMON + r'''
let removed=false,effects=0;window.confirm=()=>true;
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(p.op==='admin.user'){if(removed)throw Error('deleted client must not be read');return response({user:radioUser(p.key),boot:71})}
  if(p.op==='admin.users')return response(adminPage(removed?[]:[radioUser()]));
  if(p.op==='admin.access'){if(p.role!==0||p.expected_role!==1)throw Error('wrong removal request');effects++;removed=true;return response({ok:true})}
  const result=baseRoom(p);if(result)return result;throw Error('unexpected '+p.op);
};
window.addEventListener('load',()=>{(async()=>{
  await joinedRoom();await loadAdmin();await selectAdminUser(radioKey,true);$('admin-access-role').value='0';await saveAdminAccess();
  mark('effects',effects);mark('selected-hidden',$('admin-selected').hidden);mark('selected-key',selectedKey);mark('rows',$('admin-user-rows').childElementCount);
  mark('result',$('admin-result').textContent);mark('done',true);
})().catch(e=>mark('js-error',e.message))});
''')
        self.assertEqual(self.attribute(dom, "effects"), "1")
        self.assertEqual(self.attribute(dom, "selected-hidden"), "true")
        self.assertEqual(self.attribute(dom, "selected-key"), "")
        self.assertEqual(self.attribute(dom, "rows"), "0")
        self.assertEqual(self.attribute(dom, "result"), "Access updated.")

    def test_pending_access_save_and_zero_keep_catchup_have_accurate_feedback(self):
        dom = self.run_page(ADMIN_COMMON + r'''
let userRole=1,cursor=100,pendingSave=true,confirmations=[],changes=[];
window.confirm=text=>{confirmations.push(text);return true};
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(p.op==='admin.user')return response({user:radioUser(p.key,{role:userRole,sync_since:cursor}),boot:71});
  if(p.op==='admin.access'){if(p.expected_role!==userRole)throw Error('wrong access baseline');userRole=p.role;return response({ok:true,pending_save:pendingSave})}
  if(p.op==='admin.catchup'){changes.push(p);if(p.mode!=='keep'||p.count!==0||p.expected_sync!==100)throw Error('wrong skip-all request');cursor=104;return response({ok:true,skipped:4,sync_since:cursor})}
  const result=baseRoom(p);if(result)return result;throw Error('unexpected '+p.op);
};
window.addEventListener('load',()=>{(async()=>{
  await joinedRoom();await selectAdminUser(radioKey,true);$('admin-access-role').value='2';await saveAdminAccess();mark('pending-save',$('admin-result').textContent);
  pendingSave=false;$('admin-access-role').value='3';await saveAdminAccess();mark('saved',$('admin-result').textContent);
  $('admin-skip-count').value='0';await adminCatchup('keep');mark('zero-keep',JSON.stringify(changes[0]));mark('skip-all-confirm',confirmations[2]);mark('cursor',selectedUser.sync_since);mark('done',true);
})().catch(e=>mark('js-error',e.message))});
''')
        self.assertEqual(self.attribute(dom, "pending-save"), "Access updated; keep power on for at least 5 seconds while it is saved.")
        self.assertEqual(self.attribute(dom, "saved"), "Access updated.")
        skip = json.loads(self.attribute(dom, "zero-keep"))
        self.assertEqual((skip["mode"], skip["count"], skip["expected_sync"]), ("keep", 0, 100))
        self.assertIn("Skip all unread retained chat messages", self.attribute(dom, "skip-all-confirm"))
        self.assertIn("ab" * 32, self.attribute(dom, "skip-all-confirm"))
        self.assertEqual(self.attribute(dom, "cursor"), "104")

    def test_browser_regression_guards_reject_stale_route_private_cache_and_future_cursor(self):
        controls = (
            (
                "stale route baseline",
                "if(routeDirty[which]&&!force){",
                "if(routeDirty[which]&&!force){routeBaseline[which]=value;",
                self.test_admin_routes_preserve_drafts_stale_baselines_and_identical_write_retry,
            ),
            (
                "private admin cache after role loss",
                "if(role!==3)clearAdmin();updateAdminButtons();updateBudget();",
                "updateAdminButtons();updateBudget();",
                self.test_admin_pages_full_identities_safe_rendering_and_permission_downgrade,
            ),
            (
                "future timestamp cursor",
                "after=skipped?unread[skipped-1]:0;",
                "after=mode==='before'?before-1:(skipped?unread[skipped-1]:0);",
                self.test_browser_catchup_keeps_newest_and_never_sets_future_cursor,
            ),
        )
        for name, old, new, check in controls:
            with self.subTest(name=name):
                def transform(source, old=old, new=new):
                    self.assertIn(old, source)
                    return source.replace(old, new, 1)
                self.page_transform = transform
                try:
                    with self.assertRaises(AssertionError):
                        check()
                finally:
                    del self.page_transform


if __name__ == "__main__":
    unittest.main()
