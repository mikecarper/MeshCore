#!/usr/bin/env python3
"""One guarded-orchestrator case: real Flutter SSH through two isolated TAPs.

Invoked by ethernet_preamble_bench.py, not directly with firmware settings.
JSON configuration path: LORA_SSH_BENCH_CONFIG. JSON request: stdin; result:
stdout only. Keys are copied as files, never read/printed here, and removed in
finally. Only uniquely named helper resources are stopped; host networking,
system sshd, accounts and firmware are never configured by this runner.
"""
from __future__ import annotations

import ipaddress
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import queue
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlparse

from ethernet_tcp_stats import parse_tcp, summarize

ARTIFACTS = ("speed.json", "tcp-stats.json", "tcp-before.snmp", "tcp-after.snmp",
             "tcp-before.utc", "tcp-after.utc", "tcp-ss.txt", "relay.json",
             "result.txt", "routes.txt", "flutter.txt")


def posix_path(value):
    if not isinstance(value, str) or not value.startswith("/") or value == "/" \
            or ".." in PurePosixPath(value).parts or any(ord(c) < 32 for c in value):
        raise ValueError("An explicit absolute non-root Linux path is required")
    return str(PurePosixPath(value))


def wsl_path(value):
    value = str(value)
    path = PureWindowsPath(value)
    if re.fullmatch(r"[A-Za-z]:", path.drive) and path.is_absolute():
        return "/mnt/" + path.drive[0].lower() + "/" + "/".join(path.parts[1:])
    return posix_path(value)  # Supports running host-only tests under WSL too.


