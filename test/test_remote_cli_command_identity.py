"""Exercise production repeater receive/dispatch against mutating CLI handlers."""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/simple_repeater/MyMesh.cpp"
FIXTURE = ROOT / "test/fixtures/remote_cli_command_identity/test.cpp"


class RemoteCliCommandIdentityTest(unittest.TestCase):
    def test_wire_identity_survives_mutation_and_retries(self):
        source = SOURCE.read_text(encoding="utf-8")
        receive = extract_braced(source, "void MyMesh::onPeerDataRecv(")
        dispatch = extract_braced(source, "void __attribute__((noinline)) MyMesh::processDeferredCliCommand()")
        trim_start = source.index("  char* command_end = command + strlen(command);")
        trim_end = source.index("\n\n", trim_start)
        normalize_start = source.index("  while (*command == ' ') command++;", trim_end)
        normalize_end = source.index("  mesh::cli::normalizeCommandVerb(command);", normalize_start)
        normalize_end += len("  mesh::cli::normalizeCommandVerb(command);")
        mutation = source[trim_start:trim_end] + "\n" + source[normalize_start:normalize_end]
        # setperm splits the argument in the same buffer; retain its real
        # delimiter mutation without pulling ACL/filesystem hardware into this test.
        setperm = source.index('char* hex = &command[8];', normalize_end)
        tokenize_end = source.index('uint8_t pubkey[PUB_KEY_SIZE];', setperm)
        tokenize = source[setperm:tokenize_end]
        tokenize = re.sub(r"size_t hex_len = [^;]+;", "", tokenize)
        # Close the successful-argument branch whose delimiter statement we extracted.
        mutation += '\nif (strncmp(command, "setperm ", 8) == 0) {\n' + tokenize + '\n}\n}\n'
        generated = mutation + "\n"
        acl = (ROOT / "src/helpers/ClientACL.cpp").read_text(encoding="utf-8")
        delete_start = acl.index("    num_clients--;   // delete from contacts[]")
        delete_end = acl.index("  } else {", delete_start)
        completion = extract_braced(source, "bool MyMesh::completeHostCliRequest(")
        serial_reply = extract_braced(source, "bool MyMesh::handleHostCliSerialReply(")
        clear = extract_braced(source, "void MyMesh::clearDeferredCliCommand()")
        with tempfile.TemporaryDirectory(prefix="meshcore-cli-identity-") as directory:
            work = Path(directory)
            (work / "normalization.inc").write_text(generated, encoding="utf-8")
            (work / "acl_delete.inc").write_text(acl[delete_start:delete_end], encoding="utf-8")
            (work / "production.inc").write_text("\n".join(
                (receive, dispatch, completion, serial_reply, clear)), encoding="utf-8")
            binary = work / "cli-identity.exe"
            sanitizer = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                         "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else []
            for host_enabled in (0, 1):
                with self.subTest(host_enabled=host_enabled):
                    result = subprocess.run([
                        os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra",
                        "-Werror", "-Wno-unused-parameter", *sanitizer,
                        f"-DMESH_ENABLE_HOST_CLI={host_enabled}",
                        f"-I{work}", f"-I{ROOT / 'src'}", str(FIXTURE), "-o", str(binary),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("CLI wire identity checks passed", checked.stdout)

    def test_host_completion_uses_original_wire_fingerprint(self):
        source = SOURCE.read_text(encoding="utf-8")
        completion = extract_braced(source, "bool MyMesh::completeHostCliRequest(")
        self.assertIn("deferred_cli_command.command_fingerprint", completion)
        self.assertNotIn("RemoteCliReplyCache::fingerprint(", completion)


if __name__ == "__main__":
    unittest.main()
