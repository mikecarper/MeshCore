#!/usr/bin/env python3
"""Run disjoint release matrix workers with independent PlatformIO state."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time


MINIMUM_FREE_BYTES = 20 * 1024 ** 3


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _git(root: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=root, text=True).strip()


def _validate_source(root: Path, source: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("invalid parallel release source")
    if _git(root, "rev-parse", "HEAD") != source:
        raise ValueError("parallel release source changed")
    if _git(root, "status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("parallel release checkout has source changes")


def _active_platformio() -> bool:
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            words = (proc / "cmdline").read_bytes().split(b"\0")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        for i, word in enumerate(words):
            if Path(os.fsdecode(word)).name in ("pio", "platformio") and any(
                    operation in words[i + 1:] for operation in (b"run", b"test", b"pkg")):
                return True
    return False


def _seed_core(source: Path, destination: Path) -> None:
    """Copy once before any worker starts; mutable package trees are never linked."""
    marker = destination / ".meshcore-seeded.json"
    if marker.is_file() and not marker.is_symlink():
        if json.loads(marker.read_text()).get("source") != str(source):
            raise ValueError("worker PlatformIO seed source changed")
        return
    if destination.exists():
        raise ValueError("worker PlatformIO directory lacks its seed receipt")
    if _active_platformio():
        raise RuntimeError("cannot seed PlatformIO packages while a PlatformIO process is active")
    print(f"Preparing isolated PlatformIO packages: {destination}", flush=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".core-seed-", dir=destination.parent))
    try:
        for name in ("packages", "platforms", ".cache"):
            original = source / name
            if original.is_symlink():
                raise ValueError("PlatformIO seed directory is a symlink")
            if original.is_dir():
                subprocess.run(["cp", "-a", "--reflink=auto", "--", str(original),
                                str(staging / name)], check=True)
        (staging / marker.name).write_text(json.dumps({"source": str(source)}, indent=2) + "\n")
        staging.rename(destination)
        print(f"Isolated PlatformIO packages ready: {destination}", flush=True)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def cleanup_worker_caches(build: Path, libdeps: Path, active: set[str],
                          minimum_free: int = MINIMUM_FREE_BYTES) -> list[str]:
    """Evict only this worker's inactive generated environment directories."""
    if not active or any(not re.fullmatch(r"[A-Za-z0-9_.+-]+", value) for value in active):
        return []
    removed = []
    for parent in (build, libdeps):
        if parent.is_symlink():
            raise ValueError("worker cache root is a symlink")
        if not parent.is_dir():
            continue
        candidates = sorted((p for p in parent.iterdir()
                             if p.is_dir() and not p.is_symlink() and p.name not in active),
                            key=lambda p: p.stat().st_mtime)
        for candidate in candidates:
            if shutil.disk_usage(parent).free >= minimum_free:
                return removed
            print(f"Clearing inactive worker cache: {candidate}", flush=True)
            shutil.rmtree(candidate)
            removed.append(str(candidate))
    return removed


def worker_pio_main(real_pio: str, build: str, libdeps: str) -> None:
    arguments = sys.argv[1:]
    if arguments and arguments[0] == "run":
        active = set()
        for i, value in enumerate(arguments):
            if value in ("-e", "--environment") and i + 1 < len(arguments):
                active.add(arguments[i + 1])
            elif value.startswith("--environment="):
                active.add(value.split("=", 1)[1])
        cleanup_worker_caches(Path(build), Path(libdeps), active)
    os.execv(real_pio, [real_pio, *arguments])


@contextmanager
def _pool_lock(pool: Path):
    if pool.is_symlink() or pool.parent.is_symlink():
        raise ValueError("parallel release worker pool is a symlink")
    pool.mkdir(parents=True, exist_ok=True)
    lock = pool / "coordinator.lock"
    if lock.is_symlink():
        raise ValueError("parallel release lock is a symlink")
    with lock.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another release owns these parallel workers") from error
        yield


def _pool(root: Path, version: str, source: str, workers: int) -> Path:
    if not re.fullmatch(r"v[0-9]+(?:\.[0-9]+){3}-halo-keymind-cascade-dev", version):
        raise ValueError("invalid parallel release version")
    if type(workers) is not int or workers < 1:
        raise ValueError("parallel worker count must be positive")
    return root / ".releases" / f".workers-{version}-{source[:8]}"


