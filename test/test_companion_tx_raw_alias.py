#!/usr/bin/env python3
"""Keep actual channel aliases raw across durable same-value TX route SETs."""
from pathlib import Path
import hashlib
import shutil
import subprocess
import tempfile
import unittest

from test_companion_channel_publication import publication_fixture
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]


def replace_once(source, before, after):
    assert source.count(before) == 1, before
    return source.replace(before, after, 1)


def raw_alias_fixture(work):
    mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="ascii")
    header = (ROOT / "examples/companion_radio/MyMesh.h").read_text(encoding="ascii")
    base = (ROOT / "src/helpers/BaseChatMesh.cpp").read_text(encoding="ascii")
    # The alias/key helpers execute unchanged. Supply independent SHA-256
    # vectors only at their crypto boundary, including cached channel hashes.
    vectors = [b"#r2-emergency", b"#emergency", bytes(16)]
    vectors += [hashlib.sha256(name).digest()[:16] for name in vectors[:2]]
    sha = "static void sha256(uint8_t* out, size_t size, const uint8_t* input, int length) {\n"
    for raw in vectors:
        data = ",".join(str(value) for value in raw)
        digest = ",".join(str(value) for value in hashlib.sha256(raw).digest())
        sha += ("  { static const uint8_t in[] = {" + data + "};\n"
                "    static const uint8_t hash[] = {" + digest + "};\n"
                "    if (length == int(sizeof(in)) && !memcmp(in,input,sizeof(in))) "
                "{ assert(size <= sizeof(hash)); memcpy(out,hash,size); return; } }\n")
    sha += '  assert(!"input outside independent SHA256 oracle");\n}\n'

    generated = publication_fixture(work, False)
    code = generated.read_text(encoding="ascii")
    code = code[:code.index("int main() {")]
    code = replace_once(code, "struct Utils {", "struct Utils {\n" + sha)
    # Replace the publication fixture's simplified channel boundary with the
    # real raw accessors, alias resolution and generation-changing setter.
    for signature, declaration in (
        (" bool getChannel(int i, ChannelDetails& c)", " bool getChannel(int, ChannelDetails&);"),
        (" bool setChannel(int i, const ChannelDetails& c)", " bool setChannel(int, const ChannelDetails&);"),
        (" bool isAliasRadio2Defined() const", " bool isAliasRadio2Defined() const;"),
        (" bool resolveChannelAlias(ChannelDetails&) const", " bool resolveChannelAlias(ChannelDetails&) const;"),
    ):
        code = replace_once(code, method(code, signature), declaration)
    for signature, production in (
        (" bool getChannelForSave(uint8_t i,ChannelDetails& c)", "  bool getChannelForSave(uint8_t channel_idx,"),
        (" bool onChannelLoaded(uint8_t i,const ChannelDetails& c)", "  bool onChannelLoaded(uint8_t channel_idx,"),
    ):
        code = replace_once(code, method(code, signature), method(header, production))
    code = replace_once(code, " ChannelDetails channels[MAX_GROUP_CHANNELS];\n", "")
    code = replace_once(code, "class MyMesh : public DataStoreHost {", """
class BaseChatMesh {
public:
  ChannelDetails channels[MAX_GROUP_CHANNELS];
  bool getChannel(int, ChannelDetails&);
  bool setChannel(int, const ChannelDetails&);
};
class MyMesh : public BaseChatMesh, public DataStoreHost {""")
    code = replace_once(code, " bool writable=true, writes_ok=true; unsigned writes=0;", """
 struct { bool enabled = false; } _ethernet_settings;
 bool writable=true, writes_ok=true; unsigned writes=0;""")
    assert "unsigned channel_sets=0, _ethernet_channel_generation=0;" in code
    code += "\n" + "\n".join(method(base, signature) for signature in (
        "bool BaseChatMesh::getChannel(", "bool BaseChatMesh::setChannel("))
    code += "\n" + "\n".join(method(mesh, signature) for signature in (
        "bool MyMesh::isAliasRadio2Defined(", "bool MyMesh::resolveChannelAlias(",
        "bool MyMesh::getChannel(", "bool MyMesh::setChannel("))
    code += r'''
int main() {
  MyMesh node;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  node.store.fs.rename_replaces = true;
#endif
  strcpy(node.channels[0].name, "#r2-emergency");
  mesh::Utils::sha256(node.channels[0].channel.secret, 16,
      reinterpret_cast<const uint8_t*>(node.channels[0].name), strlen(node.channels[0].name));
  node.channels[0].channel.tx_radio = mesh::RADIO_TX_AUTO;
  node.radio.config.secondary.mode = mesh::RadioProfileMode::RxTx;
  node.radio.config.cross = mesh::RadioCrossMode::Off;
  strcpy(node.contacts[1].name, "Healthy contact");
  node.contacts[1].tx_radio = mesh::RADIO_TX_PRIMARY;
  assert(node.store.saveChannels(&node));
  const ChannelDetails raw = node.channels[0];
  const auto original = node.store.fs.files.at("/channels2");
  uint8_t canonical_key[16];
  mesh::Utils::sha256(canonical_key, sizeof(canonical_key),
      reinterpret_cast<const uint8_t*>("#emergency"), strlen("#emergency"));
  ChannelDetails effective;
  assert(node.getChannel(0, effective));
  assert(effective.channel.tx_radio == mesh::RADIO_TX_SECONDARY);
  assert(memcmp(effective.channel.secret, raw.channel.secret, 16));
  assert(!memcmp(effective.channel.secret, canonical_key, sizeof(canonical_key)));
  char reply[160] = {};
  const auto generation = node._ethernet_channel_generation;
  auto writes = node.writes;
  node.store.fs.fail_write = true;
  assert(node.handleTxRoutingCommand("set tx.channel 0 radio2", reply, sizeof(reply)));
  assert(strstr(reply, "not saved") && node.writes == writes + 1);
  assert(!memcmp(&node.channels[0], &raw, sizeof(raw)));
  assert(node._ethernet_channel_generation == generation);
  assert(node.store.fs.files.at("/channels2") == original);
  assert(!node.store.hasIncompleteChannelLoad());
  node.store.fs.fail_write = false;
  writes = node.writes;
  assert(node.handleTxRoutingCommand("set tx.channel 0 radio2", reply, sizeof(reply)));
  assert(!strncmp(reply, "OK", 2) && node.writes == writes + 1);
  assert(!memcmp(&node.channels[0], &raw, sizeof(raw)));
  assert(node._ethernet_channel_generation == generation);
  assert(node.store.fs.files.at("/channels2") == original);
  assert(original[0] == mesh::encodeRadioTxPolicy(mesh::RADIO_TX_AUTO));
  assert(!memcmp(original.data() + 36, raw.channel.secret, 32));
  writes = node.writes;
  assert(node.handleTxRoutingCommand("get tx.channel 0", reply, sizeof(reply)));
  assert(node.handleTxRoutingCommand("set tx.user \"Healthy contact\" radio", reply, sizeof(reply)));
  assert(!strncmp(reply, "OK", 2) && node.writes == writes);
  assert(node.handleTxRoutingCommand("set tx.channel 0 radio", reply, sizeof(reply)));
  assert(strstr(reply, "prefix controls routing") && node.writes == writes);
  MyMesh reboot;
  reboot.store.fs.files = node.store.fs.files;
  reboot.radio.config.secondary = node.radio.config.secondary;
  reboot.store.loadChannels(&reboot);
  assert(reboot.channels[0].channel.tx_radio == mesh::RADIO_TX_AUTO);
  assert(!memcmp(reboot.channels[0].channel.secret, raw.channel.secret, 32));
  assert(reboot.getChannel(0, effective));
  assert(effective.channel.tx_radio == mesh::RADIO_TX_SECONDARY);
  assert(!memcmp(effective.channel.secret, canonical_key, sizeof(canonical_key)));
  puts("PASS: actual channel SET retry, raw AUTO/app PSK and effective SECONDARY/wire key");
}
'''
    generated.write_text(code, encoding="ascii")
    return generated


