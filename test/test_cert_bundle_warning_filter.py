#!/usr/bin/env python3
"""Regression tests for serial-zero roots in the ESP32 CA bundle."""

import contextlib
import io
import os
from pathlib import Path
import runpy
import struct
import tempfile
import unittest
import warnings

from cryptography.utils import CryptographyDeprecationWarning


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_cert_bundle.py"

NON_POSITIVE_SERIAL_WARNING = (
    "Parsed a serial number which wasn't positive (i.e., it was negative or zero), "
    "which is disallowed by RFC 5280. Loading this certificate will cause an "
    "exception in a future release of cryptography."
)

# Go Daddy Root Certificate Authority - G2, one of the serial-zero roots in
# Mozilla and Adafruit trust bundles.
SERIAL_ZERO_ROOT = """-----BEGIN CERTIFICATE-----
MIIDxTCCAq2gAwIBAgIBADANBgkqhkiG9w0BAQsFADCBgzELMAkGA1UEBhMCVVMx
EDAOBgNVBAgTB0FyaXpvbmExEzARBgNVBAcTClNjb3R0c2RhbGUxGjAYBgNVBAoT
EUdvRGFkZHkuY29tLCBJbmMuMTEwLwYDVQQDEyhHbyBEYWRkeSBSb290IENlcnRp
ZmljYXRlIEF1dGhvcml0eSAtIEcyMB4XDTA5MDkwMTAwMDAwMFoXDTM3MTIzMTIz
NTk1OVowgYMxCzAJBgNVBAYTAlVTMRAwDgYDVQQIEwdBcml6b25hMRMwEQYDVQQH
EwpTY290dHNkYWxlMRowGAYDVQQKExFHb0RhZGR5LmNvbSwgSW5jLjExMC8GA1UE
AxMoR28gRGFkZHkgUm9vdCBDZXJ0aWZpY2F0ZSBBdXRob3JpdHkgLSBHMjCCASIw
DQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAL9xYgjx+lk09xvJGKP3gElY6SKD
E6bFIEMBO4Tx5oVJnyfq9oQbTqC023CYxzIBsQU+B07u9PpPL1kwIuerGVZr4oAH
/PMWdYA5UXvl+TW2dE6pjYIT5LY/qQOD+qK+ihVqf94Lw7YZFAXK6sOoBJQ7Rnwy
DfMAZiLIjWltNowRGLfTshxgtDj6AozO091GB94KPutdfMh8+7ArU6SSYmlRJQVh
GkSBjCypQ5Yj36w6gZoOKcUcqeldHraenjAKOc7xiID7S13MMuyFYkMlNAJWJwGR
tDtwKj9useiciAF9n9T521NtYJ2/LOdYq7hfRvzOxBsDPAnrSTFcaUaz4EcCAwEA
AaNCMEAwDwYDVR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMCAQYwHQYDVR0OBBYE
FDqahQcQZyi27/a9BUFuIMGU2g/eMA0GCSqGSIb3DQEBCwUAA4IBAQCZ21151fmX
WWcDYfF+OwYxdS2hII5PZYe096acvNjpL9DbWu7PdIxztDhC2gV7+AJ1uP2lsdeu
9tfeE8tTEH6KRtGX+rcuKxGrkLAngPnon1rpN5+r5N9ss4UXnT3ZJE95kTXWXwTr
gIOrmgIttRD02JDHBHNA7XIloKmf7J6raBKZV8aPEjoJpL1E/QYVN8Gb5DKj7Tjo
2GTzLH4U/ALqn83/B2gX2yKQOC16jdFU8WnjXzPKej17CuPKf1855eJ1usV2GDPO
LPAvTK33sefOT6jEm0pUBsV/fdUID+Ic/n4XuKxe9tQWskMJDE32p2u0mYRlynqI
4uJEvlz36hz1
-----END CERTIFICATE-----
"""


class FakeEnvironment:
    def subst(self, value):
        if value != "$PIOENV":
            raise AssertionError(f"unexpected substitution: {value}")
        return "certificate_filter_test"

    def GetProjectOption(self, name):
        if name != "board_ssl_cert_source":
            raise AssertionError(f"unexpected project option: {name}")
        # An unrecognized source skips the download path. The fixture is then
        # loaded through the script's supported extra-certificate path.
        return "fixture-only"

    def Execute(self, _command):
        raise AssertionError("test requires cryptography to be preinstalled")


class CertificateBundleWarningFilterTest(unittest.TestCase):
    def test_serial_zero_root_is_retained_and_filter_is_narrow(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            certificates = temporary / "ssl_certs"
            certificates.mkdir()
            (certificates / "serial_zero.pem").write_text(
                SERIAL_ZERO_ROOT, encoding="utf-8"
            )

            previous_directory = Path.cwd()
            stderr = io.StringIO()
            try:
                os.chdir(temporary)
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    with contextlib.redirect_stderr(stderr):
                        runpy.run_path(
                            str(SCRIPT),
                            init_globals={
                                "Import": lambda _name: None,
                                "env": FakeEnvironment(),
                            },
                        )

                    warnings.warn(
                        NON_POSITIVE_SERIAL_WARNING,
                        CryptographyDeprecationWarning,
                    )
                    warnings.warn(
                        "unrelated cryptography deprecation",
                        CryptographyDeprecationWarning,
                    )
            finally:
                os.chdir(previous_directory)

            self.assertEqual(len(caught), 1)
            self.assertEqual(
                str(caught[0].message), "unrelated cryptography deprecation"
            )
            self.assertIs(
                caught[0].category, CryptographyDeprecationWarning
            )

            output = temporary / "src" / "certs" / "x509_crt_bundle.bin"
            bundle = output.read_bytes()
            self.assertEqual(struct.unpack(">H", bundle[:2])[0], 1)
            self.assertGreater(len(bundle), 256)
            self.assertIn(
                "Successfully added 1 certificates in total",
                stderr.getvalue(),
            )


if __name__ == "__main__":
    unittest.main()
