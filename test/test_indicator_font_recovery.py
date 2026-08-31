#!/usr/bin/env python3

import base64
import hashlib
import re
from pathlib import Path
import unittest
import zlib


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "variants/sensecap_indicator-espnow/IndicatorFontRecoveryPolicy.h"
CLIENT = ROOT / "variants/sensecap_indicator-espnow/IndicatorFontClient.cpp"
DISPLAY = ROOT / "variants/sensecap_indicator-espnow/SCIndicatorDisplay.h"
PROFILE = ROOT / "variants/sensecap_indicator-espnow/platformio.ini"
RP2040 = ROOT / "tools/sensecap_indicator_rp2040/src/main.cpp"
STAGE_V2_PROTOCOL = ROOT / "src/helpers/IndicatorFontStageV2Protocol.h"
ASSET = ROOT / "variants/sensecap_indicator-espnow/sd/ui-font.vlw"


def source(path: Path) -> str:
    return path.read_text()


def function_body(text: str, signature: str, next_signature: str) -> str:
    start = text.index(signature)
    end = text.index(next_signature, start)
    return text[start:end]


def profile_section(name: str) -> str:
    text = source(PROFILE)
    match = re.search(
        rf"^\[{re.escape(name)}\]\n(?P<body>.*?)(?=^\[|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing profile section {name}")
    return match.group("body")


