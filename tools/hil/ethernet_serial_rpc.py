#!/usr/bin/env python3
"""Private SSH-stdio transport for one explicitly selected lab USB CLI port.

Stdout is exclusively bounded JSON-lines. The SSH connection owns the serial
session; EOF or a malformed/incomplete command closes it, without rebooting.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import sys

MAX_LINE = 65536
MAX_COMMAND = 512
PROTOCOL = "meshcore-serial-rpc-1"


def validate_command(command, timeout):
    if not isinstance(command, str) or not command or len(command) > MAX_COMMAND \
            or any(character in command for character in "\r\n\0"):
        raise ValueError("a bounded single-line command is required")
    command.encode("ascii")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) \
            or not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError("command timeout must be finite and in (0,30]")


def encode_message(message):
    data = (json.dumps(message, ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
    if len(data) > MAX_LINE:
        raise ValueError("RPC message exceeds the line limit")
    return data


def decode_message(line):
    if not line or len(line) > MAX_LINE or not line.endswith(b"\n"):
        raise ValueError("RPC requires bounded, complete JSON lines")
    message = json.loads(line.decode("utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    if not isinstance(message, dict):
        raise ValueError("RPC message must be an object")
    return message


def serve(node, source, destination):
    def send(message):
        destination.write(encode_message(message))
        destination.flush()

    try:
        send(dict(type="ready", protocol=PROTOCOL, identity=node.identity,
                  hello=getattr(node, "hello", "")))
        while True:
            line = source.readline(MAX_LINE + 1)
            if not line:
                return 0
            request_id = None
            try:
                request = decode_message(line)
                if set(request) != {"id", "command", "timeout"}:
                    raise ValueError("unexpected RPC request fields")
                request_id = request["id"]
                if type(request_id) is not int or request_id <= 0:
                    raise ValueError("positive integer request id required")
                validate_command(request["command"], request["timeout"])
                # No incidental diagnostic output may corrupt the protocol.
                with contextlib.redirect_stdout(sys.stderr):
                    reply = node.command(request["command"], timeout=request["timeout"])
                if not isinstance(reply, str):
                    raise ValueError("serial command did not return a text reply")
                send(dict(id=request_id, completed=True, reply=reply))
            except Exception as error:
                send(dict(id=request_id, completed=False, error=str(error)[:1024]))
                return 1  # Never attribute a late serial reply to another command.
    finally:
        with contextlib.redirect_stdout(sys.stderr):
            node.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    args = parser.parse_args(argv)
    try:
        from ethernet_primary_probe import SerialNode
        with contextlib.redirect_stdout(sys.stderr):
            node = SerialNode(args.port)
        return serve(node, sys.stdin.buffer, sys.stdout.buffer)
    except Exception as error:
        print("Serial RPC stopped: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
