#!/usr/bin/env python3
"""Keep source-visible links aligned with the pages MkDocs actually publishes."""

import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
from urllib.parse import urljoin, urlsplit

HOSTED_BASE = 'https://mikecarper.github.io/MeshCore/'
BEGIN = '<!-- meshcore-hosted-doc-link:start -->'
END = '<!-- meshcore-hosted-doc-link:end -->'
SCRIPT = '  - _javascript/hosted_doc_links.js?v=20261007-1\n'
SUPPORT_FILES = (
    'scripts/update_hosted_doc_links.py',
    'docs/_javascript/hosted_doc_links.js',
    'test/test_hosted_doc_links.py',
    'test/test_hosted_doc_links.js',
)
WORKFLOW_JOB = '''
  hosted-doc-links:
    runs-on: ubuntu-26.04
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v6
        with:
          python-version: '3.13'
      - name: Install the documentation renderer
        run: python3 -m pip install mkdocs==1.6.1 mkdocs-material==9.7.7
      - name: Verify source links and published page routes
        run: python3 -B test/test_hosted_doc_links.py -v
      - name: Verify hosted-link visibility and navigation
        run: node test/test_hosted_doc_links.js
'''


def routes(repo_root):
    """Use MkDocs' own URL mapping, including non-navigation historical pages."""
    from mkdocs.config import load_config
    from mkdocs.structure.files import get_files
    config = load_config(str(repo_root / 'mkdocs.yml'))
    documents = []
    for file in get_files(config):
        if not file.is_documentation_page() or not file.inclusion.is_included():
            continue
        url = urljoin(HOSTED_BASE, file.url)
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.netloc != 'mikecarper.github.io'
                or not parsed.path.startswith('/MeshCore/')):
            raise ValueError('MkDocs page URL escaped the hosted documentation site')
        path = Path(file.abs_src_path)
        if path.is_symlink() or not path.resolve().is_relative_to(Path(config.docs_dir).resolve()):
            raise ValueError('Unsafe documentation source path')
        documents.append({'source': file.src_uri, 'url': url, 'path': path})
    if not documents:
        raise ValueError('MkDocs did not discover any documentation pages')
    return sorted(documents, key=lambda row: row['source'])


def split_frontmatter(text):
    """Keep an optional BOM and YAML metadata before the source-visible banner."""
    prefix = '\ufeff' if text.startswith('\ufeff') else ''
    body = text[len(prefix):]
    if not re.match(r'\A---(?:\r\n|\n)', body):
        return prefix, body
    lines = body.splitlines(keepends=True)
    for index in range(1, len(lines)):
        if lines[index].rstrip('\r\n') in ('---', '...'):
            return prefix + ''.join(lines[:index + 1]), ''.join(lines[index + 1:])
    raise ValueError('Unterminated documentation frontmatter')


def strip_banner(body):
    if BEGIN not in body and END not in body:
        return body
    if body.count(BEGIN) != 1 or body.count(END) != 1 or not body.startswith(BEGIN + '\n'):
        raise ValueError('Hosted documentation banner is duplicated or misplaced')
    ending = body.find(END) + len(END)
    if body[ending:ending + 2] != '\n\n':
        raise ValueError('Hosted documentation banner delimiter is incomplete')
    return body[ending + 2:]


def with_banner(text, url):
    prefix, body = split_frontmatter(text)
    original = strip_banner(body)
    banner = (BEGIN + '\n<p class="meshcore-hosted-doc-link"><a href="'
              + html.escape(url, quote=True)
              + '">View this page on MeshCore Docs</a>.</p>\n' + END + '\n\n')
    return prefix + banner + original


def document_body(text):
    """Remove only our top banner, leaving the document's original bytes intact."""
    prefix, body = split_frontmatter(text)
    return prefix + strip_banner(body)


def transform(repo_root, *, apply=False):
    rows = []
    for document in routes(repo_root):
        path = document['path']
        before = path.read_bytes()
        text = before.decode('utf-8')
        after = with_banner(text, document['url']).encode('utf-8')
        if document_body(text).encode('utf-8') != document_body(after.decode('utf-8')).encode('utf-8'):
            raise ValueError('A document body changed while adding its hosted link')
        if apply and before != after:
            path.write_bytes(after)
        rows.append({'source': document['source'], 'url': document['url'],
                     'already_current': before == after,
                     'body_sha256': hashlib.sha256(document_body(text).encode('utf-8')).hexdigest(),
                     'document_sha256': hashlib.sha256(after).hexdigest()})
    return {'schema': 'meshcore.docs.hosted-page-links.v1', 'hosted_base': HOSTED_BASE,
            'page_count': len(rows), 'applied': apply, 'pages': rows}


def install_hooks(repo_root):
    """Add only our script/job; preserve other agents' config and workflow edits."""
    source_root = Path(__file__).resolve().parents[1]
    for name in SUPPORT_FILES:
        source, destination = source_root / name, repo_root / name
        if source.resolve() == destination.resolve():
            continue
        if destination.exists() and destination.read_bytes() != source.read_bytes():
            raise ValueError('Refusing to replace a different hosted-link support file: ' + name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    config_path = repo_root / 'mkdocs.yml'
    config = config_path.read_text(encoding='utf-8')
    if config.count('extra_javascript:\n') != 1:
        raise ValueError('Expected one MkDocs JavaScript section')
    existing = re.findall(r'^  - _javascript/hosted_doc_links\.js[^\n]*\n', config, re.MULTILINE)
    if len(existing) > 1 or existing and existing[0] != SCRIPT:
        raise ValueError('Unexpected hosted-link JavaScript registration')
    if not existing:
        config_path.write_text(config.replace('extra_javascript:\n', 'extra_javascript:\n' + SCRIPT),
                               encoding='utf-8')
    workflow_path = repo_root / '.github/workflows/run-unit-tests.yml'
    workflow = workflow_path.read_text(encoding='utf-8')
    if re.search(r'^  hosted-doc-links:', workflow, re.MULTILINE):
        if workflow.count(WORKFLOW_JOB.strip('\n')) != 1:
            raise ValueError('An unexpected hosted-link workflow job already exists')
    else:
        workflow_path.write_text(workflow.rstrip('\n') + '\n' + WORKFLOW_JOB, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true', help='Update page banners and add their shared script/test job')
    parser.add_argument('--manifest', type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.apply:
        install_hooks(root)
    manifest = transform(root, apply=args.apply)
    if args.manifest:
        args.manifest.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n', encoding='ascii')
    pending = sum(not row['already_current'] for row in manifest['pages'])
    print(json.dumps({'pages': manifest['page_count'], 'updated' if args.apply else 'pending': pending}))
    if not args.apply and pending:
        parser.exit(1, 'Hosted documentation links differ; run this script with --apply.\n')


if __name__ == '__main__':
    main()
