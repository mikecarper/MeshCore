#!/usr/bin/env python3
"""Verify release snapshot completeness and release-bound provenance."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('release_snapshot', ROOT/'scripts/build_firmware_release_snapshot.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
FAMILY = 'v1.18.0.2-dev-aaaaaaaa'
SOURCE = 'a'*40


def release(tag):
    name = 'Heltec_t1_repeater-full-ota-'+FAMILY+'.uf2'
    return {'name':'Public release', 'tag_name':tag, 'draft':False, 'prerelease':False,
            'html_url':MODULE.REPO+'/releases/tag/'+tag, 'published_at':'2026-11-14T00:00:00Z',
            'created_at':'2026-11-14T00:00:00Z', 'private_authorization':'not public',
            'assets':[{'name':name, 'size':123, 'uploader':{'token':'not public'},
                       'browser_download_url':MODULE.REPO+'/releases/download/'+tag+'/'+name}]}


def controls():
    return {'familyTag':FAMILY, 'source':SOURCE, 'profiles':{}}


class SnapshotTests(unittest.TestCase):
    def test_compacts_exact_family_and_all_chunk_releases(self):
        tags = [FAMILY, 'lora-ota-'+FAMILY, 'lora-ota-2-'+FAMILY, 'companion-2-'+FAMILY]
        rows = [release(tag) for tag in tags] + [release('unrelated-preview')]
        original = copy.deepcopy(rows)
        result = MODULE.snapshot_for_releases(rows, [controls()], now='2026-11-14T00:00:00Z')
        self.assertEqual(result['schemaVersion'], 1)
        self.assertEqual(result['generatedAt'], '2026-11-14T00:00:00Z')
        self.assertEqual(result['families'][FAMILY], {'source':SOURCE, 'releaseTags':sorted(tags),
                         'assetCounts':{tag:1 for tag in sorted(tags)}})
        self.assertEqual({x['tag_name'] for x in result['releases']},set(tags))
        self.assertNotIn('not public',json.dumps(result))
        self.assertEqual(rows,original)

    def test_rejects_missing_primary_gap_duplicate_and_wrong_source(self):
        cases = [([release('lora-ota-'+FAMILY)],controls()),
                 ([release(FAMILY),release('lora-ota-'+FAMILY),release('lora-ota-3-'+FAMILY)],controls()),
                 ([release(FAMILY),release(FAMILY)],controls()),
                 ([release(FAMILY)],dict(controls(),source='b'*40)),
                 ([dict(release(FAMILY),draft=True)],controls())]
        for rows, metadata in cases:
            with self.subTest(rows=rows,metadata=metadata):
                with self.assertRaises(ValueError):MODULE.snapshot_for_releases(rows,[metadata])

    def test_rejects_invalid_duplicate_or_cross_release_asset(self):
        base=release(FAMILY)
        cases=[]
        r=copy.deepcopy(base);r['assets']=[];cases.append(r)
        r=copy.deepcopy(base);r['assets']*=2;cases.append(r)
        r=copy.deepcopy(base);r['assets'][0]['browser_download_url']=r['assets'][0]['browser_download_url'].replace('/'+FAMILY+'/', '/different/');cases.append(r)
        r=copy.deepcopy(base);r['assets'][0]['size']=-1;cases.append(r)
        for row in cases:
            with self.subTest(row=row):
                with self.assertRaises(ValueError):MODULE.snapshot_for_releases([row],[controls()])

    def test_qualified_supplemental_release_is_required_and_retained(self):
        tag='heltec-t114-v1.18.0.2.preview1'
        metadata=controls()
        metadata['supplementalReleases']=[{'releaseTag':tag,'familyTag':'v1.18.0.2.preview1-bbbbbbbb',
                                           'source':'b'*40,'profiles':{}}]
        with self.assertRaises(ValueError):MODULE.snapshot_for_releases([release(FAMILY)],[metadata])
        result=MODULE.snapshot_for_releases([release(FAMILY),release(tag)],[metadata])
        self.assertIn(tag,result['families'][FAMILY]['releaseTags'])
        self.assertEqual(result['families'][FAMILY]['assetCounts'][tag],1)
        metadata['supplementalReleases'][0]['source']='c'*40
        with self.assertRaises(ValueError):MODULE.snapshot_for_releases([release(FAMILY),release(tag)],[metadata])

    def test_paginated_fetch_fails_closed_when_later_page_invalid(self):
        requests=[]
        def opener(request,timeout):
            requests.append(request.full_url)
            return io.BytesIO(json.dumps([{'tag_name':str(i)} for i in range(100)] if len(requests)==1 else [{'tag_name':'last'}]).encode())
        result=MODULE.fetch_github_releases(token='private',opener=opener)
        self.assertEqual(len(result),101)
        self.assertTrue(requests[0].endswith('?per_page=100&page=1'))
        self.assertTrue(requests[1].endswith('?per_page=100&page=2'))
        attempts=[]
        def bad(request,timeout):
            attempts.append(request.full_url)
            return io.BytesIO(json.dumps([{}]*100 if len(attempts)==1 else {'error':'truncated'}).encode())
        with self.assertRaises(ValueError):MODULE.fetch_github_releases(opener=bad)

    def test_transient_retries_do_not_leak_credentials_and_permanent_errors_stop(self):
        requests=[]; delays=[]
        def flaky(request,timeout):
            requests.append(request)
            if len(requests)<3: raise HTTPError(request.full_url,503,'unavailable',{},None)
            return io.BytesIO(b'[]')
        self.assertEqual(MODULE.fetch_github_releases(token='secret-token',opener=flaky,sleep=delays.append),[])
        self.assertEqual(len(requests),3)
        self.assertEqual(delays,[1,2])
        self.assertTrue(all('secret-token' not in request.full_url for request in requests))
        failures=[]
        def denied(request,timeout):
            failures.append(request)
            raise HTTPError(request.full_url,403,'secret-token',{},None)
        with self.assertRaisesRegex(RuntimeError,'HTTP 403') as caught:
            MODULE.fetch_github_releases(token='secret-token',opener=denied,sleep=delays.append)
        self.assertNotIn('secret-token',str(caught.exception))
        self.assertEqual(len(failures),1)

    def test_reviewed_baseline_rejects_missing_last_page_or_lost_assets(self):
        tags=[FAMILY,'lora-ota-'+FAMILY,'lora-ota-2-'+FAMILY]
        snapshot=MODULE.snapshot_for_releases([release(tag) for tag in tags],[controls()])
        baseline={'schemaVersion':1,'families':copy.deepcopy(snapshot['families'])}
        MODULE.validate_snapshot_baseline(snapshot,baseline)
        missing=copy.deepcopy(snapshot);del missing['families'][FAMILY]['assetCounts'][tags[-1]]
        fewer=copy.deepcopy(snapshot);fewer['families'][FAMILY]['assetCounts'][tags[1]]=0
        wrong=copy.deepcopy(snapshot);wrong['families'][FAMILY]['source']='b'*40
        for invalid in (missing,fewer,wrong):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):MODULE.validate_snapshot_baseline(invalid,baseline)
        increased=copy.deepcopy(snapshot)
        increased['families'][FAMILY]['assetCounts'][tags[1]]=2
        increased['families'][FAMILY]['assetCounts']['lora-ota-3-'+FAMILY]=1
        MODULE.validate_snapshot_baseline(increased,baseline)


if __name__ == '__main__':unittest.main()
