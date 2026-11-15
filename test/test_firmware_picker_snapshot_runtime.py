#!/usr/bin/env python3
"""Exercise snapshot loading, fallback, and version races in real Chrome."""
import copy
import datetime
import http.server
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unittest

from test_compact_elegant_ota import run_until_complete
from test_webconfig_ui_runtime import BROWSER

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    target = 'Heltec_t1_repeater_lora_ota_no_external_sensors-full'
    controls, releases, families = {}, [], {}
    for version, filename in [('1.18.0.1', 'firmware_controls_1.18.0.1.json'),
                              ('1.18.0.2', 'firmware_controls.json')]:
        data = json.loads((ROOT/'docs/_data'/filename).read_text(encoding='utf-8'))
        data['profiles'] = {target: data['profiles'][target]}
        data['partitionMigrations'] = []
        controls[version] = data
        family = data['familyTag']
        releases_for_family = []
        for tag, name in [(family, 'Heltec_t1_companion_radio_full-' + family + '.uf2'),
                          ('lora-ota-'+family, target+'-ota-'+family+'.uf2')]:
            releases_for_family.append({'tag_name': tag, 'name': 'Version '+version,
                'html_url':'https://github.com/mikecarper/MeshCore/releases/tag/'+tag,
                'published_at':'2026-11-14T00:00:00Z', 'created_at':'2026-11-14T00:00:00Z',
                'draft':False, 'prerelease':False, 'assets':[{'name':name, 'size':1000000,
                'browser_download_url':'https://github.com/mikecarper/MeshCore/releases/download/'+tag+'/'+name}]})
        releases.extend(releases_for_family)
        families[family] = {'source':data['source'], 'releaseTags':[x['tag_name'] for x in releases_for_family],
                            'assetCounts':{x['tag_name']:len(x['assets']) for x in releases_for_family}}
    snapshot = {'schemaVersion':1, 'generatedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'families':families, 'releases':releases}
    return controls, releases, snapshot


class PickerSnapshotRuntime(unittest.TestCase):
    def run_browser(self, scenario, snapshot=None, fixture_override=None):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chrome is required')
            self.skipTest('Chrome unavailable')
        controls, releases, original = fixture_override or fixture()
        if snapshot is None:
            snapshot = original
        bootstrap = r'''<script>
const controls=CONTROLS,releases=RELEASES,snapshot=SNAPSHOT,inventory=INVENTORY,scenario=SCENARIO;
const requests=[],errors=[]; let phase=0, observed={};
window.addEventListener('error',e=>errors.push(String(e.message)));
window.addEventListener('unhandledrejection',e=>errors.push(String(e.reason)));
window.fetch=async function(url){
 const path=String(url);requests.push(path);
 if(path.includes('firmware_release_inventory.json')) {
   if(scenario==='missing-inventory')return {ok:false,status:404};
   return {ok:true,json:async()=>inventory};
 }
 if(path.includes('firmware_releases.json')) {
   if(scenario==='missing') return {ok:false,status:404};
   return {ok:true,json:async()=>snapshot};
 }
 if(path.includes('/releases?')) {
   if(scenario==='fast'||scenario==='race') return await new Promise(()=>{});
   return {ok:true,json:async()=>releases};
 }
 if(path.includes('firmware_controls_1.18.0.1.json')) return {ok:true,json:async()=>controls['1.18.0.1']};
 if(path.includes('firmware_controls.json')) {
   if(scenario==='race') await new Promise(r=>setTimeout(r,1600));
   return {ok:true,json:async()=>controls['1.18.0.2']};
 }
 return {ok:false,status:404,json:async()=>null};
};
window.addEventListener('DOMContentLoaded',()=>{
 const start=performance.now(), deadline=Date.now()+15000;
 const root=document.querySelector('[data-firmware-picker]');
 root.setAttribute('data-snapshot-url',scenario==='cross-origin'?'https://example.invalid/firmware_releases.json':'../_data/firmware_releases.json');
 const select=root.querySelector('[data-role="release-version"]');
 if(scenario==='race')setTimeout(()=>{select.value='1.18.0.1';select.dispatchEvent(new Event('change'));},0);
 function done(result){const req=new XMLHttpRequest();req.open('POST','/result');req.send(JSON.stringify(result));}
 function check(){
  const status=root.querySelector('[data-role="release-set"]').textContent;
  const role=root.querySelector('[data-field="role"]').textContent;
  if(scenario==='fast'&&phase===0&&status.includes('Version 1.18.0.2')&&role.includes('Repeater')){
   observed.first=performance.now()-start;observed.firstStatus=status;
   select.value='1.18.0.1';select.dispatchEvent(new Event('change'));phase=1;
  }else if(scenario==='fast'&&phase===1&&status.includes('Version 1.18.0.1')&&role.includes('Repeater')){
   return done({status,role,selected:select.value,requests,errors,observed});
  }else if(scenario==='race'&&phase===0&&status.includes('Version 1.18.0.1')){
   phase=1;setTimeout(()=>done({status:root.querySelector('[data-role="release-set"]').textContent,
    selected:select.value,requests,errors}),2200);return;
  }else if(scenario!=='fast'&&scenario!=='race'&&status.includes('Version 1.18.0.2')&&role.includes('Repeater')){
   return done({status,role,selected:select.value,requests,errors});
  }
  if(errors.length||Date.now()>=deadline)return done({status,role,selected:select.value,requests,errors,phase,timeout:true});
  setTimeout(check,25);
 }check();
});
</script>'''
        inventory={'schemaVersion':1,'families':copy.deepcopy(original['families'])}
        if scenario=='wrong-inventory':
            family=controls['1.18.0.2']['familyTag'];inventory['families'][family]['source']='f'*40
        for key, value in [('CONTROLS', controls), ('RELEASES', releases),
                           ('SNAPSHOT', snapshot), ('INVENTORY', inventory), ('SCENARIO', scenario)]:
            bootstrap = bootstrap.replace('='+key, '='+json.dumps(value, separators=(',', ':')))
        # Set the snapshot URL in the markup before picker initialization.
        markup = (ROOT/'docs/firmware_picker.md').read_text(encoding='utf-8')
        if 'data-snapshot-url=' not in markup:
            markup = markup.replace('data-firmware-picker ', 'data-firmware-picker data-snapshot-url="../_data/firmware_releases.json" ', 1)
        if 'data-inventory-url=' not in markup:
            markup = markup.replace('data-firmware-picker ', 'data-firmware-picker data-inventory-url="../_data/firmware_release_inventory.json" ', 1)
        if scenario == 'cross-origin':
            markup = re.sub(r'data-snapshot-url="[^"]*"',
                            'data-snapshot-url="https://example.invalid/firmware_releases.json"', markup, count=1)
        html = '<!doctype html><meta charset="utf-8">'+bootstrap+markup+'<script src="/picker.js"></script>'
        results, done = [], threading.Event()
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                content = (ROOT/'docs/_javascript/firmware_picker.js').read_bytes() if self.path.startswith('/picker.js') else html.encode()
                self.send_response(200);self.send_header('Content-Length',str(len(content)))
                self.send_header('Content-Type','application/javascript' if self.path.startswith('/picker.js') else 'text/html')
                self.end_headers();self.wfile.write(content)
            def do_POST(self):
                results.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'OK');done.set()
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix='picker-snapshot-',ignore_cleanup_errors=True) as folder:
                cmd=[BROWSER,'--headless=new','--disable-gpu','--no-first-run','--user-data-dir='+folder,
                     'http://127.0.0.1:%s/?s=1.hHeltec_t1' % server.server_port]
                if os.name!='nt' and hasattr(os,'geteuid') and os.geteuid()==0:cmd.insert(1,'--no-sandbox')
                run_until_complete(cmd,done,timeout=35)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)
        self.assertEqual(len(results),1,results)
        self.assertEqual(results[0]['errors'],[],results)
        self.assertFalse(results[0].get('timeout'),results)
        return results[0]

    def test_snapshot_renders_both_versions_without_github(self):
        result=self.run_browser('fast')
        self.assertEqual(result['selected'],'1.18.0.1')
        self.assertFalse(any('/releases?' in x for x in result['requests']),result)
        self.assertLess(result['observed']['first'],5000,result)

    def test_missing_malformed_and_partial_snapshot_fall_back(self):
        _, _, snapshot=fixture()
        cases={'missing':snapshot,'cross-origin':snapshot,'missing-inventory':snapshot,'wrong-inventory':snapshot,'schema':dict(snapshot,schemaVersion=2),
               'bad-date':dict(snapshot,generatedAt='not a date'),
               'missing-family':dict(snapshot,families={}), 'missing-release':dict(snapshot,releases=snapshot['releases'][:-1])}
        corrupted=copy.deepcopy(snapshot)
        family=fixture()[0]['1.18.0.2']['familyTag']
        corrupted['families'][family]['assetCounts'][corrupted['families'][family]['releaseTags'][-1]]=17
        cases['asset-count']=corrupted
        mismatch=copy.deepcopy(snapshot);mismatch['families'][family]['source']='0'*40;cases['wrong-source']=mismatch
        removed=copy.deepcopy(snapshot);removed_tag=removed['families'][family]['releaseTags'][-1]
        removed['releases']=[row for row in removed['releases'] if row['tag_name']!=removed_tag]
        removed['families'][family]['releaseTags'].remove(removed_tag)
        del removed['families'][family]['assetCounts'][removed_tag]
        cases['removed-chunk-and-manifest']=removed
        cross=copy.deepcopy(snapshot);cross['releases'][-1]['assets'][0]['browser_download_url']='https://github.com/other/repo/releases/download/v/file.uf2';cases['wrong-repository']=cross
        wrong_tag=copy.deepcopy(snapshot);wrong_tag['releases'][-1]['assets'][0]['browser_download_url']=wrong_tag['releases'][-1]['assets'][0]['browser_download_url'].replace('/releases/download/lora-ota-', '/releases/download/other-');cases['wrong-release-url']=wrong_tag
        empty=copy.deepcopy(snapshot)
        for row in empty['releases']:
            if row['tag_name'] in empty['families'][family]['releaseTags']:
                row['assets']=[];empty['families'][family]['assetCounts'][row['tag_name']]=0
        cases['no-profiles']=empty
        for name,data in cases.items():
            with self.subTest(name=name):
                result=self.run_browser(name if name in ('missing','cross-origin','missing-inventory','wrong-inventory') else 'fallback',data)
                self.assertTrue(any('/releases?' in x for x in result['requests']),result)
                self.assertFalse(any('example.invalid' in x for x in result['requests']),result)

    def test_late_old_response_does_not_override_selected_version(self):
        result=self.run_browser('race')
        self.assertEqual(result['selected'],'1.18.0.1',result)
        self.assertIn('Version 1.18.0.1',result['status'],result)

    def test_new_qualified_supplement_requires_live_refresh(self):
        controls,releases,snapshot=fixture()
        tag='heltec-t114-v1.18.0.2.preview1'
        family='v1.18.0.2.preview1-bbbbbbbb'
        controls['1.18.0.2']['supplementalReleases']=[{'releaseTag':tag,'familyTag':family,
            'source':'b'*40,'profiles':{}}]
        name='Heltec_t114_repeater-full-ota-'+family+'.uf2'
        releases.append({'tag_name':tag,'name':'T114 preview','html_url':'https://github.com/mikecarper/MeshCore/releases/tag/'+tag,
            'published_at':'2026-11-14T00:00:00Z','created_at':'2026-11-14T00:00:00Z','draft':False,
            'prerelease':True,'assets':[{'name':name,'size':1000,
            'browser_download_url':'https://github.com/mikecarper/MeshCore/releases/download/'+tag+'/'+name}]})
        result=self.run_browser('fallback',snapshot,(controls,releases,snapshot))
        self.assertTrue(any('/releases?' in x for x in result['requests']),result)


if __name__ == '__main__':unittest.main()
