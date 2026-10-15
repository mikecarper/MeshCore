#!/usr/bin/env python3
"""Summarize only the two explicitly captured isolated-namespace TCP snapshots."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

FIELDS = ("ActiveOpens", "PassiveOpens", "AttemptFails", "EstabResets", "CurrEstab",
          "InSegs", "OutSegs", "RetransSegs", "InErrs", "OutRsts", "InCsumErrors")


def parse_tcp(text):
    lines = [line.split() for line in text.splitlines() if line.startswith("Tcp:")]
    if len(lines) != 2 or len(lines[0]) != len(lines[1]) or len(set(lines[0])) != len(lines[0]):
        raise ValueError("missing or ambiguous TCP header/value pair")
    values = dict(zip(lines[0][1:], map(int, lines[1][1:])))
    if any(values.get(field, -1) < 0 for field in ("InSegs", "OutSegs", "RetransSegs")):
        raise ValueError("required TCP counters are missing or negative")
    return {field: values[field] for field in FIELDS if field in values}


def summarize(before, after):
    delta = {field: after[field] - before[field] for field in before.keys() & after.keys()
             if field != "CurrEstab"}
    if any(value < 0 for value in delta.values()):
        raise ValueError("TCP counters decreased; snapshots are not comparable")
    return dict(available=True, scope="isolated_network_namespace", before=before, after=after,
                delta=delta, retransmission_scope="total only: includes SSH handshake, commands, data and close; no data-only attribution",
                out_segment_retransmission_ratio=(delta["RetransSegs"] / delta["OutSegs"]
                                                 if delta["OutSegs"] else None))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args(argv)
    folder = args.directory
    if not folder.is_absolute() or not folder.is_dir():
        parser.error("an existing absolute evidence directory is required")
    try:
        result = summarize(*(parse_tcp((folder / f"tcp-{phase}.snmp").read_text(encoding="ascii"))
                             for phase in ("before", "after")))
        result["captured_utc"] = {phase: (folder / f"tcp-{phase}.utc").read_text(encoding="ascii").strip()
                                  for phase in ("before", "after")}
    except (OSError, ValueError) as error:
        result = dict(available=False, scope="isolated_network_namespace", error=str(error))
    (folder / "tcp-stats.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
