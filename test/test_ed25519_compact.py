import ctypes
import hashlib
import random
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ED25519 = ROOT / "lib" / "ed25519"
HARNESS = ROOT / "test" / "fixtures" / "ed25519_compact" / "test_ed25519_compact.c"
SOURCES = [
    ED25519 / name
    for name in (
        "fe.c",
        "ge.c",
        "keypair.c",
        "sc.c",
        "sha512.c",
        "sign.c",
        "verify.c",
    )
]


class SHA512Context(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint64),
        ("state", ctypes.c_uint64 * 8),
        ("curlen", ctypes.c_size_t),
        ("buf", ctypes.c_ubyte * 128),
    ]


class Ed25519CompactTest(unittest.TestCase):
    def test_small_flash_table_matches_standard_for_scalars_keys_and_signatures(self):
        # Exercise every bit/carry boundary and deterministic random scalars,
        # rather than validating only the two RFC keypair examples.
        with tempfile.TemporaryDirectory() as directory:
            wrapper = Path(directory) / "wrapper.c"
            wrapper.write_text('''#include "ge.h"
void public_from_scalar(unsigned char *out, const unsigned char *scalar) {
    ge_p3 point;
    ge_scalarmult_base(&point, scalar);
    ge_p3_tobytes(out, &point);
}
''')
            libraries = []
            for label, flags in (
                ("standard", []),
                ("small_sha512", ["-DED25519_SMALL_BASE=1", "-DED25519_COMPACT_SHA512=1"]),
                ("small_all", ["-DED25519_SMALL_BASE=1", "-DED25519_COMPACT_SHA512=1",
                               "-DED25519_COMPACT_SCALAR=1"]),
                ("small_field", ["-DED25519_SMALL_BASE=1", "-DED25519_COMPACT_SHA512=1",
                                 "-DED25519_COMPACT_SCALAR=1", "-DED25519_COMPACT_FIELD=1"]),
                ("compact_field", ["-DED25519_COMPACT_BASE=1", "-DED25519_COMPACT_SHA512=1",
                                   "-DED25519_COMPACT_SCALAR=1", "-DED25519_COMPACT_FIELD=1"]),
            ):
                path = Path(directory) / (label + ".so")
                subprocess.run(["cc", "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror",
                                "-shared", "-fPIC", "-DED25519_NO_SEED=1", *flags,
                                "-I" + str(ED25519), str(wrapper),
                                *(str(source) for source in SOURCES),
                                str(ED25519 / "key_exchange.c"), "-o", str(path)], check=True)
                library = ctypes.CDLL(str(path))
                library.public_from_scalar.argtypes = [ctypes.c_void_p] * 2
                library.ed25519_create_keypair.argtypes = [ctypes.c_void_p] * 3
                library.ed25519_sign.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                                ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
                library.ed25519_key_exchange.argtypes = [ctypes.c_void_p] * 3
                library.ed25519_verify.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                                  ctypes.c_size_t, ctypes.c_void_p]
                library.ed25519_verify.restype = ctypes.c_int
                libraries.append(library)
            rng = random.Random(0xED960)
            scalars = [0, (1 << 255) - 1]
            scalars += [value for bit in range(255)
                        for value in ((1 << bit) - 1, 1 << bit, (1 << bit) + 1)]
            scalars += [rng.getrandbits(255) for _ in range(1024)]
            for scalar in scalars:
                outputs = []
                for library in libraries:
                    out = ctypes.create_string_buffer(32)
                    library.public_from_scalar(out, scalar.to_bytes(32, "little"))
                    outputs.append(out.raw)
                for output in outputs[1:]:
                    self.assertEqual(outputs[0], output, "scalar=%x" % scalar)
            for iteration in range(128):
                seed = rng.getrandbits(256).to_bytes(32, "little")
                peer_seed = rng.getrandbits(256).to_bytes(32, "little")
                length = (0, 1, 111, 112, 127, 128, 129, 255, 256, 4097)[iteration % 10]
                message = bytes(rng.randrange(256) for _ in range(length))
                outputs = []
                for library in libraries:
                    pub, prv = ctypes.create_string_buffer(32), ctypes.create_string_buffer(64)
                    peer, peer_prv = ctypes.create_string_buffer(32), ctypes.create_string_buffer(64)
                    sig, secret = ctypes.create_string_buffer(64), ctypes.create_string_buffer(32)
                    library.ed25519_create_keypair(pub, prv, seed)
                    library.ed25519_create_keypair(peer, peer_prv, peer_seed)
                    library.ed25519_sign(sig, message, len(message), pub, prv)
                    library.ed25519_key_exchange(secret, peer, prv)
                    self.assertEqual(library.ed25519_verify(sig, message, len(message), pub), 1)
                    damaged = bytearray(sig.raw)
                    damaged[iteration % len(damaged)] ^= 1 << (iteration % 8)
                    self.assertEqual(library.ed25519_verify(bytes(damaged), message, len(message), pub), 0)
                    wrong_message = bytes([message[0] ^ 1]) + message[1:] if message else b"x"
                    self.assertEqual(library.ed25519_verify(sig, wrong_message, len(wrong_message), pub), 0)
                    outputs.append((pub.raw, prv.raw, sig.raw, secret.raw))
                for output in outputs[1:]:
                    self.assertEqual(outputs[0], output)

    def build_sha512_library(self, temp_dir, compact):
        library = Path(temp_dir) / f"sha512-{int(compact)}.so"
        flags = ["-DED25519_COMPACT_SHA512=1"] if compact else []
        subprocess.run(["cc", "-std=c99", "-O2", "-shared", "-fPIC",
                        *flags, str(ED25519 / "sha512.c"),
                        "-o", str(library)], check=True)
        sha = ctypes.CDLL(str(library))
        sha.sha512.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
        sha.sha512.restype = ctypes.c_int
        sha.sha512_init.argtypes = [ctypes.POINTER(SHA512Context)]
        sha.sha512_init.restype = ctypes.c_int
        sha.sha512_update.argtypes = [ctypes.POINTER(SHA512Context),
                                     ctypes.c_void_p, ctypes.c_size_t]
        sha.sha512_update.restype = ctypes.c_int
        sha.sha512_final.argtypes = [ctypes.POINTER(SHA512Context), ctypes.c_void_p]
        sha.sha512_final.restype = ctypes.c_int
        return sha

    def test_sha512_matches_hashlib_at_block_boundaries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for compact in (False, True):
                sha = self.build_sha512_library(temp_dir, compact)
                for length in (0, 1, 63, 64, 111, 112, 127, 128, 129,
                               239, 240, 255, 256, 257, 1024, 4097):
                    data = bytes((i * 73 + 19) % 256 for i in range(length))
                    output = ctypes.create_string_buffer(64)
                    with self.subTest(compact=compact, length=length):
                        self.assertEqual(sha.sha512(data, len(data), output), 0)
                        self.assertEqual(output.raw, hashlib.sha512(data).digest())

    def test_sha512_streaming_contexts_match_hashlib(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for compact in (False, True):
                sha = self.build_sha512_library(temp_dir, compact)
                rng = random.Random(0x512)
                lengths = [0, 1, 111, 112, 127, 128, 129, 255, 256, 257,
                           4096, 16385]
                lengths.extend(rng.randrange(1, 32769) for _ in range(20))
                for length in lengths:
                    with self.subTest(compact=compact, length=length):
                        # Interleave two live contexts and irregular updates,
                        # including empty updates and multiple full blocks.
                        messages = [bytes(rng.randrange(256) for _ in range(n))
                                    for n in (length, length + 137)]
                        contexts = [SHA512Context(), SHA512Context()]
                        positions = [0, 0]
                        for context in contexts:
                            self.assertEqual(sha.sha512_init(ctypes.byref(context)), 0)
                            self.assertEqual(sha.sha512_update(ctypes.byref(context),
                                                              b"", 0), 0)
                        while any(position < len(message)
                                  for position, message in zip(positions, messages)):
                            for index, (context, message) in enumerate(zip(contexts, messages)):
                                position = positions[index]
                                if position == len(message):
                                    continue
                                size = rng.choice((1, 7, 63, 64, 111, 112, 127,
                                                   128, 129, 257, 1024))
                                chunk = message[position:position + size]
                                self.assertEqual(sha.sha512_update(ctypes.byref(context),
                                                                  chunk, len(chunk)), 0)
                                positions[index] += len(chunk)
                        for context, message in zip(contexts, messages):
                            output = ctypes.create_string_buffer(64)
                            self.assertEqual(sha.sha512_update(ctypes.byref(context),
                                                              b"", 0), 0)
                            self.assertEqual(sha.sha512_final(ctypes.byref(context), output), 0)
                            self.assertEqual(output.raw, hashlib.sha512(message).digest())

    def test_stm32_builds_enable_compact_base_table(self):
        platformio = (ROOT / "platformio.ini").read_text()
        stm32_base = platformio.split("[stm32_base]", 1)[1].split("\n[", 1)[0]
        self.assertIn("-D ED25519_COMPACT_BASE=1", stm32_base)

    def test_standard_and_compact_paths_match_rfc8032(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for label, extra_flags in (
                ("standard", []),
                ("compact", ["-DED25519_COMPACT_BASE=1"]),
                ("small", ["-DED25519_SMALL_BASE=1"]),
                ("small_sha512", ["-DED25519_SMALL_BASE=1", "-DED25519_COMPACT_SHA512=1"]),
                ("small_all", ["-DED25519_SMALL_BASE=1", "-DED25519_COMPACT_SHA512=1",
                               "-DED25519_COMPACT_SCALAR=1"]),
                ("compact_sha512", ["-DED25519_COMPACT_SHA512=1"]),
                ("compact_base_sha512", ["-DED25519_COMPACT_BASE=1",
                                         "-DED25519_COMPACT_SHA512=1"]),
                ("compact_scalar", ["-DED25519_COMPACT_SCALAR=1"]),
                ("compact_all", ["-DED25519_COMPACT_BASE=1",
                                 "-DED25519_COMPACT_SHA512=1",
                                 "-DED25519_COMPACT_SCALAR=1"]),
                ("compact_field", ["-DED25519_COMPACT_BASE=1",
                                   "-DED25519_COMPACT_SHA512=1",
                                   "-DED25519_COMPACT_SCALAR=1",
                                   "-DED25519_COMPACT_FIELD=1"]),
            ):
                executable = Path(temp_dir) / label
                subprocess.run(
                    [
                        "cc",
                        "-std=c99",
                        "-O2",
                        "-Wall",
                        "-Wextra",
                        "-Werror",
                        "-DED25519_NO_SEED=1",
                        *extra_flags,
                        f"-I{ED25519}",
                        str(HARNESS),
                        *(str(source) for source in SOURCES),
                        "-o",
                        str(executable),
                    ],
                    check=True,
                    cwd=ROOT,
                )
                subprocess.run([str(executable)], check=True, cwd=ROOT)


if __name__ == "__main__":
    unittest.main()
