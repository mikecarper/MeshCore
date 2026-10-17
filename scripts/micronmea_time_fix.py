"""Fix reviewed MicroNMEA UTC parsing in private build copies, not libdeps.

MicroNMEA 2.0.6 skips nine characters for every time field, rejecting valid
integer seconds and one-digit fractions. Its fixed-width date/time reads can
also pass field boundaries. Scan and validate fields before numeric reads;
keep framing, raw sentences, position validity and the public ABI unchanged.
The provider still owns calendar validation and fresh-observation admission.
"""
import hashlib
from pathlib import Path


SOURCE_SHA256 = "925da47a9aaa75c43c786ffb5a6530544283dda5138cd7792c4e8f79c8238377"
HEADER_SHA256 = "d87810b25ff920fe5810b8bf7be17c69eb608853407be9da82214ef5616f1fb2"
PATCHED_SHA256 = "df2e660e79235e317ec00b86ec86883655a70a60a38b11ac2eb60189036fc9e0"

ORIGINAL_TIME = """const char* MicroNMEA::parseTime(const char* s)
{
\tif (*s == ',')
\t\treturn skipField(s);
\t_hour = parseUnsignedInt(s, 2);
\t_minute = parseUnsignedInt(s + 2, 2);
\t_second = parseUnsignedInt(s + 4, 2);
\t_hundredths = parseUnsignedInt(s + 7, 2);
\treturn skipField(s + 9);
}"""

PATCHED_TIME = """const char* MicroNMEA::parseTime(const char* s)
{
\t_hour = _minute = _second = 99;
\t_hundredths = 0;
\tconst char* end = s;
\twhile (*end != ',' && !isEndOfFields(*end))
\t\t++end;
\tif (end - s < 6)
\t\treturn skipField(end);
\tfor (const char* digit = s; digit < s + 6; ++digit)
\t\tif (*digit < '0' || *digit > '9')
\t\t\treturn skipField(end);

\tuint8_t hundredths = 0;
\tif (end - s > 6) {
\t\tif (s[6] != '.' || end - s < 8)
\t\t\treturn skipField(end);
\t\tfor (const char* digit = s + 7; digit < end; ++digit) {
\t\t\tif (*digit < '0' || *digit > '9')
\t\t\t\treturn skipField(end);
\t\t\tif (digit == s + 7)
\t\t\t\thundredths = (*digit - '0') * 10;
\t\t\telse if (digit == s + 8)
\t\t\t\thundredths += *digit - '0';
\t\t}
\t}
\t_hour = parseUnsignedInt(s, 2);
\t_minute = parseUnsignedInt(s + 2, 2);
\t_second = parseUnsignedInt(s + 4, 2);
\t_hundredths = hundredths;
\treturn skipField(end);
}"""

ORIGINAL_DATE = """const char* MicroNMEA::parseDate(const char* s)
{
\tif (*s == ',')
\t\treturn skipField(s);
\t_day = parseUnsignedInt(s, 2);
\t_month = parseUnsignedInt(s + 2, 2);
\t_year = parseUnsignedInt(s + 4, 2) + 2000;
\treturn skipField(s + 6);
}"""

PATCHED_DATE = """const char* MicroNMEA::parseDate(const char* s)
{
\t_year = _month = _day = 0;
\tconst char* end = s;
\twhile (*end != ',' && !isEndOfFields(*end))
\t\t++end;
\tif (end - s != 6)
\t\treturn skipField(end);
\tfor (const char* digit = s; digit < end; ++digit)
\t\tif (*digit < '0' || *digit > '9')
\t\t\treturn skipField(end);
\t_day = parseUnsignedInt(s, 2);
\t_month = parseUnsignedInt(s + 2, 2);
\t_year = parseUnsignedInt(s + 4, 2) + 2000;
\treturn skipField(end);
}"""

PATCH_NOTICE = (
    "// MeshCore local changes (2026-10-17): bounded UTC fields and variable-width\n"
    "// time fractions. Original MicroNMEA 2.0.6 by Steve Marple, GNU LGPL v2.1.\n"
)


def digest(source):
    return hashlib.sha256(source.encode("ascii")).hexdigest()


def patched_source(source):
    source = source.replace("\r\n", "\n")
    source_hash = digest(source)
    if source_hash == PATCHED_SHA256:
        return source
    if source_hash != SOURCE_SHA256:
        raise RuntimeError("MicroNMEA UTC fix: changed reviewed source; review dependency update")
    if (source.count(ORIGINAL_TIME) != 1 or source.count(ORIGINAL_DATE) != 1
            or source.count("#include <MicroNMEA.h>") != 1):
        raise RuntimeError("MicroNMEA UTC fix: changed reviewed field parsers")
    source = PATCH_NOTICE + source.replace("#include <MicroNMEA.h>", '#include "MicroNMEA.h"', 1)
    source = source.replace(ORIGINAL_TIME, PATCHED_TIME, 1)
    source = source.replace(ORIGINAL_DATE, PATCHED_DATE, 1)
    if digest(source) != PATCHED_SHA256:
        raise RuntimeError("MicroNMEA UTC fix: unexpected transform result")
    return source


def write_if_changed(path, content):
    if not path.exists() or path.read_text(encoding="ascii") != content:
        path.write_text(content, encoding="ascii")


def replace_micronmea_source(build_env, node):
    source = Path(node.srcnode().get_abspath())
    if source.name != "MicroNMEA.cpp" or source.parent.name != "src":
        return node
    original = source.read_text(encoding="ascii")
    patched = patched_source(original)
    try:
        header = source.with_suffix(".h").read_text(encoding="ascii").replace("\r\n", "\n")
    except (OSError, UnicodeError) as error:
        raise RuntimeError("MicroNMEA UTC fix: missing or changed reviewed header") from error
    if digest(header) != HEADER_SHA256:
        raise RuntimeError("MicroNMEA UTC fix: changed reviewed header; review dependency update")
    destination = Path(build_env.subst("$BUILD_DIR")) / "patched-micronmea" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A quoted include binds the source to its reviewed header even if a mock
    # or unrelated library has placed another MicroNMEA.h earlier in CPPPATH.
    write_if_changed(destination.with_suffix(".h"), header)
    write_if_changed(destination, patched)
    return build_env.File(str(destination))


def install(build_env):
    build_env.AddBuildMiddleware(replace_micronmea_source, "*MicroNMEA.cpp")


if "Import" in globals():
    Import("env")
    install(env)
