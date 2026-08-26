#!/usr/bin/env python3
"""Verify promised firmware capabilities and emit a machine-readable manifest."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--target", required=True)
    parser.add_argument("--artifact-target")
    parser.add_argument("--platformio-env")
    parser.add_argument("--platform", required=True)
    parser.add_argument("--build-profile", required=True)
    parser.add_argument("--capability", action="append", default=[])
    parser.add_argument("--reduction", action="append", default=[])
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="CAPABILITY=TEXT",
        help="Require TEXT to be present in the linked image.",
    )
    return parser.parse_args()


def stable_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def main() -> int:
    args = parse_args()
    try:
        image = args.image.read_bytes()
    except OSError as exc:
        print(f"capability check: cannot read {args.image}: {exc}", file=sys.stderr)
        return 2

    checks = []
    malformed = False
    for expectation in args.expect:
        if "=" not in expectation:
            print(
                f"capability check: malformed --expect {expectation!r}",
                file=sys.stderr,
            )
            malformed = True
            continue
        capability, needle = expectation.split("=", 1)
        present = bool(needle) and needle.encode("utf-8") in image
        checks.append(
            {
                "capability": capability,
                "evidence": needle,
                "present": present,
            }
        )

    capabilities = stable_unique(
        args.capability + [check["capability"] for check in checks]
    )
    missing = [check for check in checks if not check["present"]]
    manifest = {
        "schema_version": 1,
        "target": args.target,
        "artifact_target": args.artifact_target or args.target,
        "platformio_env": args.platformio_env or args.target,
        "platform": args.platform,
        "build_profile": args.build_profile,
        "capabilities": capabilities,
        "reductions": stable_unique(args.reduction),
        "verification": checks,
        "verified": not malformed and not missing,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    temporary.replace(args.output)

    if malformed:
        return 2
    if missing:
        for check in missing:
            print(
                "capability check failed: "
                f"{check['capability']} promised but {check['evidence']!r} "
                f"is absent from {args.image}",
                file=sys.stderr,
            )
        return 1

    print(
        f"Verified {len(checks)} capability marker(s); manifest: {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