class IndicatorFontRecoveryTest(unittest.TestCase):
    def test_compiled_asset_identity_matches_checked_in_file(self):
        policy = source(POLICY)
        client = source(CLIENT)
        data = ASSET.read_bytes()

        size = int(re.search(r"kAssetSize = (\d+)", policy).group(1))
        crc = int(re.search(r"kAssetCrc32 = 0x([0-9a-f]+)", policy).group(1), 16)
        sha = re.search(r'kAssetSha256\[\] =\s*"([0-9a-f]{64})"', policy).group(1)

        self.assertEqual(len(data), size)
        self.assertEqual(zlib.crc32(data) & 0xFFFFFFFF, crc)
        self.assertEqual(hashlib.sha256(data).hexdigest(), sha)
        digest_initializer = client[
            client.index("EXPECTED_SHA256[32]") : client.index(
                "enum class FontInfoResult"
            )
        ]
        compiled_digest = bytes(
            int(value, 16)
            for value in re.findall(r"0x([0-9a-f]{2})", digest_initializer)
        )
        self.assertEqual(compiled_digest.hex(), sha)

    def test_source_is_immutable_https_and_tls_is_never_disabled(self):
        policy = source(POLICY)
        client = source(CLIENT)
        url_literals = re.findall(
            r'"([^"]*)"',
            policy[policy.index("kAssetUrl") : policy.index("kAssetSize")],
        )
        url = "".join(url_literals)

        self.assertRegex(
            url,
            r"^https://api\.github\.com/repos/mikecarper/MeshCore/git/blobs/"
            r"[0-9a-f]{40}$",
        )
        self.assertNotIn("setInsecure", client)
        self.assertNotIn("client.setCACertBundle(", client)
        self.assertIn("client.setCACert(FONT_TLS_GITHUB_ROOT_CA)", client)
        self.assertIn("Accept: application/vnd.github.raw+json", client)
        self.assertIn("X-GitHub-Api-Version: 2026-03-10", client)
        self.assertIn("parseHttpStatusCode(line, status_code)", client)

        pem_match = re.search(
            r'FONT_TLS_GITHUB_ROOT_CA\[\].*?R"CERT\((.*?)\)CERT";',
            client,
            re.DOTALL,
        )
        self.assertIsNotNone(pem_match)
        pem_lines = [
            line.strip() for line in pem_match.group(1).strip().splitlines()
        ]
        self.assertEqual(pem_lines[0], "-----BEGIN CERTIFICATE-----")
        self.assertEqual(pem_lines[-1], "-----END CERTIFICATE-----")
        der = base64.b64decode("".join(pem_lines[1:-1]), validate=True)
        self.assertEqual(
            hashlib.sha256(der).hexdigest(),
            "c90f26f0fb1b4018b22227519b5ca2b53e2ca5b3be5cf18efe1bef47380c5383",
        )

    def test_wifi_only_recovery_bootstraps_a_bounded_tls_clock(self):
        policy = source(POLICY)
        client = source(CLIENT)
        proof = function_body(
            client, "bool fontTlsClockProofValid", "bool prepareTlsClock"
        )
        callback = function_body(
            client, "void clearFontNtpCallback", "bool fontTlsClockProofValid"
        )
        clock = function_body(
            client, "bool prepareTlsClock", "uint32_t remainingHttpHeaderTimeout"
        )
        response = function_body(
            client, "RecoveryAttemptOutcome openAssetResponse", "bool writeAll"
        )
        transfer = function_body(
            client,
            "RecoveryAttemptOutcome downloadAndInstallAsset",
            "void finishRecoveryFailure",
        )

        self.assertIn("kAssetPublishedEpoch = 1787708237UL", policy)
        self.assertIn("kNtpSyncWaitMillis = 15000UL", policy)
        self.assertIn(
            "fontNtpTimeReceived.store(false, std::memory_order_release)",
            clock,
        )
        self.assertIn("sntp_set_time_sync_notification_cb(noteFontNtpTime)", clock)
        self.assertIn("sntp_set_sync_status(SNTP_SYNC_STATUS_RESET)", clock)
        self.assertIn('configTime(0, 0, "time.cloudflare.com"', clock)
        self.assertIn("OperationLease sntp_operation", clock)
        self.assertIn("processWideCoordinator()", clock)
        self.assertIn("if (!sntp_operation.tryAcquire())", clock)
        self.assertNotIn("sntp_set_time_sync_notification_cb(nullptr)", clock)
        self.assertIn("sntp_set_time_sync_notification_cb(nullptr)", callback)
        self.assertIn("processWideCoordinator().owns(generation)", callback)
        self.assertIn(
            "fontNtpTimeReceived.load(std::memory_order_acquire)", proof
        )
        self.assertIn("WiFi.status() == WL_CONNECTED", proof)
        self.assertIn("mesh::tls_clock::proofIsValid", proof)
        self.assertIn("mesh::tls_clock::proofAgeIsValid", proof)
        self.assertIn("mesh::tls_clock::proofGenerationIsValid", proof)
        self.assertIn("FONT_TLS_PROOF_MAX_AGE_MS = 300000UL", client)
        self.assertIn("const time_t now = time(nullptr)", clock)
        self.assertIn("fontTlsClockProofValid(now)", clock)
        self.assertIn(
            "now >= (time_t)mesh::indicator_font::kAssetPublishedEpoch", clock
        )
        self.assertNotIn("(uint32_t)time(nullptr)", clock)
        self.assertIn("WiFi.status() != WL_CONNECTED", clock)
        self.assertNotIn("alreadyPlausible", clock)
        self.assertTrue(clock.rstrip().endswith("}"))
        self.assertIn("return false;", clock)
        self.assertIn("prepareTlsClock()", transfer)
        self.assertLess(
            transfer.index("prepareTlsClock()"),
            transfer.index("openAssetResponse("),
        )
        self.assertIn("const time_t tls_now = time(nullptr)", response)
        proof_check = response.index("fontTlsClockProofValid(tls_now)")
        tls_connect = response.index("client.connect(host, 443)")
        self.assertLess(proof_check, tls_connect)
        self.assertIn(
            "std::atomic<bool> fontNtpTimeReceived{false}", client
        )
        self.assertIn(
            "fontNtpTimeReceived.store(true, std::memory_order_release)",
            client,
        )

    def test_release_source_has_no_forced_download_interruption(self):
        client = source(CLIENT)
        self.assertNotIn("DOWNLOAD_TEST_INTERRUPT_AT", client)
        self.assertNotIn("test hook: closing initial HTTP response", client)

    def test_http_headers_have_one_total_deadline(self):
        client = source(CLIENT)
        response = function_body(
            client, "RecoveryAttemptOutcome openAssetResponse", "bool writeAll"
        )
        self.assertIn("HTTP_HEADER_TOTAL_TIMEOUT_MS = 30000UL", client)
        self.assertIn("remainingHttpHeaderTimeout(headerStarted)", response)
        self.assertNotIn(
            "readHttpLine(client, line, sizeof(line), HTTP_LINE_TIMEOUT_MS)",
            response,
        )

    def test_http_wire_parser_is_bounded_and_rejects_ambiguous_framing(self):
        client = source(CLIENT)
        reader = function_body(
            client, "HttpLineResult readHttpLine", "bool isSecurityCriticalHttpHeader"
        )
        decimal = function_body(
            client, "bool parseUnsignedDecimalHeader", "bool parseHttpStatusCode"
        )
        response = function_body(
            client, "RecoveryAttemptOutcome openAssetResponse", "bool writeAll"
        )

        self.assertRegex(client, r"HTTP_HEADER_LIMIT = (?:6[4-9]|[7-9][0-9]|\d{3,})")
        self.assertIn("HTTP_WIRE_LINE_LIMIT = 2048", client)
        drain = reader.index("while (client.available())")
        self.assertIn(
            "millis() - started >= timeout_millis", reader[drain:]
        )
        self.assertIn("++wire_length > HTTP_WIRE_LINE_LIMIT", reader)
        self.assertIn("HttpLineResult::TooLong", reader)

        # Decimal framing accepts digits only, with optional surrounding OWS;
        # strtoul-style signs and overflow are not accepted.
        self.assertIn("*value < '0' || *value > '9'", decimal)
        self.assertIn("UINT64_MAX - digit", decimal)
        self.assertNotIn("strtoul", response)
        self.assertIn("parsed > MAX_FONT_BYTES", response)

        # Reject every obsolete folded field before handling either retained
        # or overflowed extension headers.
        obs_fold = response.index("if (line[0] == ' ' || line[0] == '\\t')")
        overflow = response.index("if (line_result == HttpLineResult::Overflow)")
        self.assertLess(obs_fold, overflow)

    def test_non_200_headers_are_parsed_and_failures_are_classified(self):
        client = source(CLIENT)
        response = function_body(
            client, "RecoveryAttemptOutcome openAssetResponse", "bool writeAll"
        )
        classify = function_body(
            client, "RecoveryAttemptOutcome classifyHttpResponse", "bool splitHttpsUrl"
        )
        finish = function_body(
            client,
            "void finishRecoveryFailure(const RecoveryAttemptOutcome& outcome)",
            "void finishRecoveryFailure()",
        )

        # The status is recorded first, but classification happens only after
        # Retry-After and X-RateLimit-Reset have been read from the full block.
        self.assertIn('"Retry-After:"', response)
        self.assertIn('"X-RateLimit-Reset:"', response)
        self.assertGreater(
            response.index("classifyHttpResponse("),
            response.index("while (header_count <= HTTP_HEADER_LIMIT)"),
        )
        self.assertNotIn("status_code != 200", response)

        self.assertIn("status_code == 403 || status_code == 429", classify)
        self.assertIn("retry_after_seconds", classify)
        self.assertIn("rate_reset_epoch", classify)
        self.assertIn("HTTP_RATE_LIMIT_MIN_DELAY_MS", classify)
        self.assertIn("HTTP_RATE_LIMIT_FALLBACK_DELAY_MS", classify)
        self.assertIn("RecoveryAttemptDisposition::RateLimited", classify)
        self.assertIn("status_code >= 500 && status_code <= 599", classify)
        self.assertIn("RecoveryAttemptDisposition::RetryableFailure", classify)
        self.assertTrue(
            classify.rstrip().endswith(
                "return recoveryOutcome(RecoveryAttemptDisposition::PermanentFailure);\n}"
            )
        )

        self.assertIn("outcome.retryDelayMillis > delay_millis", finish)
        self.assertIn("outcome.retryDelayMillis", finish)
        self.assertIn(
            "outcome.disposition == RecoveryAttemptDisposition::PermanentFailure",
            finish,
        )
        self.assertIn("recoveryState = RecoveryState::Exhausted", finish)

    def test_valid_current_font_does_not_arm_download(self):
        client = source(CLIENT)
        load = client[client.index("uint8_t* IndicatorFontClient::load") :]
        self.assertIn("if (!loaded.currentAsset)", load)
        self.assertNotIn(
            "armRecovery(mesh::indicator_font::RecoveryNeed::None)", load
        )
        self.assertIn("loaded.currentAsset = loaded.size ==", client)
        self.assertIn("memcmp(digest, EXPECTED_SHA256", client)

    def test_length_and_digest_fail_before_commit(self):
        client = source(CLIENT)
        response = function_body(
            client, "RecoveryAttemptOutcome openAssetResponse", "bool writeAll"
        )
        transfer = function_body(
            client,
            "RecoveryAttemptOutcome downloadAndInstallAsset",
            "void finishRecoveryFailure",
        )

        self.assertIn("mesh::indicator_font::kAssetSize - requested_offset", response)
        self.assertIn("content_length != expected_content_length", response)
        self.assertIn("Transfer-Encoding:", response)
        self.assertIn("Content-Encoding:", response)
        self.assertIn("HTTP_HEADER_LIMIT", response)
        self.assertIn("DOWNLOAD_TOTAL_TIMEOUT_MS", transfer)
        self.assertIn("DOWNLOAD_IDLE_TIMEOUT_MS", transfer)
        self.assertLess(transfer.index("digest_ok"), transfer.index("MCFONT COMMIT"))
        self.assertLess(transfer.index("if (!digest_ok)"), transfer.index("Serial2.begin"))

    def test_range_resume_keeps_one_buffer_and_sha_stream_and_is_strict(self):
        client = source(CLIENT)
        response = function_body(
            client, "RecoveryAttemptOutcome openAssetResponse", "bool writeAll"
        )
        transfer = function_body(
            client,
            "RecoveryAttemptOutcome downloadAndInstallAsset",
            "void finishRecoveryFailure",
        )
        content_range = function_body(
            client, "bool parseContentRangeHeader", "bool parseStrongEtagHeader"
        )
        strong_etag = function_body(
            client, "bool parseStrongEtagHeader", "bool parseHttpStatusCode"
        )

        self.assertIn("DOWNLOAD_MAX_RESUME_RECONNECTS = 2", client)
        self.assertIn("const bool is_resume = expected_etag != nullptr", response)
        self.assertIn('"Range: bytes=%lu-%lu\\r\\nIf-Range: %s\\r\\n"', response)
        self.assertIn("const int expected_status = is_resume ? 206 : 200", response)
        self.assertIn("status_code != expected_status", response)
        self.assertIn("content_length != expected_content_length", response)
        self.assertIn("parseContentRangeHeader", response)
        self.assertIn("parseStrongEtagHeader", response)
        self.assertIn("content_range_first != requested_offset", response)
        self.assertIn(
            "content_range_last != mesh::indicator_font::kAssetSize - 1",
            response,
        )
        self.assertIn(
            "content_range_total != mesh::indicator_font::kAssetSize", response
        )
        self.assertIn("strcmp(response_etag, expected_etag) != 0", response)
        self.assertIn("if (content_range_seen)", response)
        self.assertIn("!etag_seen || !etag_valid", response)
        self.assertIn("transfer_encoding_seen", response)
        self.assertIn("!content_encoding_identity", response)

        # Content-Range and reflected If-Range inputs use strict decimal and
        # quoted-tag grammars; signs, overflow, weak tags, control bytes, and
        # embedded quotes cannot pass or inject another request field.
        self.assertIn('strncasecmp(value, "bytes ", 6)', content_range)
        self.assertIn("parsed_first > parsed_last", content_range)
        self.assertIn("parsed_last >= parsed_total", content_range)
        self.assertIn("parsed_total > MAX_FONT_BYTES", content_range)
        self.assertIn("value[0] != '\"'", strong_etag)
        self.assertIn("value[length - 1] != '\"'", strong_etag)
        self.assertIn("byte < 0x21 || byte > 0x7e || byte == '\"'", strong_etag)

        # One fresh SNTP reply covers the bounded 180-second transfer. Every
        # initial/Range TLS handshake revalidates that proof and the signed
        # wall clock centrally in openAssetResponse().
        self.assertEqual(transfer.count("prepareTlsClock()"), 1)
        proof_check = response.index("fontTlsClockProofValid(tls_now)")
        tls_connect = response.index("client.connect(host, 443)")
        self.assertLess(proof_check, tls_connect)
        self.assertEqual(
            transfer.count("mbedtls_sha256_starts_ret(&sha, 0)"), 1
        )
        self.assertEqual(
            transfer.count("ps_malloc(mesh::indicator_font::kAssetSize)"), 1
        )
        initial = transfer.index("client, 0, nullptr, asset_etag")
        resume = transfer.index("client, received, asset_etag, nullptr, 0")
        self.assertLess(initial, resume)
        self.assertIn(
            "resume_reconnects >= DOWNLOAD_MAX_RESUME_RECONNECTS", transfer
        )
        self.assertIn("discarding %lu unverified bytes", transfer)
        self.assertIn("last_progress = millis()", transfer[resume:])

        # `received` moves only after the complete block has entered the one
        # SHA stream. A failed partial block is overwritten at that boundary.
        update = transfer.index("mbedtls_sha256_update_ret")
        advance = transfer.index("received += wanted", update)
        resume_call = transfer.index("openAssetResponse(", advance)
        self.assertLess(update, advance)
        self.assertLess(advance, resume_call)
        self.assertNotIn("received += filled", transfer)
        self.assertEqual(transfer.count("uint32_t started = millis();"), 1)
        self.assertNotIn("\n    started = millis();", transfer[resume:])
        self.assertLess(transfer.index("digest_ok"), transfer.index("Serial2.begin"))

    def test_download_is_buffered_before_receiver_paced_staging(self):
        client = source(CLIENT)
        protocol = source(STAGE_V2_PROTOCOL)
        transfer = function_body(
            client,
            "RecoveryAttemptOutcome downloadAndInstallAsset",
            "void finishRecoveryFailure",
        )
        self.assertIn("kStageV2ChunkBytes = 512", protocol)
        self.assertIn("ps_malloc(mesh::indicator_font::kAssetSize)", transfer)
        self.assertIn("if (wanted > 16 * 1024) wanted = 16 * 1024", transfer)
        self.assertIn("client.read(asset + received + filled", transfer)
        self.assertLess(transfer.index("client.stop()"), transfer.index("Serial2.begin"))
        self.assertLess(transfer.index("if (!digest_ok)"), transfer.index("Serial2.begin"))
        self.assertIn("free(asset)", transfer)
        self.assertIn("writeAll(Serial2, asset + staged_bytes", transfer)
        self.assertIn("stagedChunkAcknowledged(Serial2, next_staged)", transfer)
        self.assertIn("stageV2ChunkSize(", transfer)
        self.assertIn("advanceStageV2Offset(", transfer)
        self.assertIn("delay(LEGACY_STAGE_PACE_MS)", transfer)

    def test_stage_v2_is_receiver_paced_and_legacy_fallback_is_explicit(self):
        client = source(CLIENT)
        rp = source(RP2040)
        protocol = source(STAGE_V2_PROTOCOL)
        negotiate = function_body(
            client, "bool beginStagedUpload", "bool stagedChunkAcknowledged"
        )
        acknowledge = function_body(
            client,
            "bool stagedChunkAcknowledged",
            "RecoveryAttemptOutcome downloadAndInstallAsset",
        )
        receiver = function_body(
            rp, "void receiveFontStage2", "struct CommandReader"
        )

        self.assertIn("IndicatorFontStageV2Protocol.h", client)
        self.assertIn("IndicatorFontStageV2Protocol.h", rp)
        self.assertIn("kStageV2ChunkBytes = 512", protocol)
        self.assertIn('kStageV2ReadyReply[] = "READY 2 512"', protocol)
        self.assertIn(
            'kStageV2LegacyUnsupportedReply[] = "ERROR COMMAND"', protocol
        )
        self.assertIn('"MCFONT STAGEV2 %lu %08lx %lu"', negotiate)
        self.assertNotIn('"MCFONT STAGE2 ', negotiate)
        self.assertNotIn('"MCFONT STAGE2 ', rp)
        self.assertIn("classifyStageV2BeginReply(replied, reply)", negotiate)
        self.assertIn("StageV2BeginAction::UseAcknowledged", negotiate)
        self.assertIn("StageV2BeginAction::UseLegacy", negotiate)
        # A missing/delayed protocol-2 reply fails closed. Only the old
        # service's explicit unsupported-command response can reach STAGE.
        timeout_failure = negotiate.index("action !=")
        legacy_command = negotiate.index('"MCFONT STAGE %lu %08lx"')
        self.assertLess(timeout_failure, legacy_command)
        self.assertIn("return false;", negotiate[timeout_failure:legacy_command])
        self.assertIn("if (!replied)", negotiate[timeout_failure:legacy_command])

        self.assertIn("parseStageV2Ack(reply, expected_offset)", acknowledge)
        self.assertNotIn("strtoul", acknowledge)

        self.assertIn("RECEIVE_TOTAL_TIMEOUT_MS = 180000", rp)
        self.assertIn("output.print(mesh::indicator_font::kStageV2ReadyReply)", receiver)
        self.assertIn("stageV2ChunkSize(expectedSize, received)", receiver)
        fragment_loop = receiver.index("while (chunkReceived < chunkSize)")
        write = receiver.index("font.write(buffer, chunkSize)")
        ack = receiver.index('output.printf("ACK %lu\\n"')
        self.assertLess(fragment_loop, write)
        self.assertIn("chunkReceived += actual", receiver[fragment_loop:write])
        self.assertLess(write, ack)
        self.assertIn("advanceStageV2Offset(", receiver[write:ack])
        self.assertIn("received = nextReceived", receiver[write:ack])
        checksum = receiver.index("~crc != expectedCrc")
        metadata = receiver.index("writeMetadata(STAGED_META_PATH")
        staged = receiver.index('output.print("STAGED\\n")')
        close = receiver.index("font.close()")
        post_close_deadline = receiver.index(
            "espStage2LastElapsedMs >= RECEIVE_TOTAL_TIMEOUT_MS", close
        )
        failure_handling = receiver.index(
            "if (failure != Stage2Result::Idle)", post_close_deadline
        )
        self.assertLess(ack, checksum)
        self.assertLess(close, post_close_deadline)
        self.assertLess(post_close_deadline, failure_handling)
        self.assertLess(checksum, metadata)
        self.assertLess(metadata, staged)
        self.assertIn(
            '"MCFONT STAGEV2 %lu %lx %lu %n"',
            rp,
        )
        self.assertIn("reader.line[consumed] == 0", rp)

    def test_rp2040_retries_a_transient_sd_mount_for_a_bounded_window(self):
        rp = source(RP2040)
        mount = function_body(rp, "bool mountSdCard", "void serviceSdMount")
        service = function_body(rp, "void serviceSdMount", "void sendInfo")
        setup = function_body(rp, "void setup()", "void loop()")
        loop = rp[rp.index("void loop()") :]

        self.assertIn("SD_MOUNT_RETRY_INTERVAL_MS = 2000", rp)
        self.assertIn("SD_MOUNT_MAX_ATTEMPTS = 30", rp)
        self.assertIn("sdMountAttempts >= SD_MOUNT_MAX_ATTEMPTS", mount)
        self.assertIn("if (sdMountAttempts != 0) SD.end(false)", mount)
        self.assertLess(mount.index("++sdMountAttempts"), mount.index("SD.begin("))
        self.assertIn("sdMountNextAttempt = millis() +", mount)
        self.assertIn("sdMountAttempts >= SD_MOUNT_MAX_ATTEMPTS", service)
        self.assertIn("now - sdMountNextAttempt", service)
        self.assertIn("serviceSdMount(millis())", setup)
        self.assertIn("serviceSdMount(millis())", loop)
        self.assertNotIn("SD.begin(", setup)

    def test_legacy_stage_and_usb_put_have_idle_and_total_deadlines(self):
        rp = source(RP2040)
        receive = function_body(rp, "void receiveFont(Stream&", "// STAGEV2")

        self.assertIn("const uint32_t started = millis()", receive)
        self.assertIn("now - started >= RECEIVE_TOTAL_TIMEOUT_MS", receive)
        self.assertIn("now - lastProgress >= RECEIVE_IDLE_TIMEOUT_MS", receive)
        self.assertIn("timedOut = true", receive)
        self.assertGreater(
            receive.index("millis() - started >= RECEIVE_TOTAL_TIMEOUT_MS"),
            receive.index("font.close()"),
        )
        self.assertIn('timedOut ? "ERROR TIMEOUT\\n"', receive)

    def test_interrupted_stage_cannot_replace_live_font(self):
        rp = source(RP2040)
        recovery = function_body(rp, "void recoverFontTransaction", "bool refreshFontInfo")
        mount = function_body(rp, "bool mountSdCard", "void serviceSdMount")
        stage_install = function_body(rp, "bool installStagedPair", "enum class ReceiveMode")
        receive = function_body(rp, "void receiveFont", "struct CommandReader")

        self.assertNotIn("STAGED_FONT_PATH", recovery)
        self.assertNotIn("STAGED_META_PATH", recovery)
        self.assertIn("cleanStagedFiles();", mount)
        self.assertLess(stage_install.index("validatePairCrc"),
                        stage_install.index("installTemporaryPair"))
        self.assertIn("removeIfPresent(destinationFont)", receive)
        self.assertIn("ReceiveMode::StageOnly", receive)
        self.assertIn('output.print("STAGED\\n")', receive)

    def test_interrupted_pair_renames_are_crc_checked_and_recoverable(self):
        rp = source(RP2040)
        validator = function_body(
            rp, "bool validateStoredPairCrc", "void removeIfPresent"
        )
        promotion = function_body(rp, "bool promotePair", "void recoverFontTransaction")
        recovery = function_body(
            rp, "void recoverFontTransaction", "bool refreshFontInfo"
        )

        self.assertIn("validatePairCrc(fontPath, metadataPath, size, crc)", validator)
        for font_path, metadata_path in (
            ("FONT_PATH", "TEMP_META_PATH"),
            ("FONT_PATH", "BACKUP_META_PATH"),
            ("BACKUP_FONT_PATH", "FONT_META_PATH"),
            ("TEMP_FONT_PATH", "TEMP_META_PATH"),
            ("BACKUP_FONT_PATH", "BACKUP_META_PATH"),
        ):
            self.assertIn(
                f"validateStoredPairCrc({font_path}, {metadata_path}", recovery
            )

        # A reset while moving the old pair to backup leaves its metadata at
        # the live name. Complete that backup before trying the new temp pair,
        # so a failed temp promotion still has a rollback candidate.
        split_backup = recovery.index(
            "validateStoredPairCrc(BACKUP_FONT_PATH, FONT_META_PATH"
        )
        temp_promotion = recovery.index(
            "validateStoredPairCrc(TEMP_FONT_PATH, TEMP_META_PATH"
        )
        self.assertLess(split_backup, temp_promotion)
        self.assertIn("SD.rename(FONT_META_PATH, BACKUP_META_PATH)", recovery)

        # Once the source font has moved, a metadata-rename failure must leave
        # the split pair intact. Recovery also stops after trying any CRC-valid
        # candidate so later fallbacks/cleanup cannot erase that retry state.
        self.assertEqual(promotion.count("removeIfPresent(FONT_PATH)"), 1)
        self.assertNotIn(
            "if (SD.rename(sourceMetadata, FONT_META_PATH)) return true;\n"
            "  removeIfPresent(FONT_PATH)",
            promotion,
        )
        self.assertIn(
            "if (validateStoredPairCrc(TEMP_FONT_PATH, TEMP_META_PATH, size, crc))",
            recovery,
        )
        self.assertIn(
            "if (validateStoredPairCrc(BACKUP_FONT_PATH, BACKUP_META_PATH, size, crc))",
            recovery,
        )
        temp_start = recovery.index(
            "if (validateStoredPairCrc(TEMP_FONT_PATH, TEMP_META_PATH"
        )
        backup_start = recovery.index(
            "if (validateStoredPairCrc(BACKUP_FONT_PATH, BACKUP_META_PATH"
        )
        cleanup_start = recovery.index("removeIfPresent(FONT_PATH)", backup_start)
        for block in (
            recovery[temp_start:backup_start],
            recovery[backup_start:cleanup_start],
        ):
            self.assertIn("promotePair", block)
            self.assertIn("return;", block)

        # Keep the ordinary healthy-boot INFO path fast; full-file CRC reads
        # are reserved for abnormal transaction candidates.
        healthy = recovery.index("validatePair(FONT_PATH, FONT_META_PATH")
        first_transaction_crc = recovery.index("validateStoredPairCrc(")
        self.assertLess(healthy, first_transaction_crc)

    def test_internal_uart_commit_is_separate_from_usb_put(self):
        rp = source(RP2040)
        self.assertIn(
            "CommandReader usbCommands = {Serial, Serial, true, false, false}", rp
        )
        self.assertIn(
            "CommandReader espCommands = {Serial1, Serial1, false, true, true}", rp
        )
        self.assertIn("if (!reader.allowStagedUpload)", rp)
        self.assertIn("if (!reader.allowUpload)", rp)

    def test_retry_budget_and_backoff_are_bounded(self):
        policy = source(POLICY)
        client = source(CLIENT)
        self.assertIn("kMaximumAttemptsPerBoot = 4", policy)
        self.assertIn("30000UL", policy)
        self.assertIn("120000UL", policy)
        self.assertIn("600000UL", policy)
        self.assertIn(
            "recoveryAttempts >= mesh::indicator_font::kMaximumAttemptsPerBoot",
            client,
        )
        self.assertIn("RecoveryState::Exhausted", client)
        self.assertIn("WiFi.status() == WL_CONNECTED", client)

    def test_initial_unavailable_service_gets_bounded_post_wifi_reprobe(self):
        policy = source(POLICY)
        client = source(CLIENT)
        load = function_body(
            client,
            "uint8_t* IndicatorFontClient::load",
            "uint8_t* IndicatorFontClient::serviceRecovery",
        )
        probe = function_body(client, "void serviceProbeTask", "void recoveryTask")
        service = function_body(
            client,
            "uint8_t* IndicatorFontClient::serviceRecovery",
            "void IndicatorFontClient::noteRuntimeFontInstalled",
        )

        self.assertIn("armServiceProbe();", load)
        self.assertIn("RecoveryState::ProbeWaiting", service)
        self.assertIn("WiFi.status() == WL_CONNECTED", service)
        self.assertIn("++serviceProbeAttempts", service)
        self.assertIn("xTaskCreatePinnedToCore(serviceProbeTask", service)

        self.assertIn("LoadedFont loaded = loadFromService();", probe)
        self.assertIn("if (loaded.currentAsset)", probe)
        self.assertIn("RecoveryState::Ready", probe)
        self.assertIn("RecoveryNeed::Missing", probe)
        self.assertIn("RecoveryNeed::Corrupt", probe)
        self.assertIn("RecoveryNeed::VersionMismatch, true", probe)
        self.assertIn("finishServiceProbeFailure();", probe)

        self.assertIn("kMaximumServiceProbeAttemptsPerBoot = 4", policy)
        self.assertIn("serviceProbeRetryDelayAfter", policy)
        self.assertIn("2000UL", policy)
        self.assertIn("5000UL", policy)
        self.assertIn("15000UL", policy)
        self.assertIn(
            ">= mesh::indicator_font::kMaximumServiceProbeAttemptsPerBoot",
            client,
        )

    def test_acknowledged_commit_uses_bounded_local_reprobe_not_redownload(self):
        policy = source(POLICY)
        client = source(CLIENT)
        transfer = function_body(
            client,
            "RecoveryAttemptOutcome downloadAndInstallAsset",
            "void finishRecoveryFailure",
        )
        arm = function_body(
            client, "void armPostCommitProbe", "enum class HttpLineResult"
        )
        finish = function_body(
            client,
            "void finishPostCommitProbeFailure",
            "void serviceProbeTask",
        )
        probe = function_body(
            client, "void postCommitProbeTask", "void recoveryTask"
        )
        recovery = function_body(
            client, "void recoveryTask", "#endif  // INDICATOR_WIFI_FONT_RECOVERY"
        )
        service = function_body(
            client,
            "uint8_t* IndicatorFontClient::serviceRecovery",
            "void IndicatorFontClient::noteRuntimeFontInstalled",
        )

        # An acknowledged COMMIT is distinct from a network/staging failure.
        # A transient post-commit INFO failure, or an ambiguous/missing COMMIT
        # reply, enters the local-only state. Only an explicit ERROR is allowed
        # to resume network attempts.
        self.assertIn("RecoveryAttemptDisposition::CommittedNeedsProbe", client)
        commit = transfer.index("const bool commitReplied = commandReply(")
        committed = transfer.index("const bool committed =", commit)
        rejected = transfer.index("const bool commitExplicitlyRejected", committed)
        verified = transfer.index("bool postCommitVerified = false", commit)
        probe_outcome = transfer.index(
            "RecoveryAttemptDisposition::CommittedNeedsProbe", verified
        )
        self.assertLess(commit, committed)
        self.assertLess(committed, rejected)
        self.assertLess(rejected, verified)
        self.assertLess(verified, probe_outcome)
        self.assertIn("if (!committed)", transfer[commit:probe_outcome])
        unresolved = transfer[
            transfer.index("if (!committed)", commit) :
            transfer.index("return recoveryOutcome(postCommitVerified", commit)
        ]
        self.assertIn("commitExplicitlyRejected", unresolved)
        self.assertIn("RecoveryAttemptDisposition::RetryableFailure", unresolved)
        self.assertIn("RecoveryAttemptDisposition::CommittedNeedsProbe", unresolved)

        self.assertIn("armPostCommitProbe(activateCommittedLive)", recovery)
        network_failure = function_body(
            client,
            "void finishRecoveryFailure(const RecoveryAttemptOutcome& outcome)",
            "void finishRecoveryFailure()",
        )
        self.assertIn(
            "RecoveryAttemptDisposition::CommittedNeedsProbe",
            network_failure,
        )
        self.assertIn("RecoveryState::Exhausted", network_failure)
        local_load = recovery.index("LoadedFont loaded = loadFromService()")
        local_failure = recovery.index("loaded.data == nullptr", local_load)
        local_success = recovery.index("data = loaded.data", local_failure)
        self.assertIn("armPostCommitProbe(true)", recovery[local_failure:local_success])
        self.assertNotIn(
            "finishRecoveryFailure", recovery[local_failure:local_success]
        )

        # The local path preserves the consumed download budget, needs no
        # Wi-Fi/NTP/TLS, and can only become Ready/Complete or retry/exhaust.
        self.assertNotIn("recoveryAttempts =", arm)
        self.assertIn("RecoveryNeed::None", arm)
        self.assertIn("RecoveryState::PostCommitProbeWaiting", arm)
        info_only = probe[
            probe.index("if (!activateLive)") :
            probe.index("LoadedFont loaded = loadFromService();")
        ]
        self.assertIn("probeServiceInfo(size, crc)", info_only)
        self.assertIn("mesh::indicator_font::kAssetSize", info_only)
        self.assertIn("mesh::indicator_font::kAssetCrc32", info_only)
        self.assertIn("RecoveryState::Complete", info_only)
        self.assertNotIn("ps_malloc", info_only)
        self.assertNotIn("loadFromService", info_only)
        self.assertIn("LoadedFont loaded = loadFromService();", probe)
        self.assertIn("loaded.data != nullptr && loaded.currentAsset", probe)
        self.assertIn("RecoveryState::Ready", probe)
        self.assertIn("RecoveryState::Complete", probe)
        self.assertIn("finishPostCommitProbeFailure();", probe)
        for forbidden in (
            "downloadAndInstallAsset",
            "armRecovery",
            "finishRecoveryFailure",
            "prepareTlsClock",
            "WiFi.status",
        ):
            self.assertNotIn(forbidden, probe)

        self.assertIn(
            "kMaximumPostCommitProbeAttemptsPerBoot = 4", policy
        )
        post_commit_policy = policy[
            policy.index("postCommitProbeRetryDelayAfter") :
            policy.index("constexpr bool deadlineReached")
        ]
        self.assertIn("2000UL", post_commit_policy)
        self.assertIn("5000UL", post_commit_policy)
        self.assertIn("15000UL", post_commit_policy)
        self.assertIn("RecoveryState::Exhausted", finish)
        self.assertIn("RecoveryState::PostCommitProbeWaiting", finish)
        self.assertNotIn("RecoveryState::Waiting", finish)

        post_commit_branch = service[
            service.index("RecoveryState::PostCommitProbeWaiting") :
            service.index("RecoveryState::ProbeWaiting")
        ]
        self.assertNotIn("wifiConnected", post_commit_branch)
        self.assertIn("++postCommitProbeAttempts", post_commit_branch)
        self.assertIn("launchPostCommitProbe = true", post_commit_branch)
        self.assertIn("xTaskCreatePinnedToCore(", service)
        self.assertIn("postCommitProbeTask", service)
        self.assertIn("finishPostCommitProbeFailure();", service)

        maximum = int(
            re.search(
                r"kMaximumPostCommitProbeAttemptsPerBoot = (\d+)", policy
            ).group(1)
        )
        downloads_started = 1
        state = "PostCommitProbeWaiting"
        for attempt in range(1, maximum + 1):
            state = (
                "Exhausted"
                if attempt >= maximum
                else "PostCommitProbeWaiting"
            )
        self.assertEqual(downloads_started, 1)
        self.assertEqual(state, "Exhausted")

    def test_rejected_exact_recovered_font_cannot_redownload(self):
        client = source(CLIENT)
        display = source(DISPLAY)
        rejected = function_body(
            client,
            "void IndicatorFontClient::noteRecoveredFontInvalid",
            "#endif",
        )
        service = function_body(
            display,
            "void serviceFontRecovery",
            "void turnOn",
        )

        self.assertIn("noteRecoveredFontInvalid()", service)
        self.assertIn("RecoveryNeed::None", rejected)
        self.assertIn("RecoveryState::Exhausted", rejected)
        self.assertNotIn("armRecovery", rejected)
        self.assertNotIn("finishRecoveryFailure", rejected)
        self.assertNotIn("finishPostCommitProbeFailure", rejected)
        self.assertNotIn("RecoveryState::Waiting", rejected)
        self.assertNotIn("RecoveryState::PostCommitProbeWaiting", rejected)
        self.assertNotIn("recoveryAttempts =", rejected)
        self.assertNotIn("downloadAndInstallAsset", rejected)

    def test_missing_corrupt_and_version_mismatch_are_distinct(self):
        policy = source(POLICY)
        client = source(CLIENT)
        for state in ("Missing", "Corrupt", "VersionMismatch"):
            self.assertIn(state, policy)
            self.assertIn(f"RecoveryNeed::{state}", client)

    def test_live_activation_only_replaces_fallback_font(self):
        policy = source(POLICY)
        client = source(CLIENT)
        display = source(DISPLAY)
        self.assertIn(
            "need == RecoveryNeed::Missing || need == RecoveryNeed::Corrupt",
            policy,
        )
        self.assertIn("!hadRuntimeFont", client)
        self.assertIn("IndicatorFontClient::serviceRecovery", display)
        self.assertIn("installRuntimeFont(fontData, fontSize)", display)
        self.assertIn("noteRuntimeFontInstalled", display)
        self.assertIn("noteRuntimeFontInvalid", display)

    def test_tls_cost_and_global_state_are_limited_to_wifi_indicator_profile(self):
        wifi = profile_section("env:SenseCapIndicator-LoRa_comp_radio_usb_wifi")
        usb = profile_section("env:SenseCapIndicator-LoRa_comp_radio_usb")
        self.assertIn("INDICATOR_WIFI_FONT_RECOVERY=1", wifi)
        self.assertNotIn("board_build.embed_files", wifi)
        self.assertNotIn("INDICATOR_WIFI_FONT_RECOVERY", usb)
        self.assertNotIn("board_build.embed_files", usb)

    def test_core2_timeout_and_worker_stack_fit_internal_tls_buffers(self):
        client = source(CLIENT)
        response = function_body(
            client, "RecoveryAttemptOutcome openAssetResponse", "bool writeAll"
        )
        service = function_body(
            client,
            "uint8_t* IndicatorFontClient::serviceRecovery",
            "void IndicatorFontClient::noteRuntimeFontInstalled",
        )

        self.assertIn("ESP_ARDUINO_VERSION_MAJOR >= 3", response)
        self.assertIn("HTTP_LINE_TIMEOUT_MS / 1000UL", response)
        self.assertIn("RECOVERY_TASK_STACK_BYTES = 8192UL", client)
        self.assertIn("RECOVERY_TASK_STACK_BYTES", service)
        self.assertNotIn('"indicator-font", 24576', service)


if __name__ == "__main__":
    unittest.main()
