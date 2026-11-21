"""Keep locator manufacturing records out of the RAK bootloader and neighboring storage."""
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples/rak_location"))
from check_provision import check_record


class Image(dict):
    def minaddr(self):
        return min(self, default=None)

    def maxaddr(self):
        return max(self, default=None)

    def __missing__(self, key):
        return 255


class LocatorProvisionTests(unittest.TestCase):
    def test_findhub_padding_and_corruption(self):
        # Non-credential test pattern, including the erased alignment byte.
        base = 0xc8000
        record = bytes.fromhex("fa57fa57") + b"\x00" * 3 + b"\xff" + b"\x00" * 32
        image = Image(enumerate(record + hashlib.sha256(record).digest(), base))
        del image[base + 7]
        self.assertEqual(check_record("findhub", image), (base, base + 71))
        image[base + 22] = 0x42
        with self.assertRaisesRegex(ValueError, "checksum"):
            check_record("findhub", image)

    def test_sidewalk_page_and_bounds(self):
        record = b"SID0" + bytes(12)
        base = 0xd1000
        image = Image(enumerate(record, base))
        self.assertEqual(check_record("sidewalk", image), (base, base + 15))
        image[base + 0x1000] = 255
        with self.assertRaisesRegex(ValueError, "page"):
            check_record("sidewalk", image)
        for address in (0, 0xc8000, 0xfd000, 0xff000):
            with self.subTest(address=address), self.assertRaisesRegex(ValueError, "page"):
                check_record("sidewalk", Image(enumerate(record, address)))

    def test_erased_and_wrong_format(self):
        for network, address in (("sidewalk", 0xd1000), ("findhub", 0xc8000)):
            with self.subTest(network=network):
                for image in (Image(), Image(enumerate(bytes([255]) * 100, address))):
                    with self.assertRaises(ValueError):
                        check_record(network, image)
