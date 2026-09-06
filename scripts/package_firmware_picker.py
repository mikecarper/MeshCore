#!/usr/bin/env python3
"""Package the web picker and release-bound controls as one offline HTML file."""
import argparse
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def package(releases, controls):
    if not any(r['tag_name'] == controls['familyTag'] for r in releases):
        raise ValueError('Controls must match the supplied release family')
    # Retain only data used by the picker; never embed authentication or API metadata.
    compact = []
    for release in releases:
        row = {k: release.get(k) for k in ('name', 'tag_name', 'html_url', 'published_at', 'created_at', 'draft', 'prerelease')}
        row['assets'] = [{k: asset[k] for k in ('name', 'browser_download_url', 'size')}
                         for asset in release['assets']]
        compact.append(row)
    markdown = (ROOT / 'docs/firmware_picker.md').read_text()
    form = markdown[markdown.index('<div class="firmware-picker"'):markdown.index('\n## What the choices mean')]
    form = form.replace('Loading release information...', 'Loading embedded release information...')
    payload = json.dumps({'releases': compact, 'controls': controls}).replace('<', '\\u003c')
    css = (ROOT / 'docs/_stylesheets/firmware_picker.css').read_text()
    js = (ROOT / 'docs/_javascript/firmware_picker.js').read_text()
    title = html.escape('MeshCore firmware picker — ' + controls['familyTag'])
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{title}</title><style>'
            ':root{--md-default-bg-color:white;--md-code-bg-color:#f3f5f7;--md-default-fg-color--light:#555}'
            'body{font:16px system-ui;max-width:1200px;margin:2rem auto;padding:0 1rem;color:#222}'
            'a{color:#07695d}button,summary{cursor:pointer}[hidden]{display:none!important}'
            + css + '</style></head><body><h1>' + title + '</h1>'
            '<p>Choose your exact hardware and desired settings. Release metadata and '
            'command directions are included in this file; downloads and the USB '
            'web console require an internet connection.</p>' + form
            + '<script type="application/json" id="firmware-picker-data">' + payload + '</script>'
            + '<script>' + js.replace('</script', '<\\/script') + '</script></body></html>\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--releases-json', type=Path, required=True, help='JSON array of public GitHub releases')
    parser.add_argument('--controls-json', type=Path, default=ROOT / 'docs/_data/firmware_controls.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(package(json.loads(args.releases_json.read_text()), json.loads(args.controls_json.read_text())))
    print('Wrote ' + str(args.output))
