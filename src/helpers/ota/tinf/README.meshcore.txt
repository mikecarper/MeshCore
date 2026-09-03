tinf 1.2.1 raw-DEFLATE decoder
================================

Upstream: https://github.com/jibsen/tinf
Revision: 57ffa1f1d5e3dde19011b2127bd26d01689b694b

Vendored files:
  LICENSE
  tinf.h
  tinflate.c

MeshCore changes are marked in tinf.h and tinflate.c. They add
tinf_uncompress_exact(), which accepts legal padding bits in the byte containing
the final end-of-block code but rejects any trailing whole input byte. The C
implementation is included by ../OtaTinf.c only for receive-only transport
inflate builds (and the native OTA test profile). ../OtaDeflate.cpp is the
small C++ callback adapter. Keeping the decoder in a true C translation unit
avoids the several-kilobyte penalty produced by the embedded C++ toolchains;
other firmware links no decoder code or persistent RAM.

Only the raw RFC 1951 decoder is integrated. The zlib/gzip wrappers and checksum
sources are intentionally omitted. The original zlib license is preserved in
LICENSE and in the source headers.
