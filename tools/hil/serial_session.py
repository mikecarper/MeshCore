"""Open an already configured serial session without a bridge reset pulse."""
import os
import re
import shutil
import subprocess
import sys
import time


class SerialCommandTimeout(TimeoutError):
    def __init__(self, partial_reply):
        super().__init__("Timed out waiting for this command's exact echo and completed terminal prompt")
        self.partial_reply = partial_reply


def _command_echoes(command):
    echoes = [command]
    # Match the firmware's intentional password masking, not a wildcard echo.
    # Masked UTF-8 values occupy one '*' for each input byte on the device.
    for pattern in (r"^(\s*login[ \t]+)(.+)$", r"^(\s*set[ \t]+wifi\.pwd[ \t]+)(.+)$",
                    r"^(\s*set[ \t]+mt\.channel[ \t]+\S+[ \t]+\S+[ \t]+)(.+)$"):
        match = re.match(pattern, command)
        if match:
            echoes.append(match[1] + "*" * len(match[2].encode("utf-8")))
            break
    return echoes


def read_command_reply(port, command, timeout=10):
    """Wait for this exact echoed command and its completion prompt, not silence.

    A settings save can pause for over 350 ms after the immediate input echo.
    Returning at that silence would attribute the delayed OK to the next command.
    Stale responses/prompts before this command's echo cannot finish the read.
    """
    if not command or any(value in command for value in "\r\n\0") or timeout <= 0:
        raise ValueError("one nonempty command and a positive timeout are required")
    echoed = re.compile(r"(?:^|\r?\n)(?:> ?)?(?:" + "|".join(
        re.escape(value) for value in _command_echoes(command)) + r")\r?\n")
    # The real terminal writes "> ". Requiring its trailing space avoids
    # returning on a fragmented '>' and leaving that space to poison next echo.
    prompt = re.compile(r"(?:^|\r?\n)>[ \t]+(?=$|\r?\n)")
    deadline = time.monotonic() + timeout
    data = bytearray()
    while time.monotonic() < deadline:
        chunk = port.read(min(port.in_waiting or 1, 4096))
        if not chunk:
            continue
        data.extend(chunk)
        if len(data) > 65536:
            raise RuntimeError("CLI transaction output exceeded the bounded capture limit")
        text = data.decode("utf-8", errors="replace")
        match = echoed.search(text)
        if match and prompt.search(text[match.end():]):
            return text
    raise SerialCommandTimeout(data.decode("utf-8", errors="replace"))


def assert_port_idle(path):
    """Optional Linux preflight: never open/reset a port owned by another tool.

    fuser does not open the serial device. This is an extra immediate check,
    not a substitute for an exclusive open or coordination with the flasher.
    """
    if not sys.platform.startswith("linux"):
        return
    inspector = shutil.which("fuser")
    if not inspector:
        return
    try:
        result = subprocess.run([inspector, "--", os.path.realpath(path)],
                                capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Cannot safely inspect serial-port ownership") from error
    if result.returncode == 0:
        raise RuntimeError("Selected serial port is already in use; finish the flasher/client first")
    if result.returncode != 1:
        raise RuntimeError("Cannot safely inspect serial-port ownership (fuser error)")


def open_configured_session(port):
    # POSIX pySerial normally applies DTR before RTS when opening. Some
    # bridge drivers initially assert both lines; clearing DTR first pulses
    # ESP32 EN. Defer DTR until RTS is released. Windows configures both in
    # one DCB operation and uses dsrdtr for real handshaking, so leave it alone.
    # The caller selects DTR and supplies the already configured port; no USB
    # inventory or pySerial import is needed here.
    defer_dtr = os.name == "posix" and not port.dtr
    previous_dsrdtr = port.dsrdtr
    try:
        if defer_dtr:
            port.dsrdtr = True
        port.open()
        if defer_dtr:
            port.rts = False
            port.dtr = False
            port.dsrdtr = previous_dsrdtr
    except BaseException:
        try:
            port.close()
        finally:
            if defer_dtr:
                port.dsrdtr = previous_dsrdtr
        raise
