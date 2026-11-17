"""Verify every retry flag stays independent in the compact transient layout."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FLAGS = {
    "DirectRetryEntry": ("expect_path_growth", "final_hop_retry", "waiting_final_echo",
                         "queued", "has_message_replacement_key", "active"),
    "FloodRetryEntry": ("self_advert", "waiting_final_echo", "queued",
                        "has_message_replacement_key", "active"),
}


def source():
    header = (ROOT / "src/Mesh.h").read_text()
    code = "#include <stdint.h>\n#include <string.h>\n#include <assert.h>\n"
    code += "#define NRF52_PLATFORM 1\n#define MESH_CLIENT_REPEATER_ONLY 1\n"
    code += "constexpr unsigned MAX_HASH_SIZE=8; struct Packet {};\n"
    checks = []
    for name, fields in FLAGS.items():
        code += extract_braced(header, "struct " + name + " {") + ";\n"
        limit = 68 if name == "DirectRetryEntry" else 44
        code += f'static_assert(sizeof(void*)!=4 || sizeof({name})=={limit}, "ARM retry layout changed");\n'
        code += f"void check{name}() {{\n"
        code += f"for(unsigned mask=0;mask<{1 << len(fields)};++mask) {{\n{name} entry={{}};\n"
        code += "Packet packet,trigger;entry.packet=&packet;entry.trigger_packet=&trigger;\n"
        code += "entry.retry_started_at=0xfffffff0UL;entry.retry_at=17;entry.retry_delay=29;entry.message_timestamp=0x12345678;\n"
        code += "memset(entry.retry_key,0x45,MAX_HASH_SIZE);memset(entry.message_replacement_key,0x89,MAX_HASH_SIZE);\n"
        for index, field in enumerate(fields):
            code += f"entry.{field}=((mask>>{index})&1)!=0;\n"
        code += "auto copy=entry;memset(&entry,0,sizeof(entry));entry=copy;\n"
        for index, field in enumerate(fields):
            code += f"assert(entry.{field}==bool((mask>>{index})&1));\n"
        for index, field in enumerate(fields):
            code += f"entry.{field}=!entry.{field};\n"
            for other, other_field in enumerate(fields):
                code += f"assert(entry.{other_field}==bool(((mask^{1 << index})>>{other})&1));\n"
            code += f"entry.{field}=!entry.{field};\n"
        code += "assert(entry.packet==&packet&&entry.trigger_packet==&trigger);\n"
        code += "assert(entry.retry_started_at==0xfffffff0UL&&entry.retry_at==17&&entry.retry_delay==29&&entry.message_timestamp==0x12345678);\n"
        code += "for(unsigned i=0;i<MAX_HASH_SIZE;++i)assert(entry.retry_key[i]==0x45&&entry.message_replacement_key[i]==0x89);\n"
        code += "}}\n"
        checks.append("check" + name + "();")
    return code + "int main(){" + "".join(checks) + "}\n"


class RetryMetadataLayoutTests(unittest.TestCase):
    def test_all_flag_combinations_copy_and_toggle_without_touching_retry_identity(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "host C++ compiler required")
        with tempfile.TemporaryDirectory(prefix="retry-metadata-") as directory:
            work = Path(directory)
            cpp = work / "test.cpp"
            cpp.write_text(source())
            executable = work / "test"
            built = subprocess.run([compiler, "-std=c++11", "-O2", "-Wall", "-Wextra", "-Werror",
                                    str(cpp), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_arm_layout_retains_every_slot_with_smaller_naturally_aligned_records(self):
        compiler = shutil.which("arm-none-eabi-g++")
        if not compiler:
            compiler = next((str(path) for path in (
                Path.home() / ".platformio/packages/toolchain-gccarmnoneeabi/bin/arm-none-eabi-g++",
                Path.home() / ".platformio/packages/toolchain-gccarmnoneeabi/bin/arm-none-eabi-g++.exe",
            ) if path.is_file()), None)
        if not compiler:
            self.skipTest("ARM compiler not installed; firmware builds enforce the same layout")
        with tempfile.TemporaryDirectory(prefix="retry-metadata-arm-") as directory:
            work = Path(directory)
            cpp = work / "test.cpp"
            cpp.write_text(source())
            built = subprocess.run([compiler, "-std=c++11", "-mcpu=cortex-m4", "-mthumb", "-Os",
                                    "-c", str(cpp), "-o", str(work / "test.o")], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)


if __name__ == "__main__":
    unittest.main()
