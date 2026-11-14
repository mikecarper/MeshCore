#!/usr/bin/env python3
"""The release consolidator must preserve its input directories."""

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from consolidate_local_release import safe_paths


class ConsolidationPathTest(unittest.TestCase):
    def test_output_cannot_overwrite_or_contain_either_input(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base, extra = root / 'base', root / 'extra'
            base.mkdir()
            extra.mkdir()
            safe_paths(base, extra, root / 'new-release')
            for output in (base, extra, base / 'nested', extra / 'nested', root):
                with self.subTest(output=output), self.assertRaisesRegex(ValueError, 'separate'):
                    safe_paths(base, extra, output)
            with self.assertRaisesRegex(ValueError, 'separate'):
                safe_paths(base, base, root / 'new-release')


if __name__ == '__main__':
    unittest.main()
