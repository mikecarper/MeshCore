#!/usr/bin/env python3
"""Verify source banners against MkDocs' actual published URL mapping."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('hosted_doc_links', ROOT / 'scripts/update_hosted_doc_links.py')
LINKS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LINKS)


class HostedDocLinksTest(unittest.TestCase):
    def test_every_served_document_has_its_current_exact_link(self):
        manifest = LINKS.transform(ROOT)
        self.assertTrue(manifest['pages'])
        pending = [row['source'] for row in manifest['pages'] if not row['already_current']]
        self.assertEqual(pending, [],
                         'Missing or stale hosted links: ' + ', '.join(pending)
                         + '; run scripts/update_hosted_doc_links.py --apply')
        routes = {row['source']: row['url'] for row in manifest['pages']}
        self.assertEqual(routes['index.md'], LINKS.HOSTED_BASE)
        self.assertEqual(routes['research/index.md'], LINKS.HOSTED_BASE + 'research/')
        self.assertEqual(routes['WiFi.md'], LINKS.HOSTED_BASE + 'WiFi/')
        self.assertEqual(routes['releases/1.17.1.9.md'], LINKS.HOSTED_BASE + 'releases/1.17.1.9/')

    def test_mkdocs_routes_cover_spaces_case_nested_index_and_non_navigation_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'docs/Nested Space').mkdir(parents=True)
            (root / 'mkdocs.yml').write_text('site_name: Route fixture\ntheme: {name: mkdocs}\n'
                                           'not_in_nav: |\n  /Hidden.md\n'
                                           'exclude_docs: |\n  /Excluded.md\n')
            for name in ('index.md', 'WiFi.md', 'Hidden.md', 'Nested Space/index.md',
                         'Nested Space/Mixed Case.md', 'Excluded.md'):
                (root / 'docs' / name).write_bytes(('# ' + name + '\n\nBody stays the same.\n').encode())
            excluded_before = (root / 'docs/Excluded.md').read_bytes()
            manifest = LINKS.transform(root, apply=True)
            routes = {row['source']: row['url'] for row in manifest['pages']}
            self.assertEqual(routes, {
                'index.md': LINKS.HOSTED_BASE,
                'WiFi.md': LINKS.HOSTED_BASE + 'WiFi/',
                'Hidden.md': LINKS.HOSTED_BASE + 'Hidden/',
                'Nested Space/index.md': LINKS.HOSTED_BASE + 'Nested%20Space/',
                'Nested Space/Mixed Case.md': LINKS.HOSTED_BASE + 'Nested%20Space/Mixed%20Case/',
            })
            before = {p: p.read_bytes() for p in (root / 'docs').rglob('*.md')}
            self.assertTrue(all(row['already_current'] for row in LINKS.transform(root, apply=True)['pages']))
            self.assertEqual(before, {p: p.read_bytes() for p in before})
            self.assertEqual((root / 'docs/Excluded.md').read_bytes(), excluded_before)

    def test_original_body_frontmatter_bom_and_line_endings_are_preserved(self):
        for text in ('# Heading\n\nBody with <b>HTML</b>.\n',
                     '---\ntitle: Example\n---\n\n# Heading\n',
                     '\ufeff---\r\ntitle: Example\r\n...\r\n\r\n# Heading\r\n'):
            with self.subTest(text=text):
                updated = LINKS.with_banner(text, LINKS.HOSTED_BASE + 'Example/')
                self.assertEqual(LINKS.document_body(updated), text)
                self.assertEqual(LINKS.with_banner(updated, LINKS.HOSTED_BASE + 'Example/'), updated)
                if 'title: Example' in text:
                    self.assertLess(updated.index('title: Example'), updated.index(LINKS.BEGIN))

    def test_moved_page_gets_a_new_link_without_changing_its_body(self):
        body = '# Example\n\nOriginal text.\n'
        old = LINKS.with_banner(body, LINKS.HOSTED_BASE + 'old/')
        updated = LINKS.with_banner(old, LINKS.HOSTED_BASE + 'new/')
        self.assertNotIn('href="' + LINKS.HOSTED_BASE + 'old/', updated)
        self.assertIn('href="' + LINKS.HOSTED_BASE + 'new/', updated)
        self.assertEqual(LINKS.document_body(updated), body)

    def test_duplicate_misplaced_or_incomplete_banner_is_rejected(self):
        banner = LINKS.with_banner('# Heading\n', LINKS.HOSTED_BASE)
        for text in (banner + banner, '# Heading\n' + banner, banner.replace(LINKS.END + '\n\n', LINKS.END)):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    LINKS.with_banner(text, LINKS.HOSTED_BASE)

    def test_shared_script_is_registered_exactly_once(self):
        self.assertEqual((ROOT / 'mkdocs.yml').read_text().count(LINKS.SCRIPT), 1)
        self.assertTrue((ROOT / 'docs/_javascript/hosted_doc_links.js').is_file())

    def test_hooks_preserve_other_tool_config_and_workflow_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.github/workflows').mkdir(parents=True)
            config = 'site_name: Fixture\nextra_javascript:\n  - _javascript/telemetry_decoder.js?v=telemetry-fix2\n'
            workflow = ('name: Tests\njobs:\n  management-reports:\n    steps:\n'
                        '      - run: node scripts/test_telemetry_decoder.js\n')
            (root / 'mkdocs.yml').write_text(config)
            (root / '.github/workflows/run-unit-tests.yml').write_text(workflow)
            LINKS.install_hooks(root)
            self.assertEqual((root / 'mkdocs.yml').read_text().replace(LINKS.SCRIPT, ''), config)
            self.assertEqual((root / '.github/workflows/run-unit-tests.yml').read_text()
                             .replace(LINKS.WORKFLOW_JOB, ''), workflow)
            before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
            LINKS.install_hooks(root)
            self.assertEqual(before, {p: p.read_bytes() for p in before})


if __name__ == '__main__':
    unittest.main()