def _prepare_workers(root: Path, version: str, source: str, workers: int,
                     jobs: int, environment: dict[str, str]) -> list[dict]:
    _validate_source(root, source)
    if type(jobs) is not int or jobs < 1:
        raise ValueError("worker compiler job count must be positive")
    pool = _pool(root, version, source, workers)
    seed = Path(environment.get("PLATFORMIO_CORE_DIR", str(Path.home() / ".platformio"))).resolve()
    real_pio = shutil.which("pio", path=environment.get("PATH"))
    if not real_pio:
        raise ValueError("PlatformIO executable was not found")
    result = []
    for index in range(workers):
        checkout = pool / f"worker-{index}"
        if not checkout.exists():
            subprocess.run(["git", "worktree", "add", "--detach", str(checkout), source],
                           cwd=root, check=True)
        if checkout.is_symlink():
            raise ValueError("worker checkout is a symlink")
        _validate_source(checkout, source)
        core, build = pool / f"core-{index}", pool / f"build-{index}"
        libdeps, output = pool / f"libdeps-{index}", pool / f"output-{index}"
        for path in (core, build, libdeps, output):
            if path.is_symlink():
                raise ValueError("worker directory is a symlink")
        _seed_core(seed, core)
        bin_dir = pool / f"bin-{index}"
        bin_dir.mkdir(exist_ok=True)
        wrapper = bin_dir / "pio"
        wrapper.write_text(
            "#!/usr/bin/python3\nimport sys\n" +
            f"sys.path.insert(0, {str(root / 'scripts')!r})\n" +
            "from parallel_release_workers import worker_pio_main\n" +
            f"worker_pio_main({str(real_pio)!r}, {str(build)!r}, {str(libdeps)!r})\n",
            encoding="ascii")
        wrapper.chmod(0o700)
        selected = environment.copy()
        for key in ("MESHCORE_RELEASE_SHARD_INDEX", "MESHCORE_RELEASE_SHARD_COUNT"):
            selected.pop(key, None)
        selected.pop("PLATFORMIO_BUILD_CACHE_DIR", None)
        selected.update({"PLATFORMIO_CORE_DIR": str(core), "PLATFORMIO_BUILD_DIR": str(build),
                         "PLATFORMIO_PACKAGES_DIR": str(core / "packages"),
                         "PLATFORMIO_PLATFORMS_DIR": str(core / "platforms"),
                         "PLATFORMIO_CACHE_DIR": str(core / ".cache"),
                         "PLATFORMIO_GLOBALLIB_DIR": str(core / "lib"),
                         "PLATFORMIO_WORKSPACE_DIR": str(checkout / ".pio"),
                         "PLATFORMIO_LIBDEPS_DIR": str(libdeps), "OUTPUT_DIR": str(output),
                         "OPTION3_PIO_JOBS": str(jobs),
                         "PATH": str(bin_dir) + os.pathsep + environment.get("PATH", os.defpath)})
        output.mkdir(exist_ok=True)
        result.append({"index": index, "checkout": checkout, "environment": selected,
                       "output": output, "embedded": pool / f"embedded-{index}"})
    return result


def _stop(process: subprocess.Popen) -> None:
    # A failed shell can exit before its compiler children. Always address the
    # owned process group, even after its original group leader has exited.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def _run_workers(specifications: list[dict], command_for, phase: str) -> None:
    running = []
    cleaned = set()
    previous_term = signal.getsignal(signal.SIGTERM)
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt("parallel release terminated")
    signal.signal(signal.SIGTERM, interrupted)
    try:
        for spec in specifications:
            log = spec["output"].parent / f"{phase}-worker-{spec['index']}.log"
            stream = log.open("a", encoding="utf-8")
            try:
                process = subprocess.Popen(command_for(spec), cwd=spec["checkout"],
                                           env=spec["environment"], stdout=stream,
                                           stderr=subprocess.STDOUT, start_new_session=True)
            except BaseException:
                stream.close()
                raise
            running.append((process, stream, log))
            print(f"Parallel {phase} worker {spec['index']} started; log: {log}", flush=True)
        next_report = time.monotonic() + 30
        while True:
            for index, (process, _, log) in enumerate(running):
                if index in cleaned or process.poll() is None:
                    continue
                # Reap the group as soon as its leader exits. A faster shard
                # can finish hours before its sibling; do not retain an old
                # PGID and signal it only at the end of the slower shard.
                _stop(process)
                cleaned.add(index)
                if process.returncode != 0:
                    raise RuntimeError(f"parallel {phase} worker failed; see {log}")
            if len(cleaned) == len(running):
                break
            if time.monotonic() >= next_report:
                print(f"Parallel {phase}: " + ", ".join(
                    f"worker {i} {'running' if p.poll() is None else 'finished'}"
                    for i, (p, _, _) in enumerate(running)), flush=True)
                next_report = time.monotonic() + 30
            time.sleep(1)
    finally:
        try:
            for index, (process, stream, _) in enumerate(running):
                try:
                    if index not in cleaned:
                        _stop(process)
                finally:
                    stream.close()
        finally:
            signal.signal(signal.SIGTERM, previous_term)


