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


class RoomWebUiTest(unittest.TestCase):
    def run_page(self, scenario, virtual_time=7000):
        if not BROWSER:
            if os.environ.get("MESHCORE_REQUIRE_ROOM_BROWSER") == "1":
                self.fail("Chromium is required for room browser validation")
            self.skipTest("Chromium-family browser is unavailable")
        source = HEADER.read_text().split('R"roomhtml(', 1)[1].split(')roomhtml";', 1)[0]
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


if __name__ == "__main__":
    unittest.main()
