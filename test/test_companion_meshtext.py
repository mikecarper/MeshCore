"""Run actual Companion Meshtext methods with real CLI/AES/SHA and bounded adapters.

No PlatformIO or hardware is used. Only filesystem/NVS, the serial/UI boundary,
and the foreign-frame queue are substituted. Protocol/profile decisions remain
the production code, extracted without rewriting their bodies.
"""

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from cpp_source import body
from test_meshtext_chat import array, data_text, reference_frame, varint

ROOT = Path(__file__).resolve().parents[1]


class CompanionMeshtextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import cryptography  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("Python cryptography is required for independent receive frames")
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise unittest.SkipTest("a host C++ compiler is required")
        required = ["AESCommon.cpp", "AES128.cpp", "AES256.cpp", "BlockCipher.cpp",
                    "Crypto.cpp", "SHA256.cpp", "Hash.cpp"]
        override = os.environ.get("MESHCORE_CRYPTO_DIR")
        candidates = [Path(override)] if override else sorted((ROOT / ".pio/libdeps").glob("*/Crypto"))
        crypto = next((path for path in candidates if all((path / name).is_file() for name in required)), None)
        if not crypto:
            raise unittest.SkipTest("an existing rweather/Crypto checkout is required (MESHCORE_CRYPTO_DIR)")
        cls.temp = tempfile.TemporaryDirectory(prefix="meshcore-companion-mt-")
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        # Identity.h uses a quoted Utils include. Shadow both to keep the real
        # SHA implementation rather than the general native tests' zero hash.
        utils = (ROOT / "test/mocks/Utils.h").read_text(encoding="utf-8")
        fake_sha = body(utils, "static void sha256(")
        utils = utils.replace(fake_sha, "static void sha256(uint8_t*, size_t, const uint8_t*, int);")
        utils = utils.replace("#define CIPHER_MAC_SIZE 16", "#ifndef CIPHER_MAC_SIZE\n#define CIPHER_MAC_SIZE 16\n#endif")
        utils = utils.replace("class Utils {", "class Utils {\npublic:\n  static bool isHexChar(char);\n  static bool fromHex(uint8_t*, int, const char*);\n  static bool isZeroes(const uint8_t* p, size_t n) {\n    for (size_t i=0;i<n;++i) { if(p[i]) return false; }\n    return true;\n  }")
        (work / "Utils.h").write_text(utils, encoding="utf-8")
        identity = (ROOT / "test/mocks/Identity.h").read_text(encoding="utf-8")
        (work / "Identity.h").write_text(identity.replace('"Utils.h"', '<Utils.h>'), encoding="utf-8")

        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="utf-8")
        mt = (ROOT / "examples/companion_radio/MeshtextCompanion.cpp").read_text(encoding="utf-8")
        mt = mt[mt.index("namespace {"):].rsplit("#endif", 1)[0]
        signatures = ("bool MyMesh::isAliasRadio2Defined(", "bool MyMesh::resolveChannelAlias(",
                      "bool MyMesh::getChannel(", "bool MyMesh::setChannel(",
                      "static bool isEmergencyNoticeChannelName(",
                      "void MyMesh::seedEmergencyChannelNotice(",
                      "void MyMesh::servicePendingChannelNotices(", "bool MyMesh::queueChannelText(",
                      "bool MyMesh::saveAdvertName(")
        methods = "\n".join(body(source, signature) for signature in signatures)
        utils_source = (ROOT / "src/Utils.cpp").read_text(encoding="utf-8")
        # CLI keys use the production strict hexadecimal parser as well.
        methods = ("namespace mesh {\n" + "\n".join(body(utils_source, signature) for signature in
                   ("static uint8_t hexVal(", "bool Utils::isHexChar(", "bool Utils::fromHex("))
                   + "\n}\n" + methods)
        branches = " else ".join(body(source, signature) for signature in (
            "if (cmd_frame[0] == CMD_GET_CHANNEL && len >= 2)",
            "if (cmd_frame[0] == CMD_SET_CHANNEL && len >= 2 + 32 + 32)",
            "if (cmd_frame[0] == CMD_SET_CHANNEL && len >= 2 + 32 + 16)",
            "if (cmd_frame[0] == CMD_SEND_CHANNEL_TXT_MSG)",
        ))
        constants = []
        for name in ("CMD_GET_CHANNEL", "CMD_SET_CHANNEL", "CMD_SEND_CHANNEL_TXT_MSG",
                     "RESP_CODE_CHANNEL_INFO", "RESP_CODE_CHANNEL_MSG_RECV", "RESP_CODE_CHANNEL_MSG_RECV_V3",
                     "PUSH_CODE_MSG_WAITING", "ERR_CODE_UNSUPPORTED_CMD", "ERR_CODE_NOT_FOUND",
                     "ERR_CODE_FILE_IO_ERROR", "ERR_CODE_ILLEGAL_ARG", "ERR_CODE_BAD_STATE"):
            constants.append(re.search(r"^#define\s+" + name + r"\s+[^\r\n]+", source, re.MULTILINE).group(0))
        fixture = (ROOT / "test/fixtures/radio_profiles/cli_test.cpp").read_text(encoding="utf-8")
        fixture = fixture[:fixture.index("static void permanentScheduleOrder()")]
        text = (ROOT / "test/fixtures/companion_meshtext.cpp").read_text(encoding="utf-8")
        radio_source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text(encoding="utf-8")
        airtime = body(radio_source, "uint32_t RadioLibWrapper::getProfileAirtime(")
        airtime = "uint32_t actualProfileAirtime(uint8_t profile, int len, uint8_t cr=0) " + airtime[airtime.index("{"):]
        text = text.replace("@PROFILE_AIRTIME@", airtime)
        payload = ("A" * 153 + "\u20ac" + "B" * 69).encode()  # 225 bytes, truncation splits a scalar
        vectors = {
            "@RX_FRAME@": reference_frame(data_text(payload), packet_id=101),
            "@RX_RETRY@": reference_frame(data_text(b"retry"), packet_id=102),
            "@RX_DM@": reference_frame(data_text(b"dm"), to=0x99887766, packet_id=103),
            "@RX_ACK@": reference_frame(data_text(b"want ack") + b"\x18\x01", flags=0x6B, packet_id=104),
            "@RX_OTHER@": reference_frame(b"\x08\x02\x12\x01x", packet_id=105),
            "@RELAY_SHORT@": reference_frame(data_text(b"relay short"), packet_id=201),
            "@RELAY_LONG@": reference_frame(data_text(b"L" * 180), packet_id=202),
            "@RELAY_NODEINFO@": reference_frame(b"\x08\x04\x12\x00", packet_id=203),
            "@RELAY_PRIVATE128@": reference_frame(data_text(b"private relay"), "Team", bytes(range(1, 17)), packet_id=204),
            "@RELAY_PRIVATE256@": reference_frame(data_text(b"L" * 180), "Team", bytes(range(1, 33)), packet_id=205),
        }
        # Independent standard User/NodeInfo wire expectations; never encode
        # an expected NodeInfo through the production codec under test.
        def nodeinfo_frame(sender, packet_id, long_name, short_name, name="LongFast", key=None,
                           rebroadcast=False):
            def field(number, value):
                return varint((number << 3) | 2) + varint(len(value)) + value
            user = field(1, ("!%08x" % sender).encode())
            if long_name:
                user += field(2, long_name)
            user += field(3, short_name) + b"\x38" + varint(0 if rebroadcast else 1) + b"\x48\x01"
            data = b"\x08\x04" + field(2, user) + b"\x48\x01"  # NODEINFO_APP; MQTT consent only
            args = {} if key is None else {"key": key}
            return reference_frame(data, name, sender=sender, packet_id=packet_id, flags=0xE7, **args)
        default_node = int.from_bytes(hashlib.sha256(bytes([42]) + bytes(31)).digest()[:4], "little")
        vectors.update({
            "@RELAY_NODEINFO@": nodeinfo_frame(0x11223344, 203, b"Peer", b"PEER"),
            "@NODEINFO_DEFAULT@": nodeinfo_frame(default_node, 0x12345678, b"Test",
                                                ("%04x" % (default_node & 0xFFFF)).encode()),
            "@NODEINFO_AFTER_CHAT@": nodeinfo_frame(default_node, 0x12345679, b"Test",
                                                   ("%04x" % (default_node & 0xFFFF)).encode()),
            "@NODEINFO_REBROADCAST@": nodeinfo_frame(default_node, 0x12345679, b"Test",
                                                     ("%04x" % (default_node & 0xFFFF)).encode(),
                                                     rebroadcast=True),
            "@NODEINFO_RENAMED@": nodeinfo_frame(default_node, 0x1234567A, b"Renamed",
                                                ("%04x" % (default_node & 0xFFFF)).encode()),
            "@NODEINFO_NAMED@": nodeinfo_frame(0x12345678, 0x1234567A, b"Mike Carper", b"MIKE"),
            "@NODEINFO_UTF8@": nodeinfo_frame(0x12345678, 0x1234567C, b"A" * 22, "\U0001f600".encode()),
            "@NODEINFO_PRIVATE@": nodeinfo_frame(0x12345678, 0x1234567D, b"A" * 22,
                                                "\U0001f600".encode(), "Team", bytes(range(1, 33))),
        })
        text = text.replace("@FIXTURE@", fixture).replace("@CONSTANTS@", "\n".join(constants))
        text = text.replace("@METHODS@", methods + "\n" + mt).replace("@COMMANDS@", branches)
        for marker, frame in vectors.items():
            text = text.replace(marker, array(frame))
        legacy = bytearray(16)
        legacy[:4] = b"MT01"
        legacy[4] = 1  # Region::US; old reserved bytes 5..7 must remain zero.
        legacy[8:12] = (2).to_bytes(4, "little")
        legacy[12:] = hashlib.sha256(legacy[:12]).digest()[:4]
        text = text.replace("@LEGACY_SETTINGS@", array(legacy))
        legacy_hops = bytearray(legacy)
        legacy_hops[:4] = b"MT02"
        legacy_hops[5] = 3
        legacy_hops[12:] = hashlib.sha256(legacy_hops[:12]).digest()[:4]
        text = text.replace("@LEGACY_HOPS_SETTINGS@", array(legacy_hops))
        mt03 = bytearray(40)
        mt03[:4] = b"MT03"
        mt03[4:6] = bytes((1, 3))
        mt03[8:12] = (2).to_bytes(4, "little")
        mt03[36:] = hashlib.sha256(mt03[:36]).digest()[:4]
        text = text.replace("@LEGACY_MT03_SETTINGS@", array(mt03))
        mt03_named = bytearray(mt03)
        mt03_named[12:15] = b"OLD"
        mt03_named[32:36] = (0x12345678).to_bytes(4, "little")
        mt03_named[36:] = hashlib.sha256(mt03_named[:36]).digest()[:4]
        text = text.replace("@LEGACY_MT03_NAMED@", array(mt03_named))
        public_key = bytes([42]) + bytes(31)
        text = text.replace("@LOCAL_NODE@", str(int.from_bytes(hashlib.sha256(public_key).digest()[:4], "little")))
        for first in (43, 44):
            public_key = bytes([first]) + bytes(31)
            text = text.replace("@IMPORTED_NODE_%d@" % first,
                                str(int.from_bytes(hashlib.sha256(public_key).digest()[:4], "little")))
        cpp = work / "companion.cpp"
        cpp.write_text(text, encoding="utf-8")
        cls.binary = work / ("companion.exe" if sys.platform == "win32" else "companion")
        sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                      if sys.platform.startswith("linux") else [])
        result = subprocess.run(
            [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
             "-Wno-unused-variable", "-Wno-reorder", "-Wno-sign-compare",
             "-DMESH_ENABLE_MESHTEXT_CHAT=1", "-DMESH_ENABLE_FLEET_CONTROL=1",
             *sanitizers, "-I" + str(work), "-I" + str(crypto),
             "-I" + str(ROOT / "test/fixtures/radio_profiles/mocks"), "-I" + str(ROOT / "test/mocks"),
             "-I" + str(ROOT / "src"), str(cpp), str(ROOT / "src/helpers/RadioProfileCLI.cpp"),
             str(ROOT / "src/helpers/MeshtextChat.cpp"), *[str(crypto / name) for name in required],
             "-o", str(cls.binary)], capture_output=True, text=True, timeout=60,
        )
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def run_case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS " + name, result.stdout)

    def test_public_alias_and_private_aes_keys_preserve_raw_app_state(self):
        self.run_case("keys")

    def test_automatic_profile_is_runtime_only_and_no_automatic_transmit(self):
        self.run_case("auto")

    def test_conflicts_region_slots_and_custom_channel_dependency(self):
        self.run_case("conflicts")

    def test_saved_manual_overlay_temporary_priority_and_off_veto(self):
        self.run_case("manual")

    def test_atomic_settings_corruption_write_failures_and_reboot(self):
        self.run_case("settings")

    def test_actual_offline_queue_v1_v3_utf8_dedup_and_no_reply_relay(self):
        self.run_case("receive")

    def test_explicit_app_text_transmit_only_and_bounds(self):
        self.run_case("transmit")

    def test_binary_get_set_raw_psk_and_save_failure_rollback(self):
        self.run_case("commands")

    def test_private_cli_provisioning_metadata_masking_and_transactional_delete(self):
        self.run_case("cli")

    def test_default_seven_hops_cli_override_atomic_persistence_and_legacy_migration(self):
        self.run_case("hops")

    def test_shortname_unicode_identity_text_budget_atomic_settings_and_mt03_validation(self):
        self.run_case("shortname")

    def test_node_id_override_wire_sender_persistence_default_reset_and_failures(self):
        self.run_case("node")
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="utf-8")
        imported = body(source, "if (cmd_frame[0] == CMD_IMPORT_PRIVATE_KEY && len >= 65)")
        guard = re.search(r"else\s+if\s*\(hasForeignRadioFrame\(\)\)\s*\{[^}]*"
                          r"writeErrFrame\(ERR_CODE_BAD_STATE\)", imported)
        self.assertIsNotNone(guard, "pending Meshtext TX must block the identity import")
        self.assertLess(guard.end(), imported.index("flushContactsBeforeReboot()"))
        self.assertLess(guard.end(), imported.index("identity.readFrom("))
        self.assertLess(guard.end(), imported.index("_store->saveMainIdentity(identity)"))
        committed = body(imported, "if (_store->saveMainIdentity(identity))")
        self.assertLess(committed.index("self_id = identity;"), committed.index("refreshMeshtextIdentity();"))
        self.assertLess(committed.index("refreshMeshtextIdentity();"), committed.index("writeOKFrame();"))
        self.assertEqual(imported.count("refreshMeshtextIdentity();"), 1,
                         "the refresh belongs only to the successfully saved identity branch")

    def test_manual_nodeinfo_standard_fields_shared_sequence_guards_and_no_periodic_adverts(self):
        self.run_case("nodeinfo")
        source = (ROOT / "examples/companion_radio/MeshtextCompanion.cpp").read_text(encoding="utf-8")
        chat = body(source, "bool MyMesh::sendMeshtextText(")
        self.assertNotIn("_mt_short_name", chat)
        self.assertNotIn("node_name", chat)
        self.assertNotIn("encodeNodeInfo", chat)

    def test_first_successful_chat_announces_once_per_channel_with_no_position(self):
        self.run_case("auto-nodeinfo")

    def test_automatic_nodeinfo_waits_for_quiet_tx_gap_with_retry_refresh_and_wrap(self):
        self.run_case("auto-nodeinfo-quiet")

    def test_heard_repeat_retries_identical_packets_bounded_by_existing_settings(self):
        self.run_case("heard-retries")

    def test_only_exact_forwarded_echo_on_the_current_profile_stops_retries(self):
        self.run_case("strict-echo")

    def test_profile_channel_key_identity_and_radio_changes_cancel_stale_delivery(self):
        self.run_case("delivery-lifecycle")

    def test_busy_queue_new_chat_backpressure_and_queued_vs_inflight_echo(self):
        self.run_case("delivery-contention")

    def test_rollover_jitter_origin_path_gates_and_advert_retry_cap(self):
        self.run_case("delivery-timing")

    def test_announcement_alias_settings_key_changes_and_failure_do_not_storm(self):
        self.run_case("announcement-lifecycle")

    def test_advert_name_change_cancels_only_stale_metadata_and_waits_for_new_chat(self):
        self.run_case("advert-name-delivery")

    def test_same_tuple_publish_busy_release_refreshes_without_stealing_external_owner(self):
        self.run_case("readiness-cache")

    def test_rebroadcast_is_opt_in_persisted_atomically_and_legacy_settings_default_off(self):
        self.run_case("rebroadcast-settings")

    def test_rebroadcast_waits_five_exact_airtimes_plus_jitter_and_never_arms_local_metadata(self):
        self.run_case("rebroadcast-timing")

    def test_rebroadcast_duplicates_cancel_before_seen_shortcut_and_inflight_is_not_unsent(self):
        self.run_case("rebroadcast-duplicates")

    def test_rebroadcast_stale_work_expires_or_cancels_and_explicit_chat_has_priority(self):
        self.run_case("rebroadcast-lifecycle")

    def test_rebroadcast_requires_decoded_broadcast_text_capacity_and_uses_secondary_phy(self):
        self.run_case("rebroadcast-guards-phy")

    def test_delivery_service_is_wired_before_radio_processing_and_paused_for_morse(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="utf-8")
        loop = body(source, "void MyMesh::loop()")
        self.assertIn("if (!mesh::morseTimingActive(_radio)) loopMeshtextDelivery();", loop)
        self.assertLess(loop.index("loopMeshtextDelivery();"), loop.index("BaseChatMesh::loop();"))


if __name__ == "__main__":
    unittest.main()
