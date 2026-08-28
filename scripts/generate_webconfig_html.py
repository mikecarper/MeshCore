#!/usr/bin/env python
#
# Pre-build script: gzip webui/index.html into a PROGMEM C header so the
# webconfig portal can serve the page straight from flash with
# Content-Encoding: gzip. The generated header is tracked so ESP32 targets
# that replace extra_scripts still compile from a clean checkout; this script
# refreshes it whenever the source page (or this script) is newer.
#
# Comments and indentation are stripped before compressing (see strip_source).
# The source page is heavily commented by house style and none of it is worth
# flash, so the page ships smaller than it reads.
#
# Output: src/helpers/esp32/WebConfigHtml.h
#   WEBCONFIG_HTML_LOADER[] - small bootstrap page (PROGMEM)
#   WEBCONFIG_HTML_GZ[]     - complete gzipped page (PROGMEM)
#   WEBCONFIG_HTML_ETAG     - quoted strong ETag for the loader page
#
# Runnable outside SCons to inspect exactly what gets shipped:
#   python3 scripts/generate_webconfig_html.py --emit /tmp/shipped.html
# or served directly by the mock backend with its --minify flag.

import gzip
import hashlib
import os
import sys

try:
    Import("env")  # noqa: F821
except NameError:
    pass           # running standalone (--emit), not as a PIO extra_script

SOURCE = os.path.join("webui", "index.html")
OUTPUT = os.path.join("src", "helpers", "esp32", "WebConfigHtml.h")
# __file__ is not defined inside PIO/SCons-executed extra_scripts
SCRIPT = os.path.join("scripts", "generate_webconfig_html.py")
MINIFIER = os.path.join("scripts", "webconfig_minify.py")
HASH_MARKER = "// build-inputs-sha256: "

sys.path.insert(0, os.path.join(os.getcwd(), "scripts"))
from webconfig_minify import check_stripped, strip_source  # noqa: E402


def status(msg):
    sys.stderr.write("WebConfig HTML: %s\n" % msg)


def content_hash():
    # Hash the source page, this generator and the minifier so any change to
    # any of them forces a regenerate, independent of file timestamps.
    h = hashlib.sha256()
    with open(SOURCE, "rb") as f:
        h.update(f.read())
    for path in (SCRIPT, MINIFIER):
        if os.path.isfile(path):
            h.update(b"\0")
            with open(path, "rb") as f:
                h.update(f.read())
    return h.hexdigest()


def stored_hash():
    try:
        with open(OUTPUT, "r") as f:
            for line in f:
                if line.startswith(HASH_MARKER):
                    return line[len(HASH_MARKER):].strip()
                if line.startswith("#"):  # reached the C preprocessor lines
                    break
    except OSError:
        return None
    return None


def shipped_page():
    """The exact bytes the device serves: the source page, stripped."""
    with open(SOURCE, "r", encoding="utf-8") as f:
        raw = f.read()
    stripped = strip_source(raw)
    problem = check_stripped(raw, stripped)
    if problem:
        status("ERROR: comment stripping corrupted the page (%s)" % problem)
        sys.exit(2)
    return raw, stripped


def append_byte_array(lines, name, data):
    lines.append("const uint8_t %s[] PROGMEM = {" % name)
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        lines.append("  " + "".join("0x%02x," % b for b in chunk))
    lines.append("};")


def main():
    if not os.path.isfile(SOURCE):
        status("ERROR: %s not found" % SOURCE)
        sys.exit(2)

    if "--emit" in sys.argv:
        dest = sys.argv[sys.argv.index("--emit") + 1]
        src, stripped = shipped_page()
        with open(dest, "w", encoding="utf-8") as f:
            f.write(stripped)
        status("%s -> %s (%d -> %d bytes)" % (SOURCE, dest, len(src), len(stripped)))
        return

    src_hash = content_hash()
    if os.path.isfile(OUTPUT) and stored_hash() == src_hash:
        return

    src, stripped = shipped_page()
    raw = stripped.encode("utf-8")

    # mtime=0 keeps the gzip output and its immutable URL deterministic.
    gz = gzip.compress(raw, compresslevel=9, mtime=0)
    version = hashlib.sha256(gz).hexdigest()[:16]
    loader = ("<!doctype html><meta charset=utf-8><meta name=viewport "
              "content=\"width=device-width,initial-scale=1\"><title>MeshCore "
              "WebConfig</title><body>Loading MeshCore WebConfig&hellip;<script>"
              "(async()=>{let p;for(let n=0;n<3&&p===undefined;n++){const C="
              "window.AbortController,c=C&&new C(),t=c&&setTimeout(()=>c.abort()"
              ",90000);try{const r=await fetch(\"/ui?v=%s\",c?{signal:c.signal}"
              ":{});if(r.ok)p=await r.text();else if(r.status===409)return "
              "location.reload()}catch(e){}finally{if(t)clearTimeout(t)}if(p==="
              "undefined)await new Promise(r=>setTimeout(r,500*(n+1)))}if(p==="
              "undefined)throw Error(\"transfer failed\");document.open();"
              "document.write(p);document.close()})().catch(e=>"
              "document.body.textContent=\"Unable to load WebConfig: \"+e+"
              "\". Reload to retry.\")</script>" % version)
    loader_bytes = loader.encode("utf-8")
    etag = hashlib.sha256(loader_bytes).hexdigest()[:16]

    lines = []
    lines.append("// Auto-generated by scripts/generate_webconfig_html.py from %s" % SOURCE.replace(os.sep, "/"))
    lines.append("// DO NOT EDIT - edit webui/index.html instead.")
    lines.append("%s%s" % (HASH_MARKER, src_hash))
    lines.append("#pragma once")
    lines.append("#include <stdint.h>")
    lines.append("#include <pgmspace.h>")
    lines.append("")
    lines.append("const uint32_t WEBCONFIG_HTML_RAW_LEN = %d;" % len(raw))
    lines.append("const uint32_t WEBCONFIG_HTML_GZ_LEN = %d;" % len(gz))
    lines.append('const char WEBCONFIG_HTML_ETAG[] = "\\"%s\\"";' % etag)
    lines.append('const char WEBCONFIG_HTML_VERSION[] = "%s";' % version)
    lines.append("const uint16_t WEBCONFIG_HTML_LOADER_LEN = %d;" % len(loader_bytes))
    append_byte_array(lines, "WEBCONFIG_HTML_LOADER", loader_bytes)
    lines.append("")
    append_byte_array(lines, "WEBCONFIG_HTML_GZ", gz)
    lines.append("")

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w") as f:
        f.write("\n".join(lines))

    status("%s -> %s (%d bytes source, %d stripped, %d gzip)"
           % (SOURCE, OUTPUT, len(src.encode("utf-8")), len(raw), len(gz)))


# This script is attached only to esp32_base. Generate unconditionally here:
# CPPDEFINES is not fully resolved when PlatformIO loads pre-scripts, so trying
# to detect the final role at this point can leave a stale embedded page.
# Non-WebConfig ESP32 builds do not compile the generated data into firmware.
main()