class CompanionTxRawAliasTest(unittest.TestCase):
    def run_platform(self, platform):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="channel-samevalue-real-alias-") as directory:
            work = Path(directory)
            generated = raw_alias_fixture(work)
            binary = work / "test"
            compiled = subprocess.run([
                compiler, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-D" + platform + "=1",
                "-DMESH_CONTACT_CACHE=0", "-DMESH_ENABLE_MESHTEXT_CHAT=0", "-DMESH_ENABLE_ETHERNET_TUNNEL=1",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-I", str(work), "-I", str(ROOT / "test/fixtures/radio_profiles/mocks"),
                "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"), "-I", str(ROOT / "src/helpers"),
                str(generated), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            tested = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
            self.assertIn("PASS: actual channel SET retry", tested.stdout)

    def test_nrf52_raw_alias_failure_and_retry(self):
        self.run_platform("NRF52_PLATFORM")

    def test_stm32_raw_alias_failure_and_retry(self):
        self.run_platform("STM32_PLATFORM")

    def test_esp32_raw_alias_failure_and_retry(self):
        self.run_platform("ESP32_PLATFORM")

    def test_rp2040_raw_alias_failure_and_retry(self):
        self.run_platform("RP2040_PLATFORM")


if __name__ == "__main__":
    unittest.main()