def _validate_shards(specifications: list[dict]) -> None:
    expected = None
    combined = set()
    count = len(specifications)
    for spec in specifications:
        index = spec["index"]
        prefix = spec["output"] / f"release-shard-{index}-of-{count}"
        all_targets = Path(str(prefix) + "-all-targets.txt").read_text().splitlines()
        targets = Path(str(prefix) + "-targets.txt").read_text().splitlines()
        if (not all_targets or len(set(all_targets)) != len(all_targets)
                or any(not re.fullmatch(r"[A-Za-z0-9_.+-]+", value) for value in all_targets)):
            raise ValueError("invalid canonical release target ledger")
        if expected is None:
            expected = all_targets
        if all_targets != expected or targets != all_targets[index::count]:
            raise ValueError("parallel workers used different target selection")
        if combined.intersection(targets):
            raise ValueError("parallel release target shards overlap")
        combined.update(targets)
    if combined != set(expected or []):
        raise ValueError("parallel release target shards are incomplete")


def merge_worker_outputs(outputs: list[Path], destination: Path) -> None:
    """Preflight every conflict and unsafe path before copying any package."""
    if destination.is_symlink():
        raise ValueError("merged release directory is a symlink")
    planned = {}
    for index, output in enumerate(outputs):
        if output.is_symlink() or not output.is_dir():
            raise ValueError("worker output is missing or a symlink")
        for path in sorted(output.rglob("*")):
            if path.is_symlink():
                raise ValueError("worker output contains a symlink")
            if path.is_dir():
                continue
            if not path.is_file():
                raise ValueError("worker output contains a nonregular file")
            relative = path.relative_to(output)
            if relative.parts[0] == "build-logs":
                relative = Path("build-logs") / f"worker-{index}" / Path(*relative.parts[1:])
            target = destination / relative
            if target.is_symlink() or any(parent.is_symlink() for parent in target.parents):
                raise ValueError("merged output path traverses a symlink")
            previous = planned.get(target, target if target.exists() else None)
            if previous is not None:
                if not previous.is_file() or _digest(previous) != _digest(path):
                    raise ValueError(f"conflicting parallel release artifact: {relative}")
            else:
                planned[target] = path
    destination.mkdir(parents=True, exist_ok=True)
    for target, original in planned.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".parallel-copy-", dir=target.parent)
        os.close(descriptor)
        temporary = Path(name)
        try:
            shutil.copy2(original, temporary)
            if _digest(temporary) != _digest(original):
                raise ValueError("parallel release copy verification failed")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def build_parallel_matrix(root: Path, version: str, source: str, workers: int,
                          jobs: int, environment: dict[str, str], work: Path) -> Path:
    root, work = Path(root).resolve(), Path(work).absolute()
    with _pool_lock(_pool(root, version, source, workers)):
        specs = _prepare_workers(root, version, source, workers, jobs, environment)
        for spec in specs:
            spec["environment"].update({"MESHCORE_RELEASE_SHARD_INDEX": str(spec["index"]),
                                        "MESHCORE_RELEASE_SHARD_COUNT": str(workers)})
        _run_workers(specs, lambda _: ["bash", "build_legacy.sh", "build-firmwares-logging-matrix",
                                      "--firmware-version", version, "--radio-preset", "usa-cascade-fixed",
                                      "--profile", "cascade", "--require-ota", "--skip-kiss", "--resume"],
                     "matrix")
        _validate_shards(specs)
        merge_worker_outputs([spec["output"] for spec in specs], work)
    return work


def build_parallel_embedded(root: Path, version: str, source: str, workers: int,
                            jobs: int, environment: dict[str, str], ordinary_work: Path,
                            embedded_work: Path) -> Path:
    root, embedded_work = Path(root).resolve(), Path(embedded_work).absolute()
    program = ("import sys; from pathlib import Path; sys.path.insert(0, 'scripts'); "
               "from build_local_release import build_embedded_bootloader_artifacts; "
               "import os; build_embedded_bootloader_artifacts(Path(sys.argv[1]), "
               "Path(sys.argv[2]), sys.argv[3], sys.argv[4], dict(os.environ))")
    with _pool_lock(_pool(root, version, source, workers)):
        specs = _prepare_workers(root, version, source, workers, jobs, environment)
        _validate_shards(specs)
        # The ordinary merge must be the same successful worker set; it is not
        # a resume source for another checkout's path-bound recipe fingerprints.
        if not Path(ordinary_work).is_dir():
            raise ValueError("merged ordinary release is unavailable")
        _run_workers(specs, lambda spec: [sys.executable, "-c", program,
                                         str(spec["output"]), str(spec["embedded"]),
                                         version, source[:8]], "embedded")
        # A shard with no supported nRF52 target still creates its empty dir.
        for spec in specs:
            spec["embedded"].mkdir(exist_ok=True)
        merge_worker_outputs([spec["embedded"] for spec in specs], embedded_work)
    return embedded_work
