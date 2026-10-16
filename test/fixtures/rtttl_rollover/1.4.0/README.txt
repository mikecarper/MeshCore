NonBlockingRTTTL 1.4.0 regression fixture

Source: PlatformIO end2endzone/NonBlockingRTTTL 1.4.0
Upstream: https://github.com/end2endzone/NonBlockingRTTTL
The installed library's source is vendored as ASCII JSON for offline tests.
Decode the "source" field in src/NonBlockingRtttl.cpp.json to recover the
exact reviewed upstream CPP, including its degree symbol. The header is
unchanged. LICENSE and AUTHORS retain the original MIT attribution.
No firmware code is linked from this directory.

Canonical source normalization for hash comparison is LF line endings.
The fixture file uses a JSON escape for the single U+00B0 degree symbol in
the disabled diagnostic string "Loop n". The production private-build patch
removes that diagnostic-only symbol to make generated copies ASCII as well.
No executable statement in the unpatched fixture is changed.

Original installed CPP SHA256:
d4fddfa663c8adeacea3b1daf56ec5fbe63ecaaf68e5477ac96dc4fb2e28d363
Patched private CPP SHA256:
e7710c4e3309b25517f5bace697c0c2574a5ce219f3cb156d352f739c178d75f
Unchanged header SHA256:
5a33eec77e2ba53ed9b83a8242633b8ae11873d3b4f8cfd257abbb2eb61b6220
LICENSE SHA256:
44bbc2541c595d5d8790f9b0be9a56fc88c9a500ab6bd2a88b4e00f8e28f0585
Original AUTHORS SHA256 (trailing whitespace trimmed in copy):
5ee7d6fd1d7730b5d9184accadc45ec5ee67e57bca50ed1729d0043acf8ff303