def endpoint(value):
    parsed = urlparse(value)
    host = ipaddress.IPv4Address(parsed.hostname)
    private = any(host in ipaddress.IPv4Network(network) for network in
                  ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
    if parsed.scheme != "tcp" or parsed.port != 5003 or not private \
            or parsed.username or parsed.password or parsed.path not in ("", "/") \
            or parsed.query or parsed.fragment:
        raise ValueError("Use an explicit private numeric IPv4 TCP port-5003 endpoint")
    return value


def validate_config(value):
    required = ("ssh_host", "ssh_key", "pi_bench", "server_endpoint", "client_endpoint",
                "wsl_workspace", "flutter", "account", "out_root")
    if not isinstance(value, dict) or any(not isinstance(value.get(key), str) for key in required):
        raise ValueError("Required benchmark configuration fields are missing")
    config = {key: value[key] for key in required}
    if not re.fullmatch(r"(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9_][A-Za-z0-9_.-]*", config["ssh_host"]):
        raise ValueError("Invalid SSH host")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", config["account"]) or config["account"] == "root":
        raise ValueError("An unprivileged WSL test account is required")
    for key in ("pi_bench", "wsl_workspace", "flutter"):
        config[key] = posix_path(config[key])
    for key in ("ssh_key", "out_root"):
        path = Path(config[key])
        if not path.is_absolute() or any(ord(c) < 32 for c in config[key]):
            raise ValueError("Explicit absolute local paths are required")
        if key == "ssh_key" and not path.is_file():
            raise ValueError("The selected local SSH key file is unavailable")
        config[key] = str(path)
    endpoint(config["server_endpoint"]); endpoint(config["client_endpoint"])
    if config["server_endpoint"] == config["client_endpoint"]:
        raise ValueError("Two distinct radios are required")
    return config


def validate_request(value):
    if not isinstance(value, dict) or type(value.get("index")) is not int or not 1 <= value["index"] <= 120 \
            or type(value.get("preamble")) is not int or not 32 <= value["preamble"] <= 2048:
        raise ValueError("Invalid preamble case")
    if type(value.get("frequency_mhz")) is not float or value["frequency_mhz"] != 909.5 \
            or type(value.get("bandwidth_khz")) is not int or value["bandwidth_khz"] not in (125, 500) \
            or type(value.get("sf")) is not int or not 5 <= value["sf"] <= 9 \
            or type(value.get("cr")) is not int or value["cr"] != 5:
        raise ValueError("Only the orchestrator's 909.5/125-or-500/SF5-SF9/CR5 cases are permitted")
    expected = f"case-{value['index']:03d}-p{value['preamble']}"
    if value.get("case_id") != expected:
        raise ValueError("Invalid case identifier")
    return value


def ssh_args(config):
    return ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=8", "-i", config["ssh_key"], "--", config["ssh_host"]]


def remote_args(config, command):
    return ssh_args(config) + [shlex.join(command)]


def artifact_dir(text, parent, stem):
    matches = re.findall(r"^" + re.escape(stem) + r": (.+)$", text, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError("Missing or ambiguous helper artifact directory")
    path = PurePosixPath(posix_path(matches[0].strip()))
    expected = "ethernet-phone" if stem == "Artifacts" else "ethernet-flutter"
    if path.parent != PurePosixPath(parent) or not re.fullmatch(expected + r"\.[A-Za-z0-9]{8}", path.name):
        raise ValueError("Helper artifacts escaped the explicitly selected bench directory")
    return str(path)


def parse_server_ready(text, config):
    if sum(line.startswith("READY: ") for line in text.splitlines()) != 1 \
            or f"READY: gateway={config['server_endpoint']} local=10.90.5.1 remote=10.90.5.2 SSH=2222 user=mikec" not in text.splitlines():
        raise ValueError("Server helper did not confirm the exact isolated endpoint")
    fingerprints = re.findall(r"\bSHA256:[A-Za-z0-9+/]{43}(?=\s|$)", text)
    pids = re.findall(r"^Server helper PID: ([1-9][0-9]{0,9})$", text, re.MULTILINE)
    if len(fingerprints) != 1 or len(pids) != 1 or int(pids[0]) <= 1:
        raise ValueError("Missing or ambiguous server fingerprint/PID")
    return dict(pid=int(pids[0]), fingerprint=fingerprints[0],
                directory=artifact_dir(text, config["pi_bench"], "Artifacts"))


def read_ready(process, config, timeout):
    lines = queue.Queue(maxsize=128)

    def read():
        try:
            while True:
                line = process.stdout.readline(16385)
                if len(line) > 16384:
                    raise ValueError("Oversized helper startup line")
                lines.put_nowait(line)
                if not line:
                    break
        except Exception:
            try:
                lines.put_nowait(None)
            except queue.Full:
                pass

    threading.Thread(target=read, daemon=True).start()
    observed = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            line = lines.get(timeout=max(.001, deadline - time.monotonic()))
        except queue.Empty:
            break
        if not line:
            raise RuntimeError("Server helper ended before readiness")
        observed += line
        if len(observed) > 65536:
            raise RuntimeError("Oversized helper startup output")
        pids = re.findall(r"^Server helper PID: ([1-9][0-9]{0,9})$", observed, re.MULTILINE)
        if len(pids) == 1 and int(pids[0]) > 1:
            # Retain identity for finally even if another readiness field fails.
            process.helper_pid = int(pids[0])
        if "Artifacts: " in observed and "Server helper PID: " in observed:
            return parse_server_ready(observed, config)
    raise TimeoutError("Server helper readiness deadline exceeded")


def normalized_speed(speed, helper_exit_code):
    def finite(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    directions = {}
    for source, target in (("download", "downlink"), ("upload", "uplink")):
        item = speed.get(source)
        normalized = None
        if isinstance(item, dict) and type(item.get("bytes")) is int and item["bytes"] > 0 \
                and finite(item.get("elapsed_ms")) and item["elapsed_ms"] > 0:
            normalized = dict(bytes=item["bytes"], elapsed_ms=item["elapsed_ms"],
                elapsed_seconds=item["elapsed_ms"] / 1000,
                kbps=item["bytes"] * 8 / item["elapsed_ms"], hash_verified=item.get("sha256_valid") is True)
            for key in ("data_elapsed_ms", "data_kbps", "first_byte_ms", "ready_ms", "command_elapsed_ms"):
                if key in item:
                    normalized[key] = item[key]
        directions[target] = normalized
    verified = (helper_exit_code == 0 and speed.get("success") is True and speed.get("host_verified") is True
                and all(value and value["hash_verified"] for value in directions.values()))
    return dict(verified=bool(verified), handshake_ms=speed.get("handshake_ms"),
                total_ms=speed.get("total_elapsed_ms"), **directions)


def stop_server_args(config, pid, prefix):
    if type(pid) is not int or pid <= 1 or not re.fullmatch(r"ethphone-speed-[a-f0-9]{8}", prefix):
        raise ValueError("Invalid owned helper identity")
    # Match whole argv tokens, including the exact generated prefix. A recycled
    # PID or unrelated helper can never satisfy this check by substring alone.
    program = """import os,signal,sys
pid=int(sys.argv[1]); script=sys.argv[2]; prefix=sys.argv[3]
try:
    args=open('/proc/%d/cmdline'%pid,'rb').read().decode().strip('\\0').split('\\0')
except FileNotFoundError:
    raise SystemExit(0)
if script not in args or '--prefix' not in args or args[args.index('--prefix')+1] != prefix:
    raise SystemExit(3)
os.kill(pid,signal.SIGTERM)
"""
    return remote_args(config, ["sudo", "-n", "python3", "-c", program, str(pid),
                               config["pi_bench"] + "/ethernet_ssh_phone_bench.sh", prefix])


def execute(config, request):
    config = validate_config(config); request = validate_request(request)
    root = Path(config["out_root"]); root.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix=request["case_id"] + "-", dir=root))
    token = secrets.token_hex(4)
    server_prefix, client_prefix = "ethphone-speed-" + token, "ethflutter-speed-" + token
    windows_key = folder / "client_key.ephemeral"
    wsl_key = config["wsl_workspace"] + "/.key-" + client_prefix
    server = ready = None
    wsl_key_owned = False
    result = dict(verified=False, tcp_client=None, tcp_server=None,
                  artifacts=dict(directory=str(folder)), case_id=request["case_id"])
    deadline = time.monotonic() + 225

    def run(argv, timeout=10, check=True):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Case deadline exceeded")
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
            timeout=min(timeout, remaining), shell=False, check=check)

    def server_tcp(phase):
        try:
            text = run(remote_args(config, ["sudo", "-n", "ip", "netns", "exec", server_prefix,
                "cat", "/proc/net/snmp"]), timeout=5).stdout
            (folder / ("server-tcp-" + phase + ".snmp")).write_text(text, encoding="ascii")
            return parse_tcp(text)
        except Exception:
            return None

    before = None
    try:
        command = ["sudo", "-n", "bash", config["pi_bench"] + "/ethernet_ssh_phone_bench.sh",
            "--endpoint", config["server_endpoint"], "--bench-dir", config["pi_bench"],
            "--prefix", server_prefix, "--duration", "300"]
        server = subprocess.Popen(remote_args(config, command), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            encoding="utf-8", errors="replace", shell=False)
        ready = read_ready(server, config, timeout=20)
        result["artifacts"]["server_directory"] = ready["directory"]
        scp = ["scp", "-q", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", "ConnectTimeout=8", "-i", config["ssh_key"], "--",
               config["ssh_host"] + ":" + ready["directory"] + "/client_key", str(windows_key)]
        run(scp, timeout=10)
        # noclobber opens the new private file atomically. A stale/colliding
        # name is refused, never overwritten or removed by our cleanup.
        prepare = ('set -euC; umask 077; exec 3>"$2"; cat -- "$1" >&3; '
                   'chmod 600 -- "$2"; chown "$3": -- "$2"')
        run(["wsl", "--user", "root", "--exec", "bash", "-c", prepare, "--",
             wsl_path(windows_key), wsl_key, config["account"]], timeout=10)
        wsl_key_owned = True
        before = server_tcp("before")
        helper = wsl_path(Path(__file__).resolve().with_name("ethernet_ssh_flutter_bench.sh"))
        client = run(["wsl", "--user", "root", "--exec", "bash", helper,
            "--endpoint", config["client_endpoint"], "--bench-dir", config["wsl_workspace"],
            "--workspace", config["wsl_workspace"], "--flutter", config["flutter"],
            "--key", wsl_key, "--fingerprint", ready["fingerprint"], "--prefix", client_prefix,
            "--account", config["account"], "--test-name", "speed", "--phase-timeout", "45"],
            timeout=220, check=False)
        (folder / "client-helper.txt").write_text(client.stdout, encoding="utf-8")
        client_folder = artifact_dir(client.stdout, config["wsl_workspace"], "Flutter RF artifacts")
        result["artifacts"]["client_directory"] = client_folder
        # The allowlist contains evidence only, never arbitrary remote files or
        # the ephemeral client key. Missing evidence remains explicitly absent.
        copy = 'set -eu; src=$1; dst=$2; shift 2; for name do if [ -f "$src/$name" ]; then cp -- "$src/$name" "$dst/$name"; fi; done'
        run(["wsl", "--user", "root", "--exec", "bash", "-c", copy, "--",
             client_folder, wsl_path(folder), *ARTIFACTS], timeout=10)
        speed = json.loads((folder / "speed.json").read_text(encoding="utf-8"))
        if not isinstance(speed, dict):
            raise ValueError("Invalid speed evidence")
        result.update(normalized_speed(speed, client.returncode))
        if not result["verified"]:
            result["error"] = "SSH payload run did not complete with both hashes verified"
        try:
            result["tcp_client"] = json.loads((folder / "tcp-stats.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            result["tcp_client"] = dict(available=False, scope="isolated_network_namespace")
    except Exception as error:
        result["verified"] = False
        result["error"] = ("SSH runner deadline exceeded" if isinstance(error, (TimeoutError, subprocess.TimeoutExpired))
                           else "SSH runner failed; inspect the bounded non-secret evidence")
    finally:
        if ready:
            after = server_tcp("after")
            if before is not None and after is not None:
                try:
                    result["tcp_server"] = summarize(before, after)
                except ValueError:
                    pass
            if result["tcp_server"] is None:
                result["tcp_server"] = dict(available=False, scope="isolated_network_namespace")
        helper_pid = ready["pid"] if ready else getattr(server, "helper_pid", None)
        if type(helper_pid) is int:
            # Cleanup gets a separate bounded budget even after case timeout.
            try:
                subprocess.run(stop_server_args(config, helper_pid, server_prefix),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=5, shell=False, check=True)
            except Exception:
                result["verified"] = False
                result["cleanup_error"] = "Owned server stop could not be confirmed; helper remains duration-bounded"
        if wsl_key_owned:
            try:
                subprocess.run(["wsl", "--user", "root", "--exec", "rm", "-f", "--", wsl_key],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=5, shell=False, check=True)
            except Exception:
                result["verified"] = False
                result["cleanup_error"] = "Ephemeral WSL key cleanup could not be confirmed"
        try:
            if windows_key.exists():
                windows_key.unlink()
        except OSError:
            result["verified"] = False
            result["cleanup_error"] = "Ephemeral local key cleanup could not be confirmed"
        if server is not None:
            try:
                server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server.kill()  # Only the exact local Popen child, no PID search.
                server.wait(timeout=2)
            if server.stdout:
                server.stdout.close()
        (folder / "runner-result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    try:
        config_path = Path(os.environ.get("LORA_SSH_BENCH_CONFIG", ""))
        if not config_path.is_absolute() or not config_path.is_file() or config_path.stat().st_size > 65536:
            raise ValueError("An explicit local configuration file is required")
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        request_text = sys.stdin.read(4097)
        if len(request_text) > 4096:
            raise ValueError("Oversized case request")
        result = execute(config, json.loads(request_text))
    except Exception:
        result = dict(verified=False, error="Invalid benchmark configuration/request or unavailable runner resources")
    print(json.dumps(result), flush=True)
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
