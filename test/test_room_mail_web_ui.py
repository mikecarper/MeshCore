#!/usr/bin/env python3
"""Exercise the actual personal mailbox page in Chromium, without hardware."""

import json
import unittest

import test_room_web_ui as room_ui


MAIL_COMMON = r'''
const ownKey=boundary.status.web_key,senderKey='ab'.repeat(32),otherOwner='cd'.repeat(32);
let serverRole=2,settingsRevision=0,serverMode='closed',serverOnly=false,serverAllow=[],inbox=[],chunks=[],confirmations=[];
const settings=(owner=ownKey,extra={})=>Object.assign({owner:owner,revision:settingsRevision,mode:serverMode,mailbox_only:serverOnly,allowlist:serverAllow,blocked:false},extra);
const mailBase=p=>{
  if(p.op==='status')return response(Object.assign({},boundary.status,{role:serverRole,mailbox_only:serverOnly}));
  if(p.op==='posts')return response({post:null});
  if(p.op==='board.index')return response({revision:0,articles:[],next:255});
  if(p.op==='mail.settings')return response(settings());
  if(p.op==='mail.check')return response({owner:ownKey,revision:settingsRevision,mailbox_only:serverOnly,total:inbox.length,unread:inbox.length});
  if(p.op==='mail.list')return response({messages:inbox,next:null,revision:settingsRevision});
  return null;
};
const joinedMailRoom=async()=>{submit('join');await until(()=>!$('room').hidden&&$('notices').textContent==='No notices published.')};
const edit=id=>$(id).dispatchEvent(new Event(id.includes('allowlist')||id==='mail-text'||id==='mail-recipient'?'input':'change'));
window.confirm=text=>{confirmations.push(text);return true};
'''


