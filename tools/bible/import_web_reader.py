#!/usr/bin/env python3
"""Import the source for the offline Reader from eBible.org's engwebp VPL download."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

from pack_reader import SOURCE, pack, references

SOURCE_URL = "https://ebible.org/Scriptures/engwebp_vpl.zip"
MEMBER = "engwebp_vpl.txt"


def import_archive(path):
    archive_bytes = path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        source = archive.read(MEMBER)
        source_date = archive.getinfo(MEMBER).date_time[:3]
    verses = {}
    for line in source.decode("utf-8-sig").splitlines():
        if not line.startswith("JOH "):
            continue
        match = re.fullmatch(r"JOH ([0-9]+:[0-9]+) (.+)", line)
        if not match or match[1] in verses:
            raise ValueError("invalid or duplicate Reader verse in source")
        verses[match[1]] = match[2]
    if set(verses) != set(references()):
        raise ValueError("the source must contain exactly the 879 Reader verses")
    document = {
        "translation": "WEB",
        "edition": "World English Bible, American English, Protestant Edition (engwebp)",
        "attribution": "World English Bible - public domain (eBible.org).",
        "source_url": SOURCE_URL,
        "source_member": MEMBER,
        "source_date": "%04d-%02d-%02d" % source_date,
        "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "source_member_sha256": hashlib.sha256(source).hexdigest(),
        "license_url": "https://ebible.org/engwebp/copyright.htm",
        "verses": {key: verses[key] for key in references()},
    }
    pack(document)  # Validate ASCII firmware conversion; retain exact UTF-8 source.
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help=f"downloaded {SOURCE_URL}")
    parser.add_argument("--output", type=Path, default=SOURCE)
    args = parser.parse_args()
    document = import_archive(args.archive)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8", newline="\n")
    print(f"Imported {len(document['verses'])} Reader verses; source {document['source_date']}")


if __name__ == "__main__":
    main()