class RoomMailWebUiTest(unittest.TestCase):
    run_page = room_ui.RoomWebUiTest.run_page
    attribute = room_ui.RoomWebUiTest.attribute

    def test_saved_mailbox_only_join_skips_chat_until_explicit_refresh(self):
        dom = self.run_page(MAIL_COMMON + r'''
serverOnly=true;
window.fetch=(url,options)=>{const p=record(url,options),result=mailBase(p);if(result)return result;throw Error('unexpected '+p.op)};
window.addEventListener('load',()=>{(async()=>{
  await joinedMailRoom();mark('initial-posts',calls.filter(c=>c.op==='posts').length);mark('initial-mail',calls.filter(c=>c.op.startsWith('mail.')).length);
  mark('hint',$('mail-summary').textContent);mark('hint-visible',!$('mail-summary').hidden);await refreshChat(true);mark('auto-posts',calls.filter(c=>c.op==='posts').length);
  await refreshChat();mark('manual-posts',calls.filter(c=>c.op==='posts').length);mark('lazy-panel',$('mail-panel').hidden);mark('done',true);
})().catch(error=>mark('js-error',error.message))});
''')
        self.assertEqual(self.attribute(dom, "initial-posts"), "0")
        self.assertEqual(self.attribute(dom, "initial-mail"), "0")
        self.assertEqual(self.attribute(dom, "auto-posts"), "0")
        self.assertEqual(self.attribute(dom, "manual-posts"), "1")
        self.assertEqual(self.attribute(dom, "hint-visible"), "true")
        self.assertIn("automatic chat downloads are paused", self.attribute(dom, "hint"))
        self.assertEqual(self.attribute(dom, "lazy-panel"), "true")

    def test_lazy_closed_mailbox_selective_safe_reads_resume_and_explicit_receipt(self):
        dom = self.run_page(MAIL_COMMON + r'''
const text='<img src=x onerror=attack()> '+ 'x'.repeat(230),bytes=new TextEncoder().encode(text);
const item={id:7,sender:senderKey,created:1001,length:bytes.length,acked:false};inbox=[item];let fail=true,acks=0;
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(p.op==='mail.read'){
    chunks.push(p.offset);if(p.offset===128&&fail){fail=false;return response({error:'link lost'})}
    const part=bytes.slice(p.offset,p.offset+128);
    return response({id:7,sender:senderKey,created:1001,length:bytes.length,offset:p.offset,count:part.length,next:p.offset+part.length,data64:btoa(Array.from(part,b=>String.fromCharCode(b)).join('')),acked:false});
  }
  if(p.op==='mail.ack'){acks++;inbox=[];return response({ok:true})}
  const result=mailBase(p);if(result)return result;throw Error('unexpected '+p.op);
};
window.addEventListener('load',()=>{(async()=>{
  await joinedMailRoom();mark('initial-mail-requests',calls.filter(c=>c.op.startsWith('mail.')).length);
  await openMailbox();mark('default-mode',$('mail-mode').value);mark('address',$('mail-key').textContent);
  document.body.style.width='360px';document.body.style.boxSizing='border-box';const panel=$('mail-panel').getBoundingClientRect();mark('responsive-fit',Array.from($('mail-panel').querySelectorAll('fieldset')).every(field=>field.getBoundingClientRect().right<=panel.right+1));
  mark('before-body-reads',chunks.length);mark('sender',$('mail-rows').textContent);
  try{await openMail(item)}catch(error){}
  mark('partial',mailTransfers.get(7).next);mark('partial-ack-disabled',$('mail-ack').disabled);mark('before-acks',acks);
  await openMail(item);mark('body',$('mail-body').textContent);mark('images',document.querySelectorAll('img').length);
  mark('complete-ack-disabled',$('mail-ack').disabled);mark('read-acks',acks);await removeMail(true);
  mark('acks',acks);mark('offsets',JSON.stringify(chunks));mark('removed-body',$('mail-body').textContent);mark('removed-cache',mailTransfers.size);mark('receipt-confirmation',confirmations[0]);mark('done',true);
})().catch(error=>mark('js-error',error.message))});
''')
        self.assertEqual(self.attribute(dom, "initial-mail-requests"), "0")
        self.assertEqual(self.attribute(dom, "default-mode"), "closed")
        self.assertEqual(self.attribute(dom, "responsive-fit"), "true")
        self.assertEqual(len(self.attribute(dom, "address")), 64)
        self.assertEqual(self.attribute(dom, "before-body-reads"), "0")
        self.assertIn("ab" * 32, self.attribute(dom, "sender"))
        self.assertEqual(self.attribute(dom, "partial"), "128")
        self.assertEqual(self.attribute(dom, "partial-ack-disabled"), "true")
        self.assertEqual(self.attribute(dom, "complete-ack-disabled"), "false")
        self.assertEqual(self.attribute(dom, "read-acks"), "0")
        self.assertEqual(self.attribute(dom, "acks"), "1")
        self.assertEqual(self.attribute(dom, "images"), "0")
        self.assertIn("<img src=x onerror=attack()>", self.attribute(dom, "body"))
        self.assertEqual(json.loads(self.attribute(dom, "offsets")), [0, 128, 128, 256])
        self.assertEqual(self.attribute(dom, "removed-body"), "")
        self.assertEqual(self.attribute(dom, "removed-cache"), "0")
        self.assertIn("Confirm receipt and remove", self.attribute(dom, "receipt-confirmation"))

    def test_policy_stale_revision_retains_draft_key_limit_and_mailbox_only_poll(self):
        dom = self.run_page(MAIL_COMMON + r'''
let writes=[];
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(p.op==='mail.policy'){
    writes.push(p);if(p.expected_revision!==settingsRevision)return response({error:'mailbox policy changed; reload'});
    settingsRevision++;serverMode=p.mode;serverAllow=p.allowlist;serverOnly=p.mailbox_only;return response(settings());
  }
  const result=mailBase(p);if(result)return result;throw Error('unexpected '+p.op);
};
window.addEventListener('load',()=>{(async()=>{
  await joinedMailRoom();await openMailbox();$('mail-mode').value='private';edit('mail-mode');$('mail-allowlist').value=senderKey;edit('mail-allowlist');$('mail-only').checked=true;edit('mail-only');
  settingsRevision=2;await loadMailSettings();mark('baseline-after-refresh',mailPolicy.revision);mark('draft-before-save',$('mail-allowlist').value);
  try{await saveMailPolicy()}catch(error){}mark('failed-draft',$('mail-allowlist').value);mark('failed-revision',writes[0].expected_revision);
  await loadMailSettings(true);$('mail-mode').value='private';edit('mail-mode');$('mail-allowlist').value=senderKey;edit('mail-allowlist');$('mail-only').checked=true;edit('mail-only');await saveMailPolicy();
  mark('success',$('mail-policy-result').textContent);mark('allowlist',JSON.stringify(writes[1].allowlist));mark('saved-revision',mailPolicy.revision);
  const before=calls.filter(c=>c.op==='posts').length;await refreshChat(true);mark('auto-chat',calls.filter(c=>c.op==='posts').length-before);await refreshChat();mark('manual-chat',calls.filter(c=>c.op==='posts').length-before);
  $('mail-allowlist').value=Array.from({length:9},(_,i)=>(i+1).toString(16).padStart(64,'0')).join('\n');edit('mail-allowlist');
  try{await saveMailPolicy()}catch(error){mark('limit-error',error.message)}mark('write-count',writes.length);mark('done',true);
})().catch(error=>mark('js-error',error.message))});
''')
        self.assertEqual(self.attribute(dom, "baseline-after-refresh"), "0")
        self.assertEqual(self.attribute(dom, "failed-revision"), "0")
        self.assertEqual(self.attribute(dom, "failed-draft"), "ab" * 32)
        self.assertEqual(json.loads(self.attribute(dom, "allowlist")), ["ab" * 32])
        self.assertEqual(self.attribute(dom, "saved-revision"), "3")
        self.assertIn("saved and confirmed", self.attribute(dom, "success"))
        self.assertEqual(self.attribute(dom, "auto-chat"), "0")
        self.assertEqual(self.attribute(dom, "manual-chat"), "1")
        self.assertIn("up to 8", self.attribute(dom, "limit-error"))
        self.assertEqual(self.attribute(dom, "write-count"), "2")

    def test_send_identical_http_retry_utf8_limit_failed_draft_and_reader_permission(self):
        dom = self.run_page(MAIL_COMMON + r'''
let attempts=0,effects=0,first=null,pending=null;
window.fetch=(url,options)=>{
  const p=record(url,options);if(url==='/api/room/result')return response(pending);
  if(p.op==='mail.send'){
    attempts++;if(attempts===1){first=calls[calls.length-1];effects++;pending={ok:true,id:11};return Promise.reject(Error('lost response'))}
    if(attempts===2){const current=calls[calls.length-1];mark('same-seq',current.seq===first.seq);mark('same-body',current.body===first.body);return response({},202)}
    return response({error:'recipient mailbox is full'});
  }
  const result=mailBase(p);if(result)return result;throw Error('unexpected '+p.op);
};
window.addEventListener('load',()=>{(async()=>{
  await joinedMailRoom();await openMailbox();$('mail-recipient').value=senderKey;edit('mail-recipient');$('mail-text').value='\u{1f4e1}'.repeat(128);edit('mail-text');mark('max-disabled',$('mail-send').disabled);
  $('mail-text').value+='x';edit('mail-text');mark('over-disabled',$('mail-send').disabled);try{await sendMail()}catch(error){}mark('over-attempts',attempts);
  $('mail-text').value='hello';edit('mail-text');await sendMail();mark('confirmed-draft',$('mail-text').value);mark('confirmed-status',$('mail-send-result').textContent);
  $('mail-text').value='keep private draft';edit('mail-text');try{await sendMail()}catch(error){}mark('failed-draft',$('mail-text').value);mark('effects',effects);mark('attempts',attempts);
  serverRole=1;await status();$('mail-recipient').value=senderKey;$('mail-text').value='reader attempt';updateMailButtons();mark('reader-disabled',$('mail-send').disabled);
  try{await sendMail()}catch(error){mark('reader-error',error.message)}mark('reader-attempts',attempts);mark('done',true);
})().catch(error=>mark('js-error',error.message))});
''')
        self.assertEqual(self.attribute(dom, "max-disabled"), "false")
        self.assertEqual(self.attribute(dom, "over-disabled"), "true")
        self.assertEqual(self.attribute(dom, "over-attempts"), "0")
        self.assertEqual(self.attribute(dom, "same-seq"), "true")
        self.assertEqual(self.attribute(dom, "same-body"), "true")
        self.assertEqual(self.attribute(dom, "effects"), "1")
        self.assertEqual(self.attribute(dom, "confirmed-draft"), "")
        self.assertIn("saved for", self.attribute(dom, "confirmed-status"))
        self.assertEqual(self.attribute(dom, "failed-draft"), "keep private draft")
        self.assertEqual(self.attribute(dom, "reader-disabled"), "true")
        self.assertEqual(self.attribute(dom, "reader-attempts"), self.attribute(dom, "attempts"))

    def test_admin_metadata_only_pages_policy_purge_block_and_role_loss(self):
        dom = self.run_page(MAIL_COMMON + r'''
serverRole=3;let changes=[],deletions=[],bans=[],acceptedConfirm=true;
window.confirm=text=>{confirmations.push(text);return acceptedConfirm};
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(p.op==='admin.mail.list')return response({mailboxes:[{owner:p.cursor===0?ownKey:otherOwner,mode:'private',revision:5,total:2,unread:2,blocked:false}],next:p.cursor===0?1:null,revision:5});
  if(p.op==='admin.mail.settings')return response(settings(p.owner,{revision:5,mode:'private',allowlist:[senderKey]}));
  if(p.op==='admin.mail.policy'){changes.push(p);return response({error:'mailbox policy changed; reload'})}
  if(p.op==='admin.mail.delete'){deletions.push(p);return response({ok:true})}
  if(p.op==='admin.ban'){bans.push(p);return response({ok:true})}
  if(p.op==='mail.read')throw Error('admin downloaded private mail');
  const result=mailBase(p);if(result)return result;throw Error('unexpected '+p.op);
};
window.addEventListener('load',()=>{(async()=>{
  await joinedMailRoom();await loadAdminMail(0);mark('full-owner',$('admin-mail-rows').textContent);mark('next-enabled',!$('admin-mail-next').disabled);
  await loadAdminMail(1);mark('second-owner',$('admin-mail-rows').textContent);await selectAdminMail(otherOwner);mark('selected-address',$('admin-mail-key').textContent);mark('allowlist',$('admin-mail-allowlist').value);
  $('admin-mail-mode').value='public';edit('admin-mail-mode');try{await saveAdminMailPolicy()}catch(error){}mark('admin-failed-draft',$('admin-mail-mode').value);mark('policy-request',JSON.stringify(changes[0]));
  acceptedConfirm=false;await deleteAdminMail(true);mark('cancel-deletions',deletions.length);acceptedConfirm=true;await deleteAdminMail(true);mark('purge',JSON.stringify(deletions[0]));
  await blockAdminMail();mark('ban',JSON.stringify(bans[0]));mark('body-reads',calls.filter(c=>c.op==='mail.read').length);
  serverRole=2;await status();mark('cleared-key',$('admin-mail-key').textContent);mark('cleared-allowlist',$('admin-mail-allowlist').value);mark('hidden-policy',$('admin-mail-policy').hidden);mark('confirmations',JSON.stringify(confirmations));mark('done',true);
})().catch(error=>mark('js-error',error.message))});
''')
        self.assertIn("cd" * 32, self.attribute(dom, "second-owner"))
        self.assertEqual(self.attribute(dom, "next-enabled"), "true")
        self.assertEqual(self.attribute(dom, "selected-address"), "cd" * 32)
        self.assertEqual(self.attribute(dom, "allowlist"), "ab" * 32)
        self.assertEqual(self.attribute(dom, "admin-failed-draft"), "public")
        policy = json.loads(self.attribute(dom, "policy-request"))
        self.assertEqual((policy["owner"], policy["expected_revision"], policy["mode"]), ("cd" * 32, 5, "public"))
        self.assertEqual(self.attribute(dom, "cancel-deletions"), "0")
        purge = json.loads(self.attribute(dom, "purge"))
        self.assertEqual((purge["owner"], purge["id"]), ("cd" * 32, 0))
        ban = json.loads(self.attribute(dom, "ban"))
        self.assertEqual((ban["key"], ban["banned"]), ("cd" * 32, True))
        self.assertEqual(self.attribute(dom, "body-reads"), "0")
        self.assertEqual(self.attribute(dom, "cleared-key"), "")
        self.assertEqual(self.attribute(dom, "cleared-allowlist"), "")
        self.assertEqual(self.attribute(dom, "hidden-policy"), "true")

    def test_admin_provisions_new_radio_mailbox_by_complete_address(self):
        dom = self.run_page(MAIL_COMMON + r'''
serverRole=3;const radioOwner='ef'.repeat(32);let configured=false,loaded=[],policies=[];
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(p.op==='admin.mail.settings'){loaded.push(p.owner);return response(settings(p.owner,{revision:0,mode:'closed',mailbox_only:false,allowlist:[]}))}
  if(p.op==='admin.mail.policy'){policies.push(p);if(p.owner!==radioOwner||p.expected_revision!==0)throw Error('incorrect new radio policy');configured=true;return response(settings(radioOwner,{revision:1,mode:p.mode,mailbox_only:p.mailbox_only,allowlist:p.allowlist}))}
  if(p.op==='admin.mail.list')return response({mailboxes:configured?[{owner:radioOwner,mode:'private',revision:1,unread:0,total:0,blocked:false}]:[],revision:configured?1:0,next:null});
  const result=mailBase(p);if(result)return result;throw Error('unexpected '+p.op);
};
window.addEventListener('load',()=>{(async()=>{
  await joinedMailRoom();await loadAdminMail(0);$('admin-mail-owner').value='short-key';$('admin-mail-load-owner').click();await until(()=>$('error').textContent.includes('complete'));mark('invalid-loads',loaded.length);
  $('admin-mail-owner').value=radioOwner.toUpperCase();$('admin-mail-load-owner').click();await until(()=>!$('admin-mail-policy').hidden);mark('selected-address',$('admin-mail-key').textContent);mark('initial-mode',$('admin-mail-mode').value);mark('initial-revision',adminMailPolicy.revision);
  $('admin-mail-mode').value='private';edit('admin-mail-mode');$('admin-mail-allowlist').value=senderKey;edit('admin-mail-allowlist');$('admin-mail-only').checked=true;edit('admin-mail-only');await saveAdminMailPolicy();await loadAdminMail(0);
  mark('policy',JSON.stringify(policies[0]));mark('listed-owner',$('admin-mail-rows').textContent);mark('body-reads',calls.filter(c=>c.op==='mail.read').length);serverRole=2;await status();mark('cleared-entry',$('admin-mail-owner').value);mark('admin-hidden',$('admin-panel').hidden);mark('done',true);
})().catch(error=>mark('js-error',error.message))});
''')
        self.assertEqual(self.attribute(dom, "invalid-loads"), "0")
        self.assertEqual(self.attribute(dom, "selected-address"), "ef" * 32)
        self.assertEqual(self.attribute(dom, "initial-mode"), "closed")
        self.assertEqual(self.attribute(dom, "initial-revision"), "0")
        policy = json.loads(self.attribute(dom, "policy"))
        self.assertEqual((policy["owner"], policy["expected_revision"], policy["mode"]), ("ef" * 32, 0, "private"))
        self.assertEqual(policy["allowlist"], ["ab" * 32])
        self.assertTrue(policy["mailbox_only"])
        self.assertIn("ef" * 32, self.attribute(dom, "listed-owner"))
        self.assertEqual(self.attribute(dom, "body-reads"), "0")
        self.assertEqual(self.attribute(dom, "cleared-entry"), "")
        self.assertEqual(self.attribute(dom, "admin-hidden"), "true")

    def test_disconnect_rejects_late_private_response_and_clears_sensitive_drafts(self):
        dom = self.run_page(MAIL_COMMON + r'''
const item={id:3,sender:senderKey,created:1001,length:6,acked:false};inbox=[item];let resolveRead;
window.fetch=(url,options)=>{
  const p=record(url,options);if(p.op==='mail.read')return new Promise(resolve=>resolveRead=resolve);
  const result=mailBase(p);if(result)return result;throw Error('unexpected '+p.op);
};
window.addEventListener('load',()=>{(async()=>{
  await joinedMailRoom();await openMailbox();$('mail-text').value='private draft';$('mail-allowlist').value=senderKey;
  const reading=openMail(item).catch(error=>error.message);await until(()=>resolveRead);$('disconnect').click();
  resolveRead({ok:true,status:200,json:()=>Promise.resolve({id:3,sender:senderKey,created:1001,length:6,offset:0,count:6,next:6,data64:btoa('secret')})});
  mark('late-error',await reading);mark('body',$('mail-body').textContent);mark('cache',mailTransfers.size);mark('draft',$('mail-text').value);mark('allowlist',$('mail-allowlist').value);mark('panel-hidden',$('mail-panel').hidden);mark('selected-hidden',$('mail-selected').hidden);mark('done',true);
})().catch(error=>mark('js-error',error.message))});
''')
        self.assertIn("connection changed", self.attribute(dom, "late-error"))
        for field in ["body", "draft", "allowlist"]:
            self.assertEqual(self.attribute(dom, field), "")
        self.assertEqual(self.attribute(dom, "cache"), "0")
        self.assertEqual(self.attribute(dom, "panel-hidden"), "true")
        self.assertEqual(self.attribute(dom, "selected-hidden"), "true")

    def test_mail_and_admin_pages_pin_revision_until_explicit_refresh(self):
        dom = self.run_page(MAIL_COMMON + r'''
serverRole=3;settingsRevision=10;serverMode='public';let serverRevision=10,mailPagesAsked=[],adminPagesAsked=[];
const entries=Array.from({length:3},(_,i)=>({id:i+1,sender:senderKey,created:1001+i,length:6,acked:false}));
const owners=[ownKey,senderKey,otherOwner].map(owner=>({owner:owner,mode:'public',revision:10,unread:1,total:1,blocked:false}));
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(p.op==='mail.list'||p.op==='admin.mail.list'){
    (p.op==='mail.list'?mailPagesAsked:adminPagesAsked).push(p);
    if(p.cursor&&p.revision!==serverRevision)return response({error:'mailbox changed; refresh before continuing'});
    const data=p.op==='mail.list'?{messages:entries.slice(p.cursor,p.cursor+2)}:{mailboxes:owners.slice(p.cursor,p.cursor+2)};
    return response(Object.assign(data,{next:p.cursor===0?2:null,revision:serverRevision}));
  }
  const result=mailBase(p);if(result)return result;throw Error('unexpected '+p.op);
};
window.addEventListener('load',()=>{(async()=>{
  await joinedMailRoom();await openMailbox();serverRevision=11;
  try{await refreshMail(2)}catch(error){mark('mail-stale-error',error.message)}
  mark('retained-mail-rows',$('mail-rows').textContent);mark('mail-cursor-after-stale',mailCursor);mark('mail-stale-revision',mailPagesAsked[1].revision);
  mailPages=[];await refreshMail(0);await refreshMail(2);mark('mail-fresh-revision',mailPagesAsked[3].revision);mark('mail-last-page',$('mail-rows').textContent);
  await loadAdminMail(0);serverRevision=12;try{await loadAdminMail(2)}catch(error){mark('admin-stale-error',error.message)}
  mark('admin-cursor-after-stale',adminMailCursor);mark('admin-stale-revision',adminPagesAsked[1].revision);adminMailPages=[];await loadAdminMail(0);await loadAdminMail(2);
  mark('admin-fresh-revision',adminPagesAsked[3].revision);mark('admin-last-owner',$('admin-mail-rows').textContent);mark('body-requests',calls.filter(c=>c.op==='mail.read').length);mark('done',true);
})().catch(error=>mark('js-error',error.message))});
''')
        self.assertIn("changed", self.attribute(dom, "mail-stale-error"))
        self.assertEqual(self.attribute(dom, "mail-cursor-after-stale"), "0")
        self.assertEqual(self.attribute(dom, "mail-stale-revision"), "10")
        self.assertEqual(self.attribute(dom, "mail-fresh-revision"), "11")
        self.assertIn("Read 3", self.attribute(dom, "mail-last-page"))
        self.assertIn("changed", self.attribute(dom, "admin-stale-error"))
        self.assertEqual(self.attribute(dom, "admin-cursor-after-stale"), "0")
        self.assertEqual(self.attribute(dom, "admin-stale-revision"), "11")
        self.assertEqual(self.attribute(dom, "admin-fresh-revision"), "12")
        self.assertIn("cd" * 32, self.attribute(dom, "admin-last-owner"))
        self.assertEqual(self.attribute(dom, "body-requests"), "0")

    def test_invalid_chunk_cannot_enable_receipt_or_replace_saved_bytes(self):
        dom = self.run_page(MAIL_COMMON + r'''
const item={id:3,sender:senderKey,created:1001,length:6,acked:false};inbox=[item];let acks=0;
window.fetch=(url,options)=>{
  const p=record(url,options);
  if(p.op==='mail.read')return response({id:3,sender:otherOwner,created:1001,length:6,offset:0,count:6,next:6,data64:btoa('secret')});
  if(p.op==='mail.ack'){acks++;return response({ok:true})}
  const result=mailBase(p);if(result)return result;throw Error('unexpected '+p.op);
};
window.addEventListener('load',()=>{(async()=>{
  await joinedMailRoom();await openMailbox();try{await openMail(item)}catch(error){mark('read-error',error.message)}
  try{await removeMail(true)}catch(error){mark('receipt-error',error.message)}
  mark('offset',mailTransfers.get(3).next);mark('body',$('mail-body').textContent);mark('ack-disabled',$('mail-ack').disabled);mark('acks',acks);mark('done',true);
})().catch(error=>mark('js-error',error.message))});
''')
        self.assertIn("invalid", self.attribute(dom, "read-error"))
        self.assertIn("complete", self.attribute(dom, "receipt-error"))
        self.assertEqual(self.attribute(dom, "offset"), "0")
        self.assertEqual(self.attribute(dom, "body"), "")
        self.assertEqual(self.attribute(dom, "ack-disabled"), "true")
        self.assertEqual(self.attribute(dom, "acks"), "0")


if __name__ == "__main__":
    unittest.main()
