#!/usr/bin/env python3
"""Verify and run a pinned RAK3401 chain while rejecting unsafe bridges."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import getpass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import zipfile

try:
    from . import lora_ota as ota
except ImportError:
    import lora_ota as ota


# These exact ten package transitions passed directly on the physical target,
# and the resulting endpoint passed independent SWD readback. The host runner
# has received cleanup/recovery fixes since that run and has not itself had a
# new clean end-to-end qualification run. Keep the artifact unpublished and
# normally gated from live use; never invent a release URL.
RELEASE_URL = "unreleased local RAK3401 10-step candidate"
ASSET_NAME = "RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.1.5-fd98bc90.zip"
ASSET_SHA256 = "c0b33f4568985e8b2b8dc99411295907212cf2bad21764b6333d5e0ba298fd61"
CHECKSUM_LIST_SHA256 = "3f8c4af8096b96a4aa6506825c387cc8a06f74d5213a29c9387bd11689546881"
BUNDLE_ROOT_NAME = "RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.1.5-fd98bc90"

# Preserve the physically qualified nine-step chain for exact offline
# recognition and existing live deployments.
PHYSICALLY_QUALIFIED_9_ASSET_SHA256 = (
    "9f80eef191b88833bf4d2e4fea559cf5233ca53f9266ba310d447f37fa445f3a"
)
PHYSICALLY_QUALIFIED_9_CHECKSUM_LIST_SHA256 = (
    "73d96e23237896a3e342fe736be12d94087a813bf09ad609fb55330bbe586055"
)
PHYSICALLY_QUALIFIED_9_ROOT_NAME = (
    "RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.1.02-e742333a"
)

# The accelerated 30-step release is the pinned reconstruction input for the
# compact chain. Keep it available for offline provenance, but do not start a
# new live run with it now that the legacy-ceiling route is nine packages.
SUPERSEDED_30_ASSET_SHA256 = (
    "b2781e02460b200a7c37bfae352bad81618716e550d1d042dca8aa29bfc73c29"
)
SUPERSEDED_30_CHECKSUM_LIST_SHA256 = (
    "1f7add658ae5771451cd66a0e5c58a5e461983c1b36029ef480093ae5d5f1020"
)
SUPERSEDED_30_ROOT_NAME = (
    "RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.01-8c5262c6"
)

# This exact 29-step release completed its full direct physical-board test. It
# is now superseded because its historical targets echo terminal OTA bulk
# packets back into mesh dispatch. Keep it available for offline provenance,
# but require the compact 9-step chain for any new live run.
SUPERSEDED_29_ASSET_SHA256 = (
    "eac67a0be12690b7e22c4d1f6a15bfdeb5bd627c4850b246b1be4220e5607b34"
)
SUPERSEDED_29_CHECKSUM_LIST_SHA256 = (
    "8097d75c5d11b9e32e3ebd4971068bf743eaa108054ab39eb0049016f89d185d"
)
SUPERSEDED_29_ROOT_NAME = (
    "RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.01-cd824765"
)

# This 27-step candidate passed steps 1 and 2, then its test was deliberately
# stopped so the adaptive primary requester could be backported into every
# historical source family. It remains valid for offline diagnosis only.
SUPERSEDED_27_ASSET_SHA256 = (
    "2f81eec1f6cc2f3ab0fc246d7254766582118400f7b7de5e66ef4af582b0c337"
)
SUPERSEDED_27_CHECKSUM_LIST_SHA256 = (
    "8861c6ecb7f9c805f5e87fb422a057f0b9d4b21099ee5dbc8aa96c557f7d87f9"
)

# The second v1.17.01 candidate reached step 16 before its checked CC310 path
# returned an incorrect digest for application flash. Preserve its exact pins
# for offline diagnosis, but never permit it to reach a live device.
KNOWN_FAILED_V11701_STEP16_ASSET_SHA256 = (
    "46c7480ed6bdc2aa01fb23a0f70e34c4012ffdd42b616d07bde66cf66d594630"
)
KNOWN_FAILED_V11701_STEP16_CHECKSUM_LIST_SHA256 = (
    "8c74bbf9ae244f8c32041c68791b2780650c8a9321c23d32206614009739be5f"
)
KNOWN_FAILED_V11701_ROOT_NAME = (
    "RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.01-c96bdd6e"
)

# The first v1.17.01 candidate reused an unsafe source-transition bridge at
# step 15. Keep its hashes recognizable for offline diagnosis, but never allow
# it to reach a live device.
KNOWN_FAILED_V11701_ASSET_SHA256 = (
    "693f08187e42cce72124f01328983965726bfbbb3fef80de503f06c4cbe9256a"
)
KNOWN_FAILED_V11701_CHECKSUM_LIST_SHA256 = (
    "a3bd757db2138fc11be766976295051c017ceb02e0c3a22fe1c4c73e93f30f0a"
)

# Keep the withdrawn bundle recognizable for offline diagnosis. Live use is
# still refused before meshcli, password handling, or any device mutation.
KNOWN_UNSAFE_RELEASE_TAG = (
    "rak3401-mota-v1.16.07-c1caa5ad-to-v1.17.02-c96bdd6e"
)
KNOWN_UNSAFE_ASSET_SHA256 = (
    "a7d20449f87436dbf0b2d273e2798ebcb1e3152ccb2528cff71040ecf105a1df"
)
KNOWN_UNSAFE_CHECKSUM_LIST_SHA256 = (
    "751eb571eab4445c9862fc7f2534ad19d74530ae6105cfed1ca16a91d60a54c1"
)
KNOWN_UNSAFE_ROOT_NAME = (
    "RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.2-c96bdd6e"
)

EXTRACTION_BINDING_FILE = ".source-archive.json"
# Release bundles are small (even the retired 30-step chain is far below
# these ceilings). Bound every untrusted archive/tree operation before it can
# consume arbitrary memory, CPU, or disk.
MAX_BUNDLE_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_BUNDLE_MEMBER_BYTES = 32 * 1024 * 1024
MAX_BUNDLE_MEMBERS = 2048
MAX_BUNDLE_TREE_ENTRIES = 4096
MAX_CHECKSUM_LIST_BYTES = 1024 * 1024
MAX_PROGRESS_BYTES = 1024 * 1024
PINNED_ARCHIVE_CHECKSUMS = {
    ASSET_SHA256: CHECKSUM_LIST_SHA256,
    PHYSICALLY_QUALIFIED_9_ASSET_SHA256: (
        PHYSICALLY_QUALIFIED_9_CHECKSUM_LIST_SHA256
    ),
    SUPERSEDED_30_ASSET_SHA256: SUPERSEDED_30_CHECKSUM_LIST_SHA256,
    SUPERSEDED_29_ASSET_SHA256: SUPERSEDED_29_CHECKSUM_LIST_SHA256,
    SUPERSEDED_27_ASSET_SHA256: SUPERSEDED_27_CHECKSUM_LIST_SHA256,
    KNOWN_FAILED_V11701_ASSET_SHA256: KNOWN_FAILED_V11701_CHECKSUM_LIST_SHA256,
    KNOWN_FAILED_V11701_STEP16_ASSET_SHA256: (
        KNOWN_FAILED_V11701_STEP16_CHECKSUM_LIST_SHA256
    ),
    KNOWN_UNSAFE_ASSET_SHA256: KNOWN_UNSAFE_CHECKSUM_LIST_SHA256,
}

DEFAULT_TARGET_KEY = (
    "63d8df6387eaffd2e25db7d2a8ad967a"
    "65202182a48d681d7e7a9260f917280d"
)
EXPECTED_TARGET_ID = 0x2FA509C1
EXPECTED_HARDWARE = "RAK_3401"
EXPECTED_STEP_COUNT = 10
PHYSICALLY_QUALIFIED_9_STEP_COUNT = 9
EXPECTED_START_VERSION = "1.16.7.0"
EXPECTED_FINAL_VERSION = "1.17.1.5"
PHYSICALLY_QUALIFIED_9_FINAL_VERSION = "1.17.1.02"
SUPERSEDED_FINAL_VERSION = "1.17.1.0"
WATCHDOG_RESET_WAIT_SECONDS = 90
WATCHDOG_STABILITY_WAIT_SECONDS = 90
TRANSFER_SETTINGS_FILE = "target-transfer-settings.json"

# Physical RAK3401 testing proved two retired packages unsafe. The original
# v1.17.02 chain failed at step 6. Its software-SHA replacement then passed
# steps 1 through 14, but the first v1.17.01 candidate failed at step 15 because
# that retained 386ae4a5 bridge still used unchecked CC310 SHA. Keep both
# bundles useful for offline diagnosis, but fail closed before a live
# connection. A prior exact 29-step replacement passed its complete direct
# physical-board run on 14-Aug-2026. The compact 9-step release is exhaustively
# verified offline against the deployed bootloader and completed its exact
# nine-package physical RAK3401 run on 19-Aug-2026.
KNOWN_UNSAFE_STEP = 6
KNOWN_UNSAFE_VERSION = "1.16.8.7"
KNOWN_UNSAFE_IMAGE_SHA256 = (
    "61ced8b63953c614748c2fa1b04c2c01e8eb6626a604f6ef95fd2594d6d8ce71"
)
KNOWN_FAILED_V11701_SAFE_STEP6_IMAGE_SHA256 = (
    "4bab3d2d6f6d3a033713d2db87565cb5f7fabe29b2b902b911724dd602fb7df8"
)
SUPERSEDED_27_STEP6_IMAGE_SHA256 = (
    "16cd12a3cbbb563baf546191dc841b1255f11d3957d8c636366145430e31b617"
)
KNOWN_FAILED_V11701_STEP = 15
KNOWN_FAILED_V11701_VERSION = "1.16.9.111"
KNOWN_FAILED_V11701_STEP15_IMAGE_SHA256 = (
    "e8d9d1bd06217c7fd8d7fd333c6fcfde79000838550aa149b10be12fdc64fccb"
)
KNOWN_FAILED_V11701_SAFE_STEP15_IMAGE_SHA256 = (
    "1124247f65772f11f9527408e51971eb9633ed656206276fa95019275bb8fdd2"
)
SUPERSEDED_27_STEP15_IMAGE_SHA256 = (
    "82880e72dbbae41847ade865da06ed26f90d239a7716b1415cfe729b2d35bd94"
)
KNOWN_FAILED_V11701_STEP16 = 16
KNOWN_FAILED_V11701_STEP16_VERSION = "1.16.9.112"
KNOWN_FAILED_V11701_STEP16_IMAGE_SHA256 = (
    "35211ea70be635376b366a90afc74fcd8a7695f744f9c21a50bc872455ec5b21"
)
SUPERSEDED_27_STEP16_IMAGE_SHA256 = (
    "5fd19be1cda7a0e9bdc9464be78abb238a3db5966538b1a1efdb949e8510e1d9"
)
SUPERSEDED_27_FINAL_IMAGE_SHA256 = (
    "e1376869da043c05792b3458e505e67e818f58d94d3fc050d22ab68578a2f2e8"
)

# Exact anchors for the superseded, physically passed 29-step release.
SUPERSEDED_29_ANCHORS = (
    (1, "8364257a2b3a219905e870fad6fbb2040a96ca4b4bb7201b2867534cc2b45530"),
    (6, "4909b5cd50ca86e00b1583bf9ca50e0fc69808a4bebbec8b5e368304145e5d43"),
    (11, "28ba025251b9cf11376e09c2dc91619d9cc216de9f4998a5eeec7438b689d1c4"),
    (12, "43ee2ae539aca7cbdc032eb142474459c9873a9462846b660fad3565f4ad1290"),
    (13, "011bf1fe6a51d98b8498b4be85b65ce55f9398bcf17d57355362e913a77dd303"),
    (16, "f625e66396658704984aa5b31d2e322087a5c882a63f438e28b9972eeca619ca"),
    (17, "3c38377fcf6de36627018b04ed980a4f73ac4646f0c1ff7eaa25fe246dc837ca"),
    (25, "af25fcc8cf0932d6962958402ab1f8e718720ae34fff26383f45bc85cf277eef"),
    (26, "c9a4887774d4ca8d20f3cec8611ba7ce28611dfedd9bbed624e9578f83a2c85c"),
    (29, "5c8d94bb23e87c23b0374ffb5a46e0c1205d6eebcb9d8bae3be6fda2613f9f79"),
)

# Exact anchors for the compact nine-package release. The first target and
# package are byte-identical to the physically passed baseline; every later
# target is pinned here in addition to the outer and inner bundle checksums.
COMPACT_RELEASE_ANCHORS = (
    (1, "8364257a2b3a219905e870fad6fbb2040a96ca4b4bb7201b2867534cc2b45530"),
    (2, "7031e4d7f883c5d6ba6637eab730d143bd7d0f3c755f09a6c52190f762d23e45"),
    (3, "2a434bc74d4c5c282cf5709dff18c5cceb1d7d69321a2692944df0d8c14d43d1"),
    (4, "0a514424c121febb90c7951859f0f5ccf30ffc5392c3bc3185bc7141bde651ff"),
    (5, "871039ef453faf98b694b14c666773c8c6d5ae159fc7a4573f57feb66bdb60be"),
    (6, "644a8c562080838acac32f47871774f04820511e5595c64d674210aebed6b419"),
    (7, "b81d219393897fb1453594d9ea6983b2695c1b2396b768836af7da58b8576e83"),
    (8, "05ac521daf941b14426360e7ff81b0329f4788b84fe7db9d55f7da58ee336597"),
    (9, "2784e4b645bc3dc198de0b8b18d3d7369cd02eca61cd71c46a51b61854da5345"),
)

# Every target in the exact unreleased ten-package fd98bc90 sequence that
# passed direct physical transitions. These pins bind both offline verification
# and the hidden explicit live-lab override to those exact package bytes; they
# do not qualify later host-runner changes.
CURRENT_10_CANDIDATE_ANCHORS = (
    (1, "8364257a2b3a219905e870fad6fbb2040a96ca4b4bb7201b2867534cc2b45530"),
    (2, "ac5f50e5028378ccfe6ea08bbf32f227f50fdcf5285a7deb866e309fbdd0a88f"),
    (3, "884b5e9355b4585b7a4e079dbb44d2858ec46e2fa7bb0dba47e810db2a82e349"),
    (4, "cd6fe1752f859b9e8648f2cc2b9596962d371445e39854130197d61ce1fff49f"),
    (5, "e826c91480390e4eec8d49a29e8ec0a957c9668a51dffad1ff0bb1d39daf38c2"),
    (6, "8e96913fbacb17f43cebba4aaa3bd99cb6953711744c9e55de5b2e09af846e27"),
    (7, "47ab2282b70afeccd7fbdd0418f60a44c98639fbdd0534b43dce94c2a5af7a6d"),
    (8, "74a319a8744ec3f28c0f73214dcc153960df5c439b31998be2ff05464fccf4d7"),
    (9, "30aea80995def68ddff0671138b9f7269b0aa3dea7271fb7f6570637aae577a0"),
    (10, "31c182c888ceb1135e5afb2376610d93cee2e807b556c838e07fd4486c79d095"),
)

# Exact anchors for every image in the accelerated 30-step release. The outer
# asset and inner checksum-list pins cover every byte as well; the complete
# anchor table keeps structural validation fail-closed if those layers are
# ever reused independently.
PINNED_RELEASE_ANCHORS = (
    (1, "8364257a2b3a219905e870fad6fbb2040a96ca4b4bb7201b2867534cc2b45530"),
    (2, "e98d286b493570b25c361521f7287bcd70ee1ab15f74cb7936582736694dd330"),
    (3, "25ab4bee0eabc782c507311e32a08382cc74c612c8d8570582e2fb9546c2f34c"),
    (4, "149528bec52820f48dda92969a077ec2ba15afce05fbd8c208b9200fcbfa6607"),
    (5, "9512c7aaec3fe2958a28b52e152c85f3f581b8eb2513ab18969c822ada5c545b"),
    (6, "8a1df70b769db9e87d944d7a48300138151ca0c9b37d71ff320778cefe899652"),
    (7, "d2f75a6cdbab4eed1fce6677fbbce933b72f8353c57258c9b0adf1dbcb97da03"),
    (8, "067f92aa93e38cdead54d3d3c76327bf1474ef7b9459d8cb53cd0d8a2d3252ed"),
    (9, "a4ddc5ca224710f07894044c119e66019976e61e2fc4c4f3a0878bcbe5c6be74"),
    (10, "cbb16110f73fbf5835acab7565d1d30305417ad0f9ec5293ca252238024a5cff"),
    (11, "75e5c1f6571f7ffe3c441431de97f4f69ed8b732415b694841b85cf7ab359144"),
    (12, "99e7eb953585a1ff4075a5e375320544a1127089e1f801a793fc65ec021104b1"),
    (13, "9391d954bba77378dd84d04c0aaef344305cab311d71889caedfa48f3c3ea71b"),
    (14, "93c5c9498a1099c79f79d1de48621964c559b3d437d308883ecafa03d2300840"),
    (15, "523eadb3328d74ad3021e89201fab356559b678a7df17a9c10ab3c4ebbd3b615"),
    (16, "c8355b3bfd5327419b06389af5903641a3c4b55bb2084f92e4c5b4cbf34fb09e"),
    (17, "2019e3cb1dcb6d065a3f7f3e8c4c1b9a86338dac125a5bb0e69fe5f0cc79f69f"),
    (18, "69da55eda227ec22e5777f04a08bf9af89e49abc89fbc8312119daa797ceb393"),
    (19, "f20fbcc4a7140e85d81393beebb2df60f343f869905a8cf06ce82c0d52f61275"),
    (20, "5c1d6c8a92722fd88bc37a104b1804814d528b1a04663ffd42dece0d79474278"),
    (21, "5ee82703357377ff55a39d808749133bca0ef101ab0569e71f9fad54a706d570"),
    (22, "036a8614d47e0a532d8092a30958edd0bb28fdb2c23d5b4b773e5e141a054377"),
    (23, "22993f7a228caed4a8d625972d61b5307c528ac2013362199c8747d1348fd2ec"),
    (24, "c0e3192471cb23d2fb6c2df6e97a0adddb659420edfe3637d864c747cfd660da"),
    (25, "a25f3e94b967d88846dfc4664d34ce80ac0108c12cd5c88efe9e4b70a71ec3e5"),
    (26, "37b2687e5f53b82b6785ebcae26d4a465b94d02db4875b5784aae64fcdf6d7d4"),
    (27, "ade7ac8c7195c3e3287c3fdf5a4bd03e548f40f338488249bae45f9f75665be3"),
    (28, "12f4fe3a2bc59c28d11cb241fe669eea228b4db4fb5001f1878585b1320b2fe5"),
    (29, "166b8555b354103bd7a467f9d97cd78dd91a4dd00505c6e2c5ae0e06cc1cdf7f"),
    (30, "a08b5791419410c760f31c26bb45c77d776eb7bbf68fde656e19bcd616a6227d"),
)
KNOWN_UNSAFE_RELEASE_MESSAGE = (
    f"live installation of {KNOWN_UNSAFE_RELEASE_TAG} is disabled: a physical RAK3401 "
    f"test reached step {KNOWN_UNSAFE_STEP} (v{KNOWN_UNSAFE_VERSION}) but "
    "that bridge cannot validate its own EndF and cannot install step 7. "
    "Use --verify-only for artifact inspection. Do not deploy this release; "
    "publish and physically test a corrected chain first."
)
KNOWN_FAILED_V11701_MESSAGE = (
    "live installation of the first v1.17.01 candidate is disabled: a physical "
    f"RAK3401 test passed steps 1-14, but step {KNOWN_FAILED_V11701_STEP} "
    f"(v{KNOWN_FAILED_V11701_VERSION}) booted without a usable EndF because its "
    "unchecked CC310 SHA path failed. Use --verify-only for artifact inspection. "
    "Do not deploy this replaced candidate."
)
KNOWN_FAILED_V11701_STEP16_MESSAGE = (
    "live installation of the second v1.17.01 candidate is disabled: a "
    "physical RAK3401 test passed steps 1-15, but step "
    f"{KNOWN_FAILED_V11701_STEP16} (v{KNOWN_FAILED_V11701_STEP16_VERSION}) "
    "booted without a usable EndF. Its checked CC310 SHA call can report "
    "success while returning a wrong digest for memory-mapped application "
    "flash, so return-code fallback is not sufficient. Use --verify-only for "
    "artifact inspection. Do not deploy this replaced candidate."
)
SUPERSEDED_27_MESSAGE = (
    "live installation of the superseded 27-step candidate is disabled: its "
    "physical test was stopped after step 2 so the adaptive primary requester "
    "could be moved into every historical bridge. Use --verify-only for that "
    "bundle and use the pinned compact release."
)
SUPERSEDED_29_MESSAGE = (
    "live installation of the superseded 29-step release is disabled: that "
    "exact chain passed its direct physical run, but its historical targets "
    "do not consume terminal OTA bulk packets before mesh dispatch. Use "
    "--verify-only for provenance and deploy the compact release."
)
SUPERSEDED_30_MESSAGE = (
    "live installation of the superseded 30-step release is disabled: its "
    "images remain the pinned reconstruction input for the compact chain, "
    "which reaches the requested endpoint in nine packages without changing "
    "the deployed bootloader. Use --verify-only for the older bundle."
)
CURRENT_10_CANDIDATE_MESSAGE = (
    "live installation of the exact fd98bc90 ten-step candidate is disabled: "
    "the ten pinned package transitions completed directly on the physical "
    "RAK3401 and the endpoint passed independent SWD readback, but the current "
    "host runner includes later cleanup/recovery fixes that have not had a new "
    "clean end-to-end physical run. The artifact is also local and unpublished. "
    "Use --verify-only with the explicit local bundle path, or the hidden "
    "controlled-lab override, until the exact artifact and qualification record "
    "are published."
)
class KnownUnsafeReleaseError(ota.OtaError):
    """The pinned artifacts are intact but their live transition is unsafe."""


def require_live_release_safe(
    args: argparse.Namespace,
    steps: list[ChainStep],
) -> None:
    if len(steps) == EXPECTED_STEP_COUNT:
        if all(
            steps[number - 1].target_sha256 == expected_sha256
            for number, expected_sha256 in CURRENT_10_CANDIDATE_ANCHORS
        ):
            if args.accept_test_candidate:
                return
            raise KnownUnsafeReleaseError(CURRENT_10_CANDIDATE_MESSAGE)
        raise KnownUnsafeReleaseError(
            "live installation is disabled: this is an unrecognized variant "
            "of the pinned ten-step candidate"
        )

    if len(steps) == PHYSICALLY_QUALIFIED_9_STEP_COUNT:
        for number, expected_sha256 in COMPACT_RELEASE_ANCHORS:
            if steps[number - 1].target_sha256 != expected_sha256:
                raise KnownUnsafeReleaseError(
                    "live installation is disabled: the compact release has "
                    f"an unrecognized step-{number} image"
                )
        return

    if len(steps) == 30:
        if all(
            steps[number - 1].target_sha256 == expected_sha256
            for number, expected_sha256 in PINNED_RELEASE_ANCHORS
        ):
            raise KnownUnsafeReleaseError(SUPERSEDED_30_MESSAGE)
        raise KnownUnsafeReleaseError(
            "live installation is disabled: this is an unrecognized variant "
            "of the superseded 30-step release"
        )

    if len(steps) == 29:
        if all(
            steps[number - 1].target_sha256 == expected_sha256
            for number, expected_sha256 in SUPERSEDED_29_ANCHORS
        ):
            raise KnownUnsafeReleaseError(SUPERSEDED_29_MESSAGE)
        raise KnownUnsafeReleaseError(
            "live installation is disabled: this is an unrecognized variant "
            "of the superseded 29-step release"
        )

    step6_sha256 = steps[KNOWN_UNSAFE_STEP - 1].target_sha256
    if step6_sha256 == KNOWN_UNSAFE_IMAGE_SHA256:
        raise KnownUnsafeReleaseError(KNOWN_UNSAFE_RELEASE_MESSAGE)
    if step6_sha256 == KNOWN_FAILED_V11701_SAFE_STEP6_IMAGE_SHA256:
        if len(steps) < KNOWN_FAILED_V11701_STEP:
            raise KnownUnsafeReleaseError(
                "live installation is disabled: the failed v1.17.01 chain is "
                "missing its audited step 15"
            )
        step15_sha256 = steps[KNOWN_FAILED_V11701_STEP - 1].target_sha256
        if step15_sha256 == KNOWN_FAILED_V11701_STEP15_IMAGE_SHA256:
            raise KnownUnsafeReleaseError(KNOWN_FAILED_V11701_MESSAGE)
        if step15_sha256 != KNOWN_FAILED_V11701_SAFE_STEP15_IMAGE_SHA256:
            raise KnownUnsafeReleaseError(
                "live installation is disabled: step 15 is not a recognized, "
                "audited RAK3401 SHA-safe bridge image"
            )
        if len(steps) < KNOWN_FAILED_V11701_STEP16:
            raise KnownUnsafeReleaseError(
                "live installation is disabled: the failed v1.17.01 chain is "
                "missing its audited step 16"
            )
        step16_sha256 = steps[KNOWN_FAILED_V11701_STEP16 - 1].target_sha256
        if step16_sha256 == KNOWN_FAILED_V11701_STEP16_IMAGE_SHA256:
            raise KnownUnsafeReleaseError(KNOWN_FAILED_V11701_STEP16_MESSAGE)
        raise KnownUnsafeReleaseError(
            "live installation is disabled: this is an unrecognized replacement "
            "for a physically failed v1.17.01 chain"
        )
    if step6_sha256 == SUPERSEDED_27_STEP6_IMAGE_SHA256:
        if (
            len(steps) == 27
            and steps[14].target_sha256 == SUPERSEDED_27_STEP15_IMAGE_SHA256
            and steps[15].target_sha256 == SUPERSEDED_27_STEP16_IMAGE_SHA256
            and steps[-1].target_sha256 == SUPERSEDED_27_FINAL_IMAGE_SHA256
        ):
            raise KnownUnsafeReleaseError(SUPERSEDED_27_MESSAGE)
        raise KnownUnsafeReleaseError(
            "live installation is disabled: this is an unrecognized variant "
            "of the superseded 27-step candidate"
        )
    raise KnownUnsafeReleaseError(
        "live installation is disabled: step 6 is not a recognized, audited "
        "RAK3401 bridge image"
    )


@dataclass(frozen=True)
class ChainStep:
    number: int
    from_version: str
    to_version: str
    path: Path
    size: int
    base_hash: bytes
    target_sha256: str
    package: ota.MotaInfo


@dataclass(frozen=True)
class TargetTransferSettings:
    rxps_enabled: bool
    rxps_rx_us: int
    rxps_sleep_us: int
    rxps_level: int | None
    rxps_preamble: int | None
    powersaving_enabled: bool
    rxdelay: str
    airtime_factor: str
    ota_hops: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_regular_readonly(path: Path, label: str) -> tuple[int, os.stat_result]:
    """Open a caller-controlled path without following a final symlink."""
    try:
        path_metadata = path.lstat()
    except OSError as exc:
        raise ota.OtaError(f"cannot inspect {label} {path}: {exc}") from exc
    if stat.S_ISLNK(path_metadata.st_mode):
        raise ota.OtaError(f"{label} is a symbolic link: {path}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ota.OtaError(f"cannot open {label} {path}: {exc}") from exc
    metadata = os.fstat(descriptor)
    try:
        current_path_metadata = path.lstat()
    except OSError as exc:
        os.close(descriptor)
        raise ota.OtaError(f"{label} path changed while opening: {path}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or not os.path.samestat(metadata, path_metadata)
        or not os.path.samestat(metadata, current_path_metadata)
    ):
        os.close(descriptor)
        raise ota.OtaError(f"{label} is not one stable regular file: {path}")
    return descriptor, metadata


def sha256_file_limited(path: Path, maximum: int, label: str) -> str:
    descriptor, metadata = _open_regular_readonly(path, label)
    if metadata.st_size > maximum:
        os.close(descriptor)
        raise ota.OtaError(
            f"{label} is {metadata.st_size} bytes; limit is {maximum}: {path}"
        )
    digest = hashlib.sha256()
    total = 0
    with os.fdopen(descriptor, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            total += len(chunk)
            if total > maximum:
                raise ota.OtaError(
                    f"{label} grew beyond its {maximum}-byte limit: {path}"
                )
            digest.update(chunk)
    if total != metadata.st_size:
        raise ota.OtaError(f"{label} changed while it was read: {path}")
    return digest.hexdigest()


def read_regular_bytes_limited(path: Path, maximum: int, label: str) -> bytes:
    descriptor, metadata = _open_regular_readonly(path, label)
    if metadata.st_size > maximum:
        os.close(descriptor)
        raise ota.OtaError(
            f"{label} is {metadata.st_size} bytes; limit is {maximum}: {path}"
        )
    with os.fdopen(descriptor, "rb") as source:
        value = source.read(maximum + 1)
        if len(value) > maximum or source.read(1):
            raise ota.OtaError(f"{label} grew beyond its size limit: {path}")
    if len(value) != metadata.st_size:
        raise ota.OtaError(f"{label} changed while it was read: {path}")
    return value


def freeze_archive(source_path: Path, frozen_path: Path) -> str:
    """Copy and hash one opened archive; all later work uses this snapshot."""
    source_descriptor, metadata = _open_regular_readonly(
        source_path, "release ZIP"
    )
    if metadata.st_size > MAX_BUNDLE_ARCHIVE_BYTES:
        os.close(source_descriptor)
        raise ota.OtaError(
            f"release ZIP is {metadata.st_size} bytes; limit is "
            f"{MAX_BUNDLE_ARCHIVE_BYTES}: {source_path}"
        )
    output_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        output_descriptor = os.open(frozen_path, output_flags, 0o600)
    except OSError:
        os.close(source_descriptor)
        raise
    digest = hashlib.sha256()
    total = 0
    try:
        with (
            os.fdopen(source_descriptor, "rb") as source,
            os.fdopen(output_descriptor, "wb") as output,
        ):
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                total += len(chunk)
                if total > MAX_BUNDLE_ARCHIVE_BYTES:
                    raise ota.OtaError(
                        "release ZIP grew beyond its bounded archive limit"
                    )
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        # fdopen owns both descriptors once the with statement is entered.
        raise
    if total != metadata.st_size:
        raise ota.OtaError("release ZIP changed while it was snapshotted")
    frozen_path.chmod(0o600)
    return digest.hexdigest()


def safe_relative_path(value: str, label: str) -> PurePosixPath:
    if "\\" in value:
        raise ota.OtaError(f"{label} contains a backslash: {value!r}")
    path = PurePosixPath(value.removeprefix("./"))
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise ota.OtaError(f"{label} is not a safe relative path: {value!r}")
    return path


def download_release_asset(destination: Path) -> None:
    if destination.exists():
        actual = sha256_file_limited(
            destination, MAX_BUNDLE_ARCHIVE_BYTES, "cached release ZIP"
        )
        if actual != ASSET_SHA256:
            raise ota.OtaError(
                f"cached release asset has SHA-256 {actual}, expected {ASSET_SHA256}: "
                f"{destination}"
            )
        print(f"[bundle] using verified cached asset {destination}")
        return
    raise ota.OtaError(
        "the exact ten-step candidate is not released; pass its explicit local "
        "ZIP or extracted root with --bundle"
    )


def write_extraction_binding(
    destination: Path,
    archive_sha256: str,
    checksum_sha256: str,
    root_name: str,
) -> None:
    path = destination / EXTRACTION_BINDING_FILE
    ota.write_private_recovery_file(
        path,
        json.dumps(
            {
                "archive_sha256": archive_sha256,
                "checksum_list_sha256": checksum_sha256,
                "root_name": root_name,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def read_extraction_binding(destination: Path) -> dict[str, str] | None:
    path = destination / EXTRACTION_BINDING_FILE
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ota.OtaError(f"invalid bundle extraction binding {path}: {exc}") from exc
    if not isinstance(value, dict) or any(
        not isinstance(value.get(key), str)
        for key in ("archive_sha256", "checksum_list_sha256", "root_name")
    ):
        raise ota.OtaError(f"invalid bundle extraction binding fields: {path}")
    return value


def inspect_bundle_archive(archive_path: Path) -> str:
    """Validate bounded members and identify the archive's one bundle root."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
    except (OSError, zipfile.BadZipFile) as exc:
        raise ota.OtaError(f"cannot read release ZIP {archive_path}: {exc}") from exc

    if len(members) > MAX_BUNDLE_MEMBERS:
        raise ota.OtaError(
            f"release ZIP has {len(members)} members; limit is "
            f"{MAX_BUNDLE_MEMBERS}"
        )

    chain_roots: set[str] = set()
    checksum_roots: set[str] = set()
    member_paths: list[PurePosixPath] = []
    seen_paths: set[PurePosixPath] = set()
    compressed_total = 0
    uncompressed_total = 0
    for member in members:
        relative = safe_relative_path(member.filename.rstrip("/"), "ZIP member")
        if len(relative.as_posix()) > 512 or len(relative.parts) > 32:
            raise ota.OtaError(f"release ZIP member path is too long: {member.filename}")
        if relative in seen_paths:
            raise ota.OtaError(f"release ZIP contains a duplicate member: {relative}")
        seen_paths.add(relative)
        member_paths.append(relative)
        if member.flag_bits & 0x1:
            raise ota.OtaError(f"release ZIP contains an encrypted member: {relative}")
        mode = (member.external_attr >> 16) & 0o170000
        if stat.S_ISLNK(mode):
            raise ota.OtaError(f"release ZIP contains a symbolic link: {member.filename}")
        if mode and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ota.OtaError(f"release ZIP contains a special file: {member.filename}")
        if member.file_size < 0 or member.compress_size < 0:
            raise ota.OtaError(f"release ZIP has an invalid member size: {relative}")
        if member.file_size > MAX_BUNDLE_MEMBER_BYTES:
            raise ota.OtaError(
                f"release ZIP member exceeds {MAX_BUNDLE_MEMBER_BYTES} bytes: "
                f"{relative}"
            )
        compressed_total += member.compress_size
        uncompressed_total += member.file_size
        if compressed_total > MAX_BUNDLE_ARCHIVE_BYTES:
            raise ota.OtaError("release ZIP compressed members exceed the archive limit")
        if uncompressed_total > MAX_BUNDLE_UNCOMPRESSED_BYTES:
            raise ota.OtaError("release ZIP expands beyond the bundle size limit")
        if len(relative.parts) == 2 and relative.parts[1] == "CHAIN.csv":
            chain_roots.add(relative.parts[0])
        if len(relative.parts) == 2 and relative.parts[1] == "SHA256SUMS.txt":
            checksum_roots.add(relative.parts[0])
    roots = chain_roots & checksum_roots
    if len(roots) != 1:
        raise ota.OtaError(
            "release ZIP must contain exactly one top-level bundle root with "
            "CHAIN.csv and SHA256SUMS.txt"
        )
    root_name = next(iter(roots))
    if any(relative.parts[0] != root_name for relative in member_paths):
        raise ota.OtaError(
            "release ZIP contains members outside its single bundle root"
        )
    return root_name


def extract_archive_members(archive_path: Path, staging: Path) -> None:
    """Extract only inspected members with independent streamed byte caps."""
    actual_total = 0
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                relative = safe_relative_path(
                    member.filename.rstrip("/"), "ZIP member"
                )
                destination = staging.joinpath(*relative.parts)
                if member.is_dir():
                    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                member_total = 0
                with (
                    archive.open(member, "r") as source,
                    destination.open("xb") as output,
                ):
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        member_total += len(chunk)
                        actual_total += len(chunk)
                        if member_total > MAX_BUNDLE_MEMBER_BYTES:
                            raise ota.OtaError(
                                f"release ZIP member expanded beyond its limit: {relative}"
                            )
                        if actual_total > MAX_BUNDLE_UNCOMPRESSED_BYTES:
                            raise ota.OtaError(
                                "release ZIP expanded beyond its aggregate limit"
                            )
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                destination.chmod(0o600)
                if member_total != member.file_size:
                    raise ota.OtaError(
                        f"release ZIP member changed size while extracting: {relative}"
                    )
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise ota.OtaError(f"cannot safely extract release ZIP: {exc}") from exc


def extract_bundle(
    archive_path: Path,
    destination: Path,
    archive_sha256: str | None = None,
) -> Path:
    if destination.is_symlink() or (
        destination.exists() and not destination.is_dir()
    ):
        raise ota.OtaError(
            f"bundle extraction destination is not a real directory: {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)

    # Hash, inspect, and extract a uniquely owned snapshot. The caller's ZIP
    # pathname may be replaced after this copy without changing any byte we
    # subsequently trust or extract.
    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}.archive-", dir=destination.parent
    ) as frozen_name:
        frozen_archive = Path(frozen_name) / "release.zip"
        actual_archive_sha256 = freeze_archive(archive_path, frozen_archive)
        if (
            archive_sha256 is not None
            and actual_archive_sha256 != archive_sha256
        ):
            raise ota.OtaError(
                f"release ZIP changed after validation: got {actual_archive_sha256}, "
                f"expected {archive_sha256}"
            )
        return extract_frozen_bundle(
            frozen_archive, destination, actual_archive_sha256
        )


def extract_frozen_bundle(
    archive_path: Path,
    destination: Path,
    actual_archive_sha256: str,
) -> Path:
    """Extract one private, already-hashed archive snapshot."""
    expected_checksum_sha256 = PINNED_ARCHIVE_CHECKSUMS.get(
        actual_archive_sha256
    )
    if expected_checksum_sha256 is None:
        raise ota.OtaError(
            "refusing to extract an archive whose SHA-256 is not pinned: "
            f"{actual_archive_sha256}"
        )
    root_name = inspect_bundle_archive(archive_path)
    expected_root = destination / root_name

    if destination.exists() and any(destination.iterdir()):
        allowed_names = {root_name, EXTRACTION_BINDING_FILE}
        unexpected = sorted(
            entry.name for entry in destination.iterdir()
            if entry.name not in allowed_names
        )
        if unexpected or expected_root.is_symlink() or not expected_root.is_dir():
            raise ota.OtaError(
                "bundle extraction cache does not match this archive; use a "
                f"different work directory (unexpected={unexpected or 'root mismatch'})"
            )
        checksum_path = expected_root / "SHA256SUMS.txt"
        if (
            not checksum_path.is_file()
            or sha256_file_limited(
                checksum_path,
                MAX_CHECKSUM_LIST_BYTES,
                "bundle checksum list",
            ) != expected_checksum_sha256
        ):
            raise ota.OtaError(
                "bundle extraction cache checksum does not match this pinned "
                "archive; use a different work directory"
            )
        binding = read_extraction_binding(destination)
        expected_binding = {
            "archive_sha256": actual_archive_sha256,
            "checksum_list_sha256": expected_checksum_sha256,
            "root_name": root_name,
        }
        if binding is not None and binding != expected_binding:
            raise ota.OtaError(
                "bundle extraction cache is bound to a different archive; use "
                "a different work directory"
            )
        if binding is None:
            # Adopt a legacy cache only after its pinned checksum-list digest
            # proves it is the exact content paired with this archive hash.
            write_extraction_binding(
                destination,
                actual_archive_sha256,
                expected_checksum_sha256,
                root_name,
            )
        return expected_root

    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}.part-", dir=destination.parent
    ) as staging_name:
        staging = Path(staging_name)
        extract_archive_members(archive_path, staging)
        staged_root = staging / root_name
        checksum_path = staged_root / "SHA256SUMS.txt"
        if (
            not staged_root.is_dir()
            or not checksum_path.is_file()
            or sha256_file_limited(
                checksum_path,
                MAX_CHECKSUM_LIST_BYTES,
                "bundle checksum list",
            ) != expected_checksum_sha256
        ):
            raise ota.OtaError(
                "release ZIP checksum-list digest does not match the value "
                "paired with its pinned archive hash"
            )
        if destination.exists():
            destination.rmdir()
        os.replace(staging, destination)
        write_extraction_binding(
            destination,
            actual_archive_sha256,
            expected_checksum_sha256,
            root_name,
        )
    return destination / root_name


def locate_bundle(args: argparse.Namespace, work_dir: Path) -> Path:
    if args.bundle is None:
        archive = work_dir / ASSET_NAME
        download_release_asset(archive)
        return extract_bundle(archive, work_dir / "bundle", ASSET_SHA256)

    supplied = args.bundle.resolve()
    if supplied.is_dir():
        direct = supplied / "CHAIN.csv"
        nested_roots = [
            child for child in supplied.iterdir()
            if child.is_dir()
            and not child.is_symlink()
            and (child / "CHAIN.csv").is_file()
            and (child / "SHA256SUMS.txt").is_file()
        ]
        if direct.is_file():
            return supplied
        if len(nested_roots) == 1:
            return nested_roots[0]
        raise ota.OtaError(f"bundle directory does not contain CHAIN.csv: {supplied}")
    if not supplied.is_file() or supplied.suffix.lower() != ".zip":
        raise ota.OtaError("--bundle must be the pinned release ZIP or its extracted root")
    actual = sha256_file_limited(
        supplied, MAX_BUNDLE_ARCHIVE_BYTES, "release ZIP"
    )
    if actual not in {
        ASSET_SHA256,
        PHYSICALLY_QUALIFIED_9_ASSET_SHA256,
        SUPERSEDED_30_ASSET_SHA256,
        SUPERSEDED_29_ASSET_SHA256,
        SUPERSEDED_27_ASSET_SHA256,
        KNOWN_FAILED_V11701_ASSET_SHA256,
        KNOWN_FAILED_V11701_STEP16_ASSET_SHA256,
        KNOWN_UNSAFE_ASSET_SHA256,
    }:
        raise ota.OtaError(
            f"release ZIP has SHA-256 {actual}, expected a pinned audited asset: {supplied}"
        )
    return extract_bundle(supplied, work_dir / "bundle", actual)


def read_checksum_entries(bundle_root: Path) -> dict[PurePosixPath, str]:
    checksum_path = bundle_root / "SHA256SUMS.txt"
    if not checksum_path.is_file():
        raise ota.OtaError("bundle is missing SHA256SUMS.txt")
    checksum_bytes = read_regular_bytes_limited(
        checksum_path, MAX_CHECKSUM_LIST_BYTES, "bundle checksum list"
    )
    checksum_digest = hashlib.sha256(checksum_bytes).hexdigest()
    expected_lists = {
        CHECKSUM_LIST_SHA256,
        PHYSICALLY_QUALIFIED_9_CHECKSUM_LIST_SHA256,
        SUPERSEDED_30_CHECKSUM_LIST_SHA256,
        SUPERSEDED_29_CHECKSUM_LIST_SHA256,
        SUPERSEDED_27_CHECKSUM_LIST_SHA256,
        KNOWN_FAILED_V11701_CHECKSUM_LIST_SHA256,
        KNOWN_FAILED_V11701_STEP16_CHECKSUM_LIST_SHA256,
        KNOWN_UNSAFE_CHECKSUM_LIST_SHA256,
    }
    if checksum_digest not in expected_lists:
        raise ota.OtaError(
            "bundle checksum list is not the one pinned by this chain runner: "
            f"got {checksum_digest}, expected one of {sorted(expected_lists)}"
        )
    try:
        checksum_text = checksum_bytes.decode("ascii")
    except UnicodeError as exc:
        raise ota.OtaError("bundle checksum list is not ASCII") from exc
    listed: dict[PurePosixPath, str] = {}
    for line_number, raw_line in enumerate(checksum_text.splitlines(), 1):
        if not raw_line.strip():
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", raw_line)
        if match is None:
            raise ota.OtaError(f"invalid SHA256SUMS.txt line {line_number}")
        expected, name = match.groups()
        relative = safe_relative_path(name, "checksum entry")
        if relative in listed:
            raise ota.OtaError(f"duplicate checksum entry: {relative}")
        if len(relative.as_posix()) > 512 or len(relative.parts) > 32:
            raise ota.OtaError(f"checksum entry path is too long: {relative}")
        listed[relative] = expected.lower()
        if len(listed) > MAX_BUNDLE_MEMBERS:
            raise ota.OtaError(
                f"bundle checksum list exceeds {MAX_BUNDLE_MEMBERS} entries"
            )
    return listed


def inventory_bundle_tree(bundle_root: Path) -> dict[PurePosixPath, int]:
    """Inventory an extracted tree without following links or reading payloads."""
    if bundle_root.is_symlink() or not bundle_root.is_dir():
        raise ota.OtaError(f"bundle root is not a real directory: {bundle_root}")
    files: dict[PurePosixPath, int] = {}
    pending: list[tuple[Path, PurePosixPath]] = [(bundle_root, PurePosixPath())]
    entries_seen = 0
    total_bytes = 0
    while pending:
        directory, relative_directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise ota.OtaError(f"cannot inventory bundle directory {directory}: {exc}") from exc
        for entry in entries:
            entries_seen += 1
            if entries_seen > MAX_BUNDLE_TREE_ENTRIES:
                raise ota.OtaError(
                    f"bundle tree exceeds {MAX_BUNDLE_TREE_ENTRIES} entries"
                )
            relative = relative_directory / entry.name
            safe_relative_path(relative.as_posix(), "bundle entry")
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise ota.OtaError(f"cannot inspect bundle entry {entry.path}: {exc}") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise ota.OtaError(f"bundle contains a symbolic link: {entry.path}")
            if stat.S_ISDIR(metadata.st_mode):
                pending.append((Path(entry.path), relative))
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise ota.OtaError(f"bundle contains a non-file entry: {entry.path}")
            limit = (
                MAX_CHECKSUM_LIST_BYTES
                if relative == PurePosixPath("SHA256SUMS.txt")
                else MAX_BUNDLE_MEMBER_BYTES
            )
            if metadata.st_size > limit:
                raise ota.OtaError(
                    f"bundle entry is {metadata.st_size} bytes; limit is "
                    f"{limit}: {relative}"
                )
            total_bytes += metadata.st_size
            if total_bytes > MAX_BUNDLE_UNCOMPRESSED_BYTES:
                raise ota.OtaError("bundle tree exceeds its aggregate byte limit")
            files[relative] = metadata.st_size
    return files


def verify_checksum_list(bundle_root: Path) -> None:
    listed = read_checksum_entries(bundle_root)
    inventory = inventory_bundle_tree(bundle_root)
    checksum_relative = PurePosixPath("SHA256SUMS.txt")
    actual_files = set(inventory) - {checksum_relative}
    listed_files = set(listed)
    if listed_files != actual_files:
        missing = sorted(str(path) for path in actual_files - listed_files)
        extra = sorted(str(path) for path in listed_files - actual_files)
        raise ota.OtaError(
            "bundle checksum coverage mismatch; "
            f"unlisted={missing or 'none'}, nonexistent={extra or 'none'}"
        )

    checked = 0
    for relative, expected in listed.items():
        path = bundle_root.joinpath(*relative.parts)
        actual = sha256_file_limited(
            path, MAX_BUNDLE_MEMBER_BYTES, f"bundle entry {relative}"
        )
        if actual.lower() != expected.lower():
            raise ota.OtaError(
                f"checksum mismatch for {relative}: got {actual}, expected {expected}"
            )
        checked += 1
    print(f"[bundle] verified all {checked} SHA-256 entries")


def require_bundle_work_separation(bundle_root: Path, work_dir: Path) -> None:
    """Keep mutable run state out of the immutable bundle input tree."""
    resolved_bundle = bundle_root.resolve(strict=True)
    resolved_work = work_dir.resolve(strict=True)
    if resolved_work == resolved_bundle or resolved_work.is_relative_to(resolved_bundle):
        raise ota.OtaError(
            "work directory must be outside the supplied bundle root so run "
            "artifacts cannot change the verified input tree"
        )


def copy_regular_file_limited(
    source: Path,
    destination: Path,
    maximum: int,
    label: str,
    *,
    expected_size: int | None = None,
) -> int:
    descriptor, metadata = _open_regular_readonly(source, label)
    if metadata.st_size > maximum:
        os.close(descriptor)
        raise ota.OtaError(
            f"{label} is {metadata.st_size} bytes; limit is {maximum}: {source}"
        )
    if expected_size is not None and metadata.st_size != expected_size:
        os.close(descriptor)
        raise ota.OtaError(
            f"{label} changed size after the bounded bundle inventory"
        )
    copy_limit = metadata.st_size if expected_size is not None else maximum
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    total = 0
    source_name = str(source)
    try:
        with (
            os.fdopen(descriptor, "rb") as input_file,
            destination.open("xb") as output_file,
        ):
            class BoundedReader:
                @property
                def name(self) -> str:
                    return source_name

                def read(self, requested: int = -1) -> bytes:
                    nonlocal total
                    remaining = copy_limit - total
                    if requested < 0:
                        requested = remaining + 1
                    chunk = input_file.read(min(requested, remaining + 1))
                    if len(chunk) > remaining:
                        raise ota.OtaError(f"{label} grew beyond its size limit")
                    total += len(chunk)
                    return chunk

            shutil.copyfileobj(BoundedReader(), output_file, 1024 * 1024)
            output_file.flush()
            os.fsync(output_file.fileno())
    except Exception:
        raise
    if total != metadata.st_size:
        raise ota.OtaError(f"{label} changed while it was copied")
    destination.chmod(0o600)
    return total


def snapshot_verified_bundle(bundle_root: Path, destination_parent: Path) -> Path:
    """Copy an input tree once, then verify and use only the private snapshot."""
    if destination_parent.is_symlink() or not destination_parent.is_dir():
        raise ota.OtaError(
            f"bundle snapshot parent is not a real directory: {destination_parent}"
        )
    resolved_bundle = bundle_root.resolve(strict=True)
    resolved_parent = destination_parent.resolve(strict=True)
    if resolved_parent == resolved_bundle or resolved_parent.is_relative_to(
        resolved_bundle
    ):
        raise ota.OtaError("bundle snapshot must be outside the input bundle tree")
    snapshot_root = destination_parent / bundle_root.name
    snapshot_root.mkdir(mode=0o700)
    try:
        # Coverage and resource checks happen before the first payload byte is
        # copied. Copy only the pinned set (plus the list itself), then verify
        # the private result again to close mutation races during the copy.
        verify_checksum_list(bundle_root)
        listed = read_checksum_entries(bundle_root)
        inventory = inventory_bundle_tree(bundle_root)
        copy_order = [PurePosixPath("SHA256SUMS.txt"), *sorted(listed)]
        copied_total = 0
        for relative in copy_order:
            source = bundle_root.joinpath(*relative.parts)
            destination = snapshot_root.joinpath(*relative.parts)
            limit = (
                MAX_CHECKSUM_LIST_BYTES
                if relative == PurePosixPath("SHA256SUMS.txt")
                else MAX_BUNDLE_MEMBER_BYTES
            )
            expected_size = inventory[relative]
            if copied_total + expected_size > MAX_BUNDLE_UNCOMPRESSED_BYTES:
                raise ota.OtaError("bundle copy exceeds its aggregate byte limit")
            copied_total += copy_regular_file_limited(
                source,
                destination,
                limit,
                f"bundle entry {relative}",
                expected_size=expected_size,
            )
        # This post-copy verification binds every byte used by the runner. If
        # the caller's extracted tree changed before or during the copy, the
        # private snapshot fails closed; later caller mutations are irrelevant.
        verify_checksum_list(snapshot_root)
    except Exception:
        shutil.rmtree(snapshot_root, ignore_errors=True)
        raise
    return snapshot_root


def parse_chain(bundle_root: Path) -> tuple[list[ChainStep], bytes]:
    chain_path = bundle_root / "CHAIN.csv"
    try:
        with chain_path.open(newline="", encoding="ascii") as source:
            rows = list(csv.DictReader(source))
    except OSError as exc:
        raise ota.OtaError(f"cannot read {chain_path}: {exc}") from exc
    if len(rows) not in (
        EXPECTED_STEP_COUNT, PHYSICALLY_QUALIFIED_9_STEP_COUNT, 26, 27, 29, 30
    ):
        raise ota.OtaError(
            f"chain contains {len(rows)} steps, expected {EXPECTED_STEP_COUNT}, "
            f"{PHYSICALLY_QUALIFIED_9_STEP_COUNT}, 26, 27, 29, or 30"
        )

    steps: list[ChainStep] = []
    previous_to: str | None = None
    for expected_number, row in enumerate(rows, 1):
        try:
            number = int(row["step"])
            from_version = row["from_version"]
            to_version = row["to_version"]
            relative = safe_relative_path(row["mota_file"], "mOTA path")
            path = bundle_root.joinpath(*relative.parts)
            size = int(row["mota_size"])
            target_image_size = int(row["target_image_size"])
            base_hash = bytes.fromhex(row["base_body_hash"])
            target_sha256 = row["target_sha256"].lower()
        except (KeyError, TypeError, ValueError) as exc:
            raise ota.OtaError(f"invalid CHAIN.csv row {expected_number}: {exc}") from exc
        if number != expected_number:
            raise ota.OtaError(f"chain step {expected_number} is numbered {number}")
        if previous_to is not None and from_version != previous_to:
            raise ota.OtaError(
                f"chain discontinuity at step {number}: {from_version} follows {previous_to}"
            )
        if not path.is_file() or path.stat().st_size != size:
            raise ota.OtaError(f"chain package size/path mismatch at step {number}: {path}")
        package = ota.parse_mota(path.read_bytes(), path)
        expected_version = ota.parse_version(to_version)
        if expected_version is None or package.fw_version != expected_version:
            raise ota.OtaError(f"step {number} version does not match its mOTA manifest")
        if package.target_id != EXPECTED_TARGET_ID or package.hw_id != EXPECTED_HARDWARE:
            raise ota.OtaError(f"step {number} targets the wrong hardware")
        if package.is_full or package.codec_id != ota.MOTA_CODEC_IN_PLACE:
            raise ota.OtaError(f"step {number} is not an nRF52 in-place delta")
        if package.base_hash != base_hash:
            raise ota.OtaError(f"step {number} base hash does not match CHAIN.csv")
        if package.image_size != target_image_size:
            raise ota.OtaError(f"step {number} target image size does not match CHAIN.csv")
        if package.image_hash.hex() != target_sha256:
            raise ota.OtaError(f"step {number} target SHA-256 does not match CHAIN.csv")
        steps.append(
            ChainStep(
                number, from_version, to_version, path, size, base_hash,
                target_sha256, package,
            )
        )
        previous_to = to_version

    if steps[0].from_version != EXPECTED_START_VERSION:
        raise ota.OtaError("chain has an unexpected starting version")
    if len(steps) == EXPECTED_STEP_COUNT:
        for number, expected_sha256 in CURRENT_10_CANDIDATE_ANCHORS:
            if steps[number - 1].target_sha256 != expected_sha256:
                raise ota.OtaError(
                    f"ten-step candidate step {number} does not match its audited image pin"
                )
        expected_final_version = EXPECTED_FINAL_VERSION
    elif len(steps) == PHYSICALLY_QUALIFIED_9_STEP_COUNT:
        for number, expected_sha256 in COMPACT_RELEASE_ANCHORS:
            if steps[number - 1].target_sha256 != expected_sha256:
                raise ota.OtaError(
                    f"compact release step {number} does not match its audited image pin"
                )
        expected_final_version = PHYSICALLY_QUALIFIED_9_FINAL_VERSION
    elif len(steps) == 30:
        for number, expected_sha256 in PINNED_RELEASE_ANCHORS:
            if steps[number - 1].target_sha256 != expected_sha256:
                raise ota.OtaError(
                    f"superseded 30-step release step {number} does not match its image pin"
                )
        expected_final_version = SUPERSEDED_FINAL_VERSION
    elif len(steps) == 29:
        for number, expected_sha256 in SUPERSEDED_29_ANCHORS:
            if steps[number - 1].target_sha256 != expected_sha256:
                raise ota.OtaError(
                    f"superseded step {number} does not match its audited image pin"
                )
        expected_final_version = SUPERSEDED_FINAL_VERSION
    else:
        step6 = steps[KNOWN_UNSAFE_STEP - 1]
        recognized_step6_hashes = {
            KNOWN_UNSAFE_IMAGE_SHA256,
            KNOWN_FAILED_V11701_SAFE_STEP6_IMAGE_SHA256,
            SUPERSEDED_27_STEP6_IMAGE_SHA256,
        }
        if (
            step6.to_version != KNOWN_UNSAFE_VERSION
            or step6.target_sha256 not in recognized_step6_hashes
        ):
            raise ota.OtaError(
                "bundle does not contain a recognized audited step-6 image; "
                "review and repin the runner before using it"
            )
        if step6.target_sha256 in {
            KNOWN_FAILED_V11701_SAFE_STEP6_IMAGE_SHA256,
            SUPERSEDED_27_STEP6_IMAGE_SHA256,
        }:
            step15 = steps[KNOWN_FAILED_V11701_STEP - 1]
            recognized_step15_hashes = {
                KNOWN_FAILED_V11701_STEP15_IMAGE_SHA256,
                KNOWN_FAILED_V11701_SAFE_STEP15_IMAGE_SHA256,
                SUPERSEDED_27_STEP15_IMAGE_SHA256,
            }
            if (
                step15.to_version != KNOWN_FAILED_V11701_VERSION
                or step15.target_sha256 not in recognized_step15_hashes
            ):
                raise ota.OtaError(
                    "bundle does not contain a recognized audited step-15 image; "
                    "review and repin the runner before using it"
                )
        expected_final_version = (
            "1.17.2.0"
            if step6.target_sha256 == KNOWN_UNSAFE_IMAGE_SHA256
            else SUPERSEDED_FINAL_VERSION
        )
    if steps[-1].to_version != expected_final_version:
        raise ota.OtaError(
            f"chain ends at {steps[-1].to_version}, expected {expected_final_version}"
        )

    final_recovery_dir = bundle_root / "recovery/final"
    recovery_zips = sorted(
        (final_recovery_dir if final_recovery_dir.is_dir() else bundle_root / "recovery")
        .glob("*.zip")
    )
    if len(recovery_zips) != 1:
        raise ota.OtaError("bundle must contain exactly one recovery ZIP")
    with zipfile.ZipFile(recovery_zips[0]) as recovery:
        try:
            final_image = recovery.read("firmware.bin")
        except KeyError as exc:
            raise ota.OtaError("recovery ZIP is missing firmware.bin") from exc
    final_identity = ota.parse_endf(final_image)
    final_version = ota.parse_version(expected_final_version)
    if (
        final_identity.target_id != EXPECTED_TARGET_ID
        or final_identity.hw_id != EXPECTED_HARDWARE
        or final_identity.fw_version != final_version
        or hashlib.sha256(final_image).hexdigest() != steps[-1].target_sha256
    ):
        raise ota.OtaError("final recovery image does not match the chain endpoint")
    return steps, final_identity.body_hash


def verify_motatool(args: argparse.Namespace, steps: list[ChainStep]) -> None:
    ota.require_command(args.motatool, "motatool")
    for step in steps:
        ota.run_checked(
            [args.motatool, "verify", str(step.path)],
            label=f"verify chain step {step.number:02d}",
            timeout=60,
        )
    print(f"[bundle] motatool verified all {len(steps)} containers")


def require_meshcli_version(command: str) -> None:
    ota.require_meshcli_version(command)


def controller_namespace(args: argparse.Namespace, target: str = "pending") -> SimpleNamespace:
    return SimpleNamespace(
        meshcli=args.meshcli,
        controller_serial=args.controller_serial,
        controller_tcp=args.controller_tcp,
        controller_ble=args.controller_ble,
        controller_baud=args.controller_baud,
        reply_timeout=args.reply_timeout,
        target=target,
    )


def resolve_target_by_key(
    controller: ota.Controller,
    key_value: str,
) -> tuple[str, str]:
    key_prefix = key_value.lower().removeprefix("0x")
    if len(key_prefix) < 8 or re.fullmatch(r"[0-9a-f]{8,64}", key_prefix) is None:
        raise ota.OtaError("--target-key must contain 8 to 64 hexadecimal characters")
    matches: list[tuple[str, dict]] = []
    for value in controller._run(["contacts"], "list controller contacts"):
        for public_key, contact in value.items():
            if (
                isinstance(public_key, str)
                and re.fullmatch(r"[0-9a-fA-F]{64}", public_key)
                and public_key.lower().startswith(key_prefix)
                and isinstance(contact, dict)
            ):
                matches.append((public_key.lower(), contact))
    if len(matches) != 1:
        raise ota.OtaError(
            f"target key prefix {key_prefix} matched {len(matches)} controller contacts; need exactly one"
        )
    full_key, contact = matches[0]
    name = contact.get("adv_name")
    if contact.get("type") != 2 or not isinstance(name, str) or not name:
        raise ota.OtaError("target key does not identify a named repeater contact")
    return name, full_key


def source_namespace(args: argparse.Namespace) -> SimpleNamespace:
    return SimpleNamespace(
        source_serial=args.source_serial,
        source_tcp=args.source_tcp,
        source_cli_serial=args.source_cli_serial,
        source_cli_tcp=args.source_cli_tcp,
        source_already_temp=args.source_already_temp,
        source_shares_controller=args.source_shares_controller,
        source_companion_terminal=False,
        temp_values=args.temp_values,
        source_baud=args.source_baud,
        controller_baud=args.controller_baud,
        reply_timeout=args.reply_timeout,
        meshcli=args.meshcli,
    )


def restore_persisted_source_rxps(
    work_dir: Path,
    source_args: SimpleNamespace,
) -> None:
    """Recover the chain-start source preference after a killed nested step."""
    recovery_path = work_dir / ota.SOURCE_RXPS_RECOVERY_FILE
    if not recovery_path.exists() and not recovery_path.is_symlink():
        return
    saved = ota.read_source_rxps_recovery(recovery_path, source_args)
    if not ota.shorten_source_temp_window(source_args):
        raise ota.OtaError(
            "could not prove the OTA source is back on its normal radio before "
            "retiring persisted RXPS recovery"
        )
    current = ota.read_source_rxps(source_args)
    if current == saved:
        print("[rxps] OTA source matches the persisted chain-start setting")
    else:
        ota.restore_source_rxps(source_args, saved)
    ota.retire_source_rxps_recovery(recovery_path)
    print("[rxps] retired completed chain source recovery record")


def query_live_target(
    controller: ota.Controller,
    args: argparse.Namespace,
    target_name: str,
) -> ota.TargetInfo:
    query_args = controller_namespace(args, target_name)
    target = ota.query_target(controller, query_args)
    if target.target_id != EXPECTED_TARGET_ID:
        raise ota.OtaError(
            f"live target ID is {target.target_id:08X}, expected {EXPECTED_TARGET_ID:08X}"
        )
    if target.hw_id != EXPECTED_HARDWARE:
        raise ota.OtaError(
            f"live hardware is {target.hw_id!r}, expected {EXPECTED_HARDWARE!r}"
        )
    if target.platform != "nrf52" or target.nrf_external:
        raise ota.OtaError("live target is not the expected internal-flash nRF52 node")
    if target.bootloader_abi is None or target.bootloader_abi < 2:
        raise ota.OtaError("live target does not report OTAFIX mOTA ABI 2")
    if target.bootloader_codecs is None or not target.bootloader_codecs & (1 << ota.MOTA_CODEC_IN_PLACE):
        raise ota.OtaError("live target bootloader does not support in-place codec 2")
    return target


def version_number(value: str) -> int:
    parsed = ota.parse_version(value)
    if parsed is None:
        raise ota.OtaError(f"could not parse live firmware version {value!r}")
    return parsed


def expected_hash_after(
    steps: list[ChainStep],
    final_body_hash: bytes,
    index: int,
) -> bytes:
    return steps[index + 1].base_hash if index + 1 < len(steps) else final_body_hash


def find_resume_index(
    target: ota.TargetInfo,
    steps: list[ChainStep],
    final_body_hash: bytes,
) -> int:
    if target.base_hash == final_body_hash:
        return len(steps)
    matching_indexes = [
        index for index, step in enumerate(steps)
        if target.base_hash == step.base_hash
    ]
    if len(matching_indexes) == 1:
        return matching_indexes[0]
    if len(matching_indexes) > 1:
        raise ota.OtaError(
            "live body hash occurs at multiple points in the pinned chain"
        )
    raise ota.OtaError(
        f"live body hash {target.base_hash.hex().upper()} is not a recognized "
        "point in this exact chain"
    )


def require_watchdog_state(
    controller: ota.Controller,
    target_name: str,
    expected: str,
) -> str:
    reply = controller.remote_command(target_name, "get system.watchdog")
    if re.fullmatch(rf"\s*>\s*{expected}\s*", reply, re.IGNORECASE) is None:
        raise ota.OtaError(
            f"system watchdog must report `> {expected}`; got: {reply}"
        )
    return reply


def read_target_transfer_settings(
    controller: ota.Controller,
    target_name: str,
) -> TargetTransferSettings:
    rxps = ota.read_remote_rxps(controller, target_name)

    powersaving_reply = controller.remote_command(target_name, "powersaving")
    powersaving_match = re.fullmatch(
        r"\s*>?\s*(on|off)\s*",
        powersaving_reply,
        re.IGNORECASE,
    )
    if powersaving_match is None:
        raise ota.OtaError(
            f"could not read destination CPU power-saving state: {powersaving_reply}"
        )

    rxdelay_reply = controller.remote_command(target_name, "get rxdelay")
    rxdelay_match = re.fullmatch(
        r"\s*>\s*([0-9]+(?:\.[0-9]+)?)\s*",
        rxdelay_reply,
    )
    if rxdelay_match is None:
        raise ota.OtaError(f"could not read destination RX delay: {rxdelay_reply}")

    airtime_reply = controller.remote_command(target_name, "get af")
    airtime_match = re.fullmatch(
        r"\s*>\s*([0-9]+(?:\.[0-9]+)?)\s*",
        airtime_reply,
    )
    if airtime_match is None:
        raise ota.OtaError(
            f"could not read destination airtime factor: {airtime_reply}"
        )
    airtime_factor = float(airtime_match.group(1))
    if not math.isfinite(airtime_factor) or airtime_factor < 0.0:
        raise ota.OtaError(
            f"destination returned an invalid airtime factor: {airtime_reply}"
        )

    return TargetTransferSettings(
        rxps_enabled=rxps.enabled,
        rxps_rx_us=rxps.rx_us,
        rxps_sleep_us=rxps.sleep_us,
        rxps_level=rxps.level,
        rxps_preamble=rxps.preamble,
        powersaving_enabled=powersaving_match.group(1).lower() == "on",
        rxdelay=rxdelay_match.group(1),
        airtime_factor=airtime_match.group(1),
        ota_hops=read_ota_hops(controller, target_name),
    )


def load_or_capture_transfer_settings(
    controller: ota.Controller,
    target_name: str,
    target_key: str,
    work_dir: Path,
) -> TargetTransferSettings:
    path = work_dir / TRANSFER_SETTINGS_FILE
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise ota.OtaError(
                f"saved transfer settings are not a private regular file: {path}"
            )
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ota.OtaError(
                f"saved transfer settings are not private (0600): {path}"
            )
        try:
            saved = json.loads(path.read_text(encoding="ascii"))
            saved_target_key = saved["target_key"]
            if (
                not isinstance(saved_target_key, str)
                or re.fullmatch(r"[0-9a-fA-F]{64}", saved_target_key) is None
            ):
                raise TypeError("saved target key must be a 64-hex string")
            if saved_target_key.lower() != target_key.lower():
                raise ota.OtaError(
                    f"{path} belongs to a different destination public key"
                )
            if not isinstance(saved["rxdelay"], str) or not isinstance(
                saved["airtime_factor"], str
            ):
                raise TypeError("saved delay and airtime values must be strings")
            if not isinstance(saved["rxps_enabled"], bool) or not isinstance(
                saved["powersaving_enabled"], bool
            ):
                raise TypeError("saved power states must be JSON booleans")
            if isinstance(saved["ota_hops"], bool) or not isinstance(
                saved["ota_hops"], int
            ):
                raise TypeError("saved OTA hop reach must be a JSON integer")
            for name in ("rxps_level", "rxps_preamble"):
                value = saved.get(name)
                if value is not None and (
                    isinstance(value, bool) or not isinstance(value, int)
                ):
                    raise TypeError(f"saved {name} must be a JSON integer or null")
            settings = TargetTransferSettings(
                rxps_enabled=saved["rxps_enabled"],
                rxps_rx_us=int(saved["rxps_rx_us"]),
                rxps_sleep_us=int(saved["rxps_sleep_us"]),
                rxps_level=(
                    int(saved["rxps_level"])
                    if saved.get("rxps_level") is not None else None
                ),
                rxps_preamble=(
                    int(saved["rxps_preamble"])
                    if saved.get("rxps_preamble") is not None else None
                ),
                powersaving_enabled=saved["powersaving_enabled"],
                rxdelay=str(saved["rxdelay"]),
                airtime_factor=str(saved["airtime_factor"]),
                ota_hops=saved["ota_hops"],
            )
            rxdelay = float(settings.rxdelay)
            if not math.isfinite(rxdelay) or rxdelay < 0.0:
                raise ValueError("saved RX delay is invalid")
            airtime_factor = float(settings.airtime_factor)
            if not math.isfinite(airtime_factor) or airtime_factor < 0.0:
                raise ValueError("saved airtime factor is invalid")
            if not 0 <= settings.ota_hops <= 8:
                raise ValueError("saved OTA hop reach is invalid")
            if (
                settings.rxps_level is not None
                and not 0 <= settings.rxps_level <= 10
            ):
                raise ValueError("saved RXPS level is invalid")
            if settings.rxps_preamble not in (None, 0, 16, 32):
                raise ValueError("saved RXPS preamble is invalid")
            if not (
                ota.RXPS_MIN_PERIOD_US
                <= settings.rxps_rx_us
                <= ota.RXPS_MAX_PERIOD_US
            ):
                raise ValueError("saved RXPS receive period is invalid")
            if not (
                ota.RXPS_MIN_PERIOD_US
                <= settings.rxps_sleep_us
                <= ota.RXPS_MAX_PERIOD_US
            ):
                raise ValueError("saved RXPS sleep period is invalid")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ota.OtaError(f"invalid saved transfer settings in {path}") from exc
        print(f"[guardrail] loaded original destination settings from {path}")
        return settings

    settings = read_target_transfer_settings(controller, target_name)
    payload = {
        "target_key": target_key.lower(),
        "rxps_enabled": settings.rxps_enabled,
        "rxps_rx_us": settings.rxps_rx_us,
        "rxps_sleep_us": settings.rxps_sleep_us,
        "rxps_level": settings.rxps_level,
        "rxps_preamble": settings.rxps_preamble,
        "powersaving_enabled": settings.powersaving_enabled,
        "rxdelay": settings.rxdelay,
        "airtime_factor": settings.airtime_factor,
        "ota_hops": settings.ota_hops,
    }
    ota.write_private_recovery_file(
        path, json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )
    print(f"[guardrail] saved original destination settings to {path}")
    return settings


def enforce_transfer_guardrails(
    controller: ota.Controller,
    target_name: str,
    *,
    saved: TargetTransferSettings | None = None,
    current_version: str | None = None,
    temp_values: tuple[float, float, int, int, int] | None = None,
    all_participants_support_adaptive_preamble: bool = False,
    legacy_full_airtime: bool = False,
) -> None:
    current = read_target_transfer_settings(controller, target_name)
    if current.powersaving_enabled:
        reply = controller.remote_command(target_name, "powersaving off")
        if re.search(r"\boff\b", reply, re.IGNORECASE) is None:
            raise ota.OtaError(f"target did not disable CPU power saving: {reply}")
    rxps_requested = saved.rxps_enabled if saved is not None else current.rxps_enabled
    rxps_profile = (
        ota.select_rxps_temp_profile(
            current_version,
            temp_values,
            all_participants_support_adaptive_preamble=(
                all_participants_support_adaptive_preamble
            ),
        )
        if rxps_requested and temp_values is not None
        else None
    )
    saved_level = saved.rxps_level if saved is not None else current.rxps_level
    if saved_level is None or not 1 <= saved_level <= 10:
        rxps_profile = None
    expected_rxps = rxps_requested and rxps_profile is not None
    if rxps_requested:
        ota.apply_remote_rxps_policy(
            controller,
            target_name,
            ota.RxpsSettings(
                True,
                saved.rxps_rx_us if saved is not None else current.rxps_rx_us,
                saved.rxps_sleep_us if saved is not None else current.rxps_sleep_us,
                saved.rxps_level if saved is not None else current.rxps_level,
                saved.rxps_preamble if saved is not None else current.rxps_preamble,
            ),
            rxps_profile,
        )
    elif current.rxps_enabled:
        reply = controller.remote_command(target_name, "set radio.rxps off")
        if re.search(r"\boff\b", reply, re.IGNORECASE) is None:
            raise ota.OtaError(f"target did not restore RXPS-off policy: {reply}")
    if abs(float(current.rxdelay)) > 0.0001:
        reply = controller.remote_command(target_name, "set rxdelay 0")
        if not reply.upper().startswith("OK"):
            raise ota.OtaError(f"target did not disable RX flood delay: {reply}")
    if legacy_full_airtime and abs(float(current.airtime_factor)) > 0.0001:
        reply = controller.remote_command(target_name, "set af 0")
        if not reply.upper().startswith("OK"):
            raise ota.OtaError(f"target did not enable full legacy airtime: {reply}")

    verified = read_target_transfer_settings(controller, target_name)
    if (
        verified.powersaving_enabled
        or verified.rxps_enabled != expected_rxps
        or abs(float(verified.rxdelay)) > 0.0001
        or (
            legacy_full_airtime
            and abs(float(verified.airtime_factor)) > 0.0001
        )
    ):
        raise ota.OtaError(
            "destination transfer guardrails did not read back as "
            f"powersaving=off, RXPS={'on' if expected_rxps else 'off'}, rxdelay=0"
            + (", af=0" if legacy_full_airtime else "")
        )
    if rxps_profile is None:
        rxps_detail = "RXPS off (version/preamble gate)"
    else:
        rxps_detail = (
            f"RXPS on (qualified boundary level "
            f"{rxps_profile.boundary_level}, preamble "
            f"{rxps_profile.boundary_preamble})"
        )
    detail = f"{rxps_detail}, rxdelay 0, CPU power saving off"
    if legacy_full_airtime:
        detail += ", legacy airtime factor 0"
    print(f"[guardrail] destination verified: {detail}")


def restore_transfer_settings(
    controller: ota.Controller,
    target_name: str,
    saved: TargetTransferSettings,
) -> None:
    current = read_target_transfer_settings(controller, target_name)
    if abs(float(current.rxdelay) - float(saved.rxdelay)) > 0.0001:
        reply = controller.remote_command(target_name, f"set rxdelay {saved.rxdelay}")
        if not reply.upper().startswith("OK"):
            raise ota.OtaError(f"target did not restore RX flood delay: {reply}")

    ota.restore_remote_rxps(
        controller,
        target_name,
        ota.RxpsSettings(
            saved.rxps_enabled,
            saved.rxps_rx_us,
            saved.rxps_sleep_us,
            saved.rxps_level,
            saved.rxps_preamble,
        ),
    )
    current = read_target_transfer_settings(controller, target_name)

    if abs(float(current.airtime_factor) - float(saved.airtime_factor)) > 0.0001:
        reply = controller.remote_command(
            target_name, f"set af {saved.airtime_factor}"
        )
        if not reply.upper().startswith("OK"):
            raise ota.OtaError(f"target did not restore airtime factor: {reply}")

    if current.ota_hops != saved.ota_hops:
        reply = controller.remote_command(
            target_name, f"ota config hops {saved.ota_hops}"
        )
        if not reply.startswith(f"OK OTA reach = {saved.ota_hops} hop"):
            raise ota.OtaError(f"target did not restore OTA hop policy: {reply}")

    verified = read_target_transfer_settings(controller, target_name)
    if (
        verified.rxps_enabled != saved.rxps_enabled
        or abs(float(verified.rxdelay) - float(saved.rxdelay)) > 0.0001
        or abs(
            float(verified.airtime_factor) - float(saved.airtime_factor)
        ) > 0.0001
        or verified.ota_hops != saved.ota_hops
        or (
            saved.rxps_enabled
            and (
                verified.rxps_rx_us != saved.rxps_rx_us
                or verified.rxps_sleep_us != saved.rxps_sleep_us
                or (
                    saved.rxps_level is not None
                    and (
                        verified.rxps_level != saved.rxps_level
                        or verified.rxps_preamble != saved.rxps_preamble
                    )
                )
            )
        )
    ):
        raise ota.OtaError("destination radio transfer settings did not restore exactly")

    if verified.powersaving_enabled != saved.powersaving_enabled:
        desired = "on" if saved.powersaving_enabled else "off"
        reply = controller.remote_command(target_name, f"powersaving {desired}")
        if re.search(rf"\b{desired}\b", reply, re.IGNORECASE) is None:
            raise ota.OtaError(f"target did not restore CPU power saving: {reply}")

    final = read_target_transfer_settings(controller, target_name)
    if (
        final.rxps_enabled != saved.rxps_enabled
        or final.powersaving_enabled != saved.powersaving_enabled
        or abs(float(final.rxdelay) - float(saved.rxdelay)) > 0.0001
        or abs(float(final.airtime_factor) - float(saved.airtime_factor)) > 0.0001
        or final.ota_hops != saved.ota_hops
        or (
            saved.rxps_enabled
            and (
                final.rxps_rx_us != saved.rxps_rx_us
                or final.rxps_sleep_us != saved.rxps_sleep_us
                or (
                    saved.rxps_level is not None
                    and (
                        final.rxps_level != saved.rxps_level
                        or final.rxps_preamble != saved.rxps_preamble
                    )
                )
            )
        )
    ):
        raise ota.OtaError(
            "destination transfer settings changed during final restoration"
        )
    print("[guardrail] original destination transfer settings restored")


def restore_and_retire_transfer_settings(
    controller: ota.Controller,
    target_name: str,
    saved: TargetTransferSettings,
    work_dir: Path,
) -> None:
    """Restore exactly, then disarm the persistent resume record atomically."""
    restore_transfer_settings(controller, target_name, saved)
    path = work_dir / TRANSFER_SETTINGS_FILE
    ota.retire_private_recovery_file(
        path, "destination transfer-settings recovery record"
    )
    print("[guardrail] retired completed destination settings recovery record")


def read_ota_hops(controller: ota.Controller, target_name: str) -> int:
    reply = controller.remote_command(target_name, "ota config")
    match = re.search(r"\bhops=(\d+)\b", reply)
    if match is None:
        raise ota.OtaError(f"could not read OTA hop policy: {reply}")
    return int(match.group(1))


def enforce_ota_hops(
    controller: ota.Controller,
    target_name: str,
    expected: int,
) -> None:
    current = read_ota_hops(controller, target_name)
    if current != expected:
        reply = controller.remote_command(
            target_name, f"ota config hops {expected}"
        )
        if not reply.startswith(f"OK OTA reach = {expected} hop"):
            raise ota.OtaError(f"target did not accept OTA hop policy: {reply}")
        current = read_ota_hops(controller, target_name)
    if current != expected:
        raise ota.OtaError(
            f"target OTA reach is {current} hops, expected {expected}"
        )
    print(f"[radio] destination OTA reach verified at {current} hops")


def require_rescue_capability(
    controller: ota.Controller,
    target_name: str,
) -> None:
    """Require the guarded no-EndF recovery command before another transition."""
    reply = controller.remote_command(target_name, "ota help")
    if "rescue install <hash16>" not in reply:
        raise ota.OtaError(
            "running bridge does not advertise `ota rescue install <hash16>`; "
            "refusing to expose it to another chain transition"
        )
    print("[rescue] guarded no-EndF recovery command is present")


def require_rescue_capability_before_next_transition(
    controller: ota.Controller,
    target_name: str,
    next_step_index: int,
    step_count: int,
) -> None:
    """Gate a bridge at a chain position before exposing it to another package."""
    if 0 < next_step_index < step_count:
        require_rescue_capability(controller, target_name)


def wait_with_label(seconds: int, label: str) -> None:
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        print(f"[wait] {label}: {int(remaining + 0.999)} seconds remaining", flush=True)
        time.sleep(min(30, remaining))


def prepare_watchdog(
    controller: ota.Controller,
    target_name: str,
) -> None:
    reply = controller.remote_command(target_name, "get system.watchdog")
    if re.fullmatch(r"\s*>\s*on\s*", reply, re.IGNORECASE):
        disable_reply = controller.remote_command(
            target_name, "set system.watchdog off"
        )
        if not disable_reply.lower().startswith("ok - disabled"):
            raise ota.OtaError(f"target did not accept watchdog disable: {disable_reply}")
        print("[watchdog] disabled; do not issue a normal reboot")
        wait_with_label(
            WATCHDOG_RESET_WAIT_SECONDS,
            "waiting for the inherited watchdog reset and reconnection",
        )
        require_watchdog_state(controller, target_name, "off")
    elif re.fullmatch(r"\s*>\s*off\s*", reply, re.IGNORECASE) is None:
        raise ota.OtaError(f"could not determine watchdog state: {reply}")

    wait_with_label(
        WATCHDOG_STABILITY_WAIT_SECONDS,
        "proving stable uptime with the watchdog off",
    )
    require_watchdog_state(controller, target_name, "off")
    controller.remote_command(target_name, "ota self")
    print("[watchdog] off and target remained responsive through the stability window")


def validate_chain_state_paths(work_dir: Path) -> None:
    """Reject reused state paths that could redirect or block chain writes."""
    steps_path = work_dir / "steps"
    progress_path = work_dir / "progress.jsonl"
    for path, expected_kind in (
        (steps_path, "directory"),
        (progress_path, "regular file"),
    ):
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if expected_kind == "directory":
            valid = stat.S_ISDIR(metadata.st_mode)
        else:
            valid = stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1
        if not valid:
            raise ota.OtaError(
                f"chain state path must be a real {expected_kind}: {path}"
            )
        if path == progress_path and metadata.st_size > MAX_PROGRESS_BYTES:
            raise ota.OtaError(
                f"chain progress log exceeds {MAX_PROGRESS_BYTES} bytes: {path}"
            )


def open_steps_directory(work_dir: Path) -> int:
    parent = work_dir / "steps"
    try:
        parent.mkdir(mode=0o700)
    except FileExistsError:
        pass
    try:
        path_metadata = parent.lstat()
    except OSError as exc:
        raise ota.OtaError(f"cannot inspect chain steps path: {parent}") from exc
    if not stat.S_ISDIR(path_metadata.st_mode):
        raise ota.OtaError(f"chain steps path is not a real directory: {parent}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(parent, flags)
    except OSError as exc:
        raise ota.OtaError(f"chain steps path is not a real directory: {parent}") from exc
    metadata = os.fstat(descriptor)
    try:
        current_path_metadata = parent.lstat()
    except OSError as exc:
        os.close(descriptor)
        raise ota.OtaError(f"chain steps path changed while opening: {parent}") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or not os.path.samestat(metadata, path_metadata)
        or not os.path.samestat(metadata, current_path_metadata)
    ):
        os.close(descriptor)
        raise ota.OtaError(f"chain steps path is not one stable directory: {parent}")
    return descriptor


def next_attempt_dir(work_dir: Path, step_number: int) -> Path:
    parent = work_dir / "steps"
    descriptor = open_steps_directory(work_dir)
    try:
        for attempt in range(1, 1000):
            name = f"step-{step_number:02d}-attempt-{attempt:03d}"
            try:
                os.mkdir(name, mode=0o700, dir_fd=descriptor)
            except FileExistsError:
                continue
            # Reserve a private real directory first. lora_ota.main creates
            # its own one-use work directory below it with exist_ok=False.
            return parent / name / "work"
    finally:
        os.close(descriptor)
    raise ota.OtaError(f"too many saved attempts for step {step_number}")


def append_progress(
    work_dir: Path,
    step: ChainStep,
    body_hash: bytes,
) -> None:
    record = {
        "time": datetime.now(timezone.utc).isoformat(),
        "step": step.number,
        "from_version": step.from_version,
        "to_version": step.to_version,
        "body_hash": body_hash.hex().upper(),
    }
    path = work_dir / "progress.jsonl"
    data = (json.dumps(record, sort_keys=True) + "\n").encode("ascii")
    try:
        path_metadata: os.stat_result | None = path.lstat()
    except FileNotFoundError:
        path_metadata = None
    if path_metadata is not None and stat.S_ISLNK(path_metadata.st_mode):
        raise ota.OtaError(f"chain progress path is a symbolic link: {path}")
    flags = (
        os.O_WRONLY
        | os.O_APPEND
        | os.O_CREAT
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ota.OtaError(f"cannot safely open chain progress log {path}: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        try:
            current_path_metadata = path.lstat()
        except OSError as exc:
            raise ota.OtaError(
                f"chain progress path changed while opening: {path}"
            ) from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or not os.path.samestat(metadata, current_path_metadata)
            or (
                path_metadata is not None
                and not os.path.samestat(metadata, path_metadata)
            )
        ):
            raise ota.OtaError(
                f"chain progress path is not a private regular file: {path}"
            )
        if metadata.st_size + len(data) > MAX_PROGRESS_BYTES:
            raise ota.OtaError(
                f"chain progress log would exceed {MAX_PROGRESS_BYTES} bytes"
            )
        if os.write(descriptor, data) != len(data):
            raise ota.OtaError("short write while appending chain progress")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def clear_completed_download(
    controller: ota.Controller,
    target_name: str,
    step: ChainStep,
) -> None:
    """After exact body proof, detach only a visible completed manager session.

    A normal-channel ``no download`` proves that the manager is idle; it does
    not prove that a persistent store is empty.  In particular, the legacy
    internal-flash nRF52 store's ``clear()`` reset only RAM.  Do not turn an
    IDLE ``ota cancel`` acknowledgement into a false durable-erasure claim.
    The next transition explicitly re-adopts, proves, and cancels the previous
    MID on TempRadio before beginning its new pull; a successfully consumed
    final package is inert because OTAFIX has cleared its approval word.
    """
    status = controller.remote_command(target_name, "ota status")
    active_id = ota.download_manifest_id(status)
    if active_id is None:
        if "no download" not in status.lower():
            raise ota.OtaError(
                f"post-install download state is ambiguous after step {step.number}: "
                f"{status}"
            )
        print(
            f"[chain] step {step.number:02d} manager is idle; persistent "
            "staging is not inferred from normal-channel status"
        )
        return
    elif active_id != step.package.manifest_id:
        raise ota.OtaError(
            f"post-install target retained mOTA {active_id} after step "
            f"{step.number}; expected only {step.package.manifest_id}"
        )
    elif "ready to install" not in status.lower():
        raise ota.OtaError(
            f"post-install target still has an active step-{step.number} session: "
            f"{status}"
        )
    reply = controller.remote_command(target_name, "ota cancel")
    if not reply.startswith("OK"):
        raise ota.OtaError(
            f"could not clear completed step-{step.number} staging record: {reply}"
        )
    status = controller.remote_command(target_name, "ota status")
    if "no download" not in status.lower():
        raise ota.OtaError(
            f"completed step-{step.number} staging record remains: {status}"
        )
    print(
        f"[chain] detached completed step-{step.number:02d} manager session; "
        "persistent erasure is not inferred"
    )


def connection_arguments(args: argparse.Namespace) -> list[str]:
    values: list[str] = []
    if args.controller_serial:
        values.extend(["--controller-serial", args.controller_serial])
    elif args.controller_tcp:
        values.extend(["--controller-tcp", args.controller_tcp])
    else:
        values.extend(["--controller-ble", args.controller_ble])

    if args.source_serial:
        values.extend(["--source-serial", args.source_serial])
    else:
        values.extend(["--source-tcp", args.source_tcp])
        if args.source_cli_serial:
            values.extend(["--source-cli-serial", args.source_cli_serial])
        elif args.source_cli_tcp:
            values.extend(["--source-cli-tcp", args.source_cli_tcp])
        elif args.source_already_temp:
            values.append("--source-already-temp")
        if args.source_shares_controller:
            values.append("--source-shares-controller")
    return values


def run_step(
    args: argparse.Namespace,
    target_name: str,
    step: ChainStep,
    previous_step: ChainStep | None,
    expected_body_hash: bytes,
    work_dir: Path,
    controller: ota.Controller,
) -> None:
    command = [
        str(step.path),
        target_name,
        *connection_arguments(args),
        "--controller-baud", str(args.controller_baud),
        "--source-baud", str(args.source_baud),
        "--relay-txdelay", str(args.relay_txdelay),
        "--temp-radio", args.temp_radio,
        "--meshcli", args.meshcli,
        "--motatool", args.motatool,
        "--reply-timeout", str(args.reply_timeout),
        "--discovery-timeout", str(args.discovery_timeout),
        "--discovery-interval", str(args.discovery_interval),
        "--poll-seconds", str(args.poll_seconds),
        "--transfer-timeout-minutes", str(args.transfer_timeout_minutes),
        "--seeder-start-wait", str(args.seeder_start_wait),
        "--reboot-wait", str(args.reboot_wait),
        "--source-rxps-recovery-file",
        str(work_dir / ota.SOURCE_RXPS_RECOVERY_FILE),
        "--work-dir", str(next_attempt_dir(work_dir, step.number)),
        "--require-system-watchdog-off",
        "--expected-installed-body-hash", expected_body_hash.hex().upper(),
        # Some audited bridge binaries retain a later historical runtime
        # version string than their pinned EndF chain version. The exact base
        # hash, target hash, package ID, and ordered step still gate the move.
        "--allow-non-upgrade",
        "--yes",
    ]
    if previous_step is not None:
        command.extend([
            "--clear-completed-manifest", previous_step.package.manifest_id,
            "--clear-completed-on-body-hash", step.base_hash.hex().upper(),
        ])
    if args.debug:
        command.append("--debug")
    for relay in args.relay:
        command.extend(["--relay", relay])
    result = ota.main(command, controller_override=controller)
    if result != 0:
        raise ota.OtaError(f"chain step {step.number} exited with status {result}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the exact local ten-step RAK3401 candidate from c1caa5ad "
            "to fd98bc90. Its exact package transitions passed directly on "
            "hardware, but this later host-runner revision has not had a clean "
            "end-to-end physical rerun and normal live use remains disabled."
        )
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        help="pinned local ZIP or extracted bundle root (required until release)",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path.cwd() / "rak3401-mota-chain-work",
        help="persistent cache, logs, and resume records",
    )
    parser.add_argument("--target-key", default=DEFAULT_TARGET_KEY)
    parser.add_argument(
        "--accept-test-candidate",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    controller = parser.add_mutually_exclusive_group()
    controller.add_argument("--controller-serial")
    controller.add_argument("--controller-tcp")
    controller.add_argument("--controller-ble")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--source-serial")
    source.add_argument("--source-tcp")
    source_cli = parser.add_mutually_exclusive_group()
    source_cli.add_argument("--source-cli-serial")
    source_cli.add_argument("--source-cli-tcp")
    parser.add_argument("--source-already-temp", action="store_true")
    parser.add_argument("--source-shares-controller", action="store_true")

    parser.add_argument(
        "--relay",
        action="append",
        default=[],
        metavar="NAME[=PASSWORD]",
        help="intermediate relay, farthest-to-nearest; repeat for each relay",
    )
    parser.add_argument(
        "--relay-txdelay",
        type=float,
        default=ota.DEFAULT_RELAY_TX_DELAY,
        help="temporary flood txdelay for managed intermediate relays",
    )
    parser.add_argument("--temp-radio", default="909.950,250,5,5,120")
    parser.add_argument(
        "--ota-hops",
        type=int,
        default=3,
        help="destination OTA receive/relay reach to enforce before every step",
    )
    parser.add_argument(
        "--legacy-full-airtime",
        action="store_true",
        help=(
            "temporarily set destination af=0 for legacy TempRadio firmware; "
            "use only where the selected frequency and local rules allow it"
        ),
    )
    parser.add_argument("--controller-baud", type=int, default=115200)
    parser.add_argument("--source-baud", type=int, default=115200)
    parser.add_argument("--meshcli", default="meshcli")
    parser.add_argument("--motatool", default="motatool")
    parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "show redacted motatool/meshcli commands, subprocess status, "
            "stdout, stderr, and timeouts"
        ),
    )
    parser.add_argument("--reply-timeout", type=int, default=45)
    parser.add_argument("--discovery-timeout", type=int, default=180)
    parser.add_argument("--discovery-interval", type=int, default=8)
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=60,
        help="seconds between transfer status checks; sparse checks leave airtime for OTA",
    )
    parser.add_argument("--transfer-timeout-minutes", type=int, default=90)
    parser.add_argument("--seeder-start-wait", type=int, default=5)
    parser.add_argument(
        "--reboot-wait",
        type=int,
        default=ota.DEFAULT_POST_INSTALL_READY_WAIT_SECONDS,
        help=(
            "post-install identity-probe window in seconds; `ota self` is "
            "scheduled every 10 seconds"
        ),
    )
    parser.add_argument("--keep-watchdog-off", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--verify-only",
        action="store_true",
        help="verify the pinned bundle and all mOTA containers without connecting",
    )
    mode.add_argument(
        "--preflight-only",
        action="store_true",
        help="also validate the live source and destination, but change no radio settings",
    )
    parser.add_argument("--yes", action="store_true")
    return parser


def validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.controller_serial is None and args.controller_tcp is None and args.controller_ble is None:
        args.controller_serial = "/dev/ttyACM0"
    if not args.verify_only and args.source_serial is None and args.source_tcp is None:
        parser.error("live operation requires --source-serial or --source-tcp")
    if args.source_serial and (args.source_cli_serial or args.source_cli_tcp):
        parser.error("--source-cli-serial/--source-cli-tcp are only used with --source-tcp")
    if args.source_tcp and not (
        args.source_cli_serial or args.source_cli_tcp or args.source_already_temp
    ):
        parser.error(
            "--source-tcp also needs --source-cli-serial, --source-cli-tcp, or --source-already-temp"
        )
    if args.source_already_temp and not args.source_tcp:
        parser.error("--source-already-temp requires --source-tcp")
    if args.source_already_temp and (
        args.source_cli_serial or args.source_cli_tcp
    ):
        parser.error(
            "--source-already-temp cannot be combined with a managed source CLI"
        )
    if args.source_shares_controller and not (
        args.source_tcp and args.source_cli_tcp
    ):
        parser.error(
            "--source-shares-controller requires --source-tcp and --source-cli-tcp"
        )
    if args.source_shares_controller and args.source_already_temp:
        parser.error(
            "--source-shares-controller and --source-already-temp are mutually exclusive"
        )
    if args.controller_serial and args.source_serial and ota.serial_paths_match(
        args.controller_serial, args.source_serial
    ):
        parser.error("controller and source must be separate nodes/serial ports")
    if args.controller_serial and args.source_cli_serial and ota.serial_paths_match(
        args.controller_serial, args.source_cli_serial
    ):
        parser.error("controller and source CLI must use separate serial ports")
    for name in (
        "controller_baud", "source_baud", "reply_timeout", "discovery_timeout",
        "discovery_interval", "poll_seconds", "transfer_timeout_minutes",
        "seeder_start_wait", "reboot_wait",
    ):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if not math.isfinite(args.relay_txdelay) or not 0.0 <= args.relay_txdelay <= 2.0:
        parser.error("--relay-txdelay must be between 0 and 2")
    if not 0 <= args.ota_hops <= 8:
        parser.error("--ota-hops must be from 0 through 8")
    try:
        temp_values = ota.parse_temp_radio(args.temp_radio)
    except argparse.ArgumentTypeError as exc:
        parser.error(f"--temp-radio: {exc}")
    args.temp_values = temp_values
    remote_setup_seconds = (1 + len(args.relay)) * args.reply_timeout
    required_seconds = (
        remote_setup_seconds
        + (0 if args.source_already_temp or args.source_shares_controller else 30)
        + ota.TEMP_RADIO_SWITCH_DELAY_SECONDS
        + args.seeder_start_wait
        + args.discovery_timeout
        + args.transfer_timeout_minutes * 60
        + ota.adaptive_poll_ceiling(args.poll_seconds)
        + args.reply_timeout * (4 + len(args.relay))
        + len(args.relay) * ota.RELAY_TIMING_COMMANDS_PER_RELAY * args.reply_timeout
    )
    if not args.verify_only and temp_values[4] * 60 <= required_seconds:
        parser.error(
            "TempRadio window is too short for the selected relay count and timeouts"
        )


def confirm_chain(
    args: argparse.Namespace,
    target_name: str,
    full_key: str,
    target: ota.TargetInfo,
    first_index: int,
    steps: list[ChainStep],
) -> None:
    print("\nValidated RAK3401 chain plan:")
    print(f"  bundle      : {args.bundle or RELEASE_URL}")
    print(f"  destination : {target_name}")
    print(f"  public key  : {full_key}")
    print(f"  target      : {target.target_id:08X} hw={target.hw_id}")
    print(f"  running     : {target.current_version} {target.base_hash.hex().upper()}")
    print(
        f"  bootloader  : {target.bootloader_version or 'unknown'} "
        f"(ABI {target.bootloader_abi}, codecs 0x{target.bootloader_codecs:X}; ready)"
    )
    if first_index == len(steps):
        print("  action      : endpoint already installed")
    else:
        print(
            f"  action      : steps {steps[first_index].number}-{steps[-1].number} "
            f"through {steps[-1].to_version}"
        )
    print(f"  TempRadio   : {args.temp_radio}")
    print(f"  relays      : {len(args.relay)}")
    if args.relay:
        print(
            f"  relay timing: rxdelay 0, txdelay "
            f"{ota.format_decimal(args.relay_txdelay)} (saved/restored)"
        )
    print(
        f"  OTA reach   : enforce {args.ota_hops} hops before every step, "
        "then restore"
    )
    print(
        "  guardrails  : save settings; version-gate RXPS, disable rxdelay/CPU "
        "sleep, restore all settings at endpoint"
    )
    if args.legacy_full_airtime:
        print(
            "  full airtime: temporarily set af=0; operator accepts local "
            "duty-cycle responsibility"
        )
    print("  watchdog    : disable, prove stable, gate every install, then re-enable")
    if args.preflight_only:
        print("Live preflight passed; no radio or watchdog settings were changed.")
        return
    if args.yes:
        return
    if not sys.stdin.isatty():
        raise ota.OtaError("non-interactive execution requires --yes")
    answer = input("Continue with the complete RAK3401 chain? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        raise ota.OtaError("cancelled by operator")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    ota.DEBUG = bool(args.debug)
    validate_args(args, parser)
    work_dir = args.work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    validate_chain_state_paths(work_dir)
    snapshot_parent: Path | None = None

    try:
        supplied_bundle_root = locate_bundle(args, work_dir)
        require_bundle_work_separation(supplied_bundle_root, work_dir)
        snapshot_parent = Path(tempfile.mkdtemp(
            prefix=".verified-bundle-", dir=work_dir
        ))
        snapshot_parent.chmod(0o700)
        bundle_root = snapshot_verified_bundle(
            supplied_bundle_root, snapshot_parent
        )
        steps, final_body_hash = parse_chain(bundle_root)
        verify_motatool(args, steps)
        if args.verify_only:
            print(f"Verified pinned bundle: {supplied_bundle_root}")
            if len(steps) == EXPECTED_STEP_COUNT:
                print(
                    "Ten-step candidate verified offline: exact archive/checksum "
                    "pins, all target anchors, zero-filled and erased-workspace "
                    "reconstruction, independent motatool verification, and "
                    "bootloader simulation passed. These exact package transitions "
                    "also passed directly on the RAK3401 and the endpoint passed "
                    "independent SWD readback. This later host-runner revision has "
                    "not had a new clean physical rerun; live use remains gated."
                )
            elif len(steps) == PHYSICALLY_QUALIFIED_9_STEP_COUNT:
                print(
                    "Compact release verified: all 9 transitions passed its exact "
                    "physical RAK3401 run, zero-filled and erased-workspace "
                    "reconstruction, independent motatool verification, and "
                    "deployed/current bootloader simulation."
                )
            elif len(steps) == 30:
                print(f"WARNING: {SUPERSEDED_30_MESSAGE}", file=sys.stderr)
            elif len(steps) == 29:
                print(f"WARNING: {SUPERSEDED_29_MESSAGE}", file=sys.stderr)
            elif steps[KNOWN_UNSAFE_STEP - 1].target_sha256 == KNOWN_UNSAFE_IMAGE_SHA256:
                print(f"WARNING: {KNOWN_UNSAFE_RELEASE_MESSAGE}", file=sys.stderr)
            elif (
                steps[KNOWN_FAILED_V11701_STEP - 1].target_sha256
                == KNOWN_FAILED_V11701_STEP15_IMAGE_SHA256
            ):
                print(f"WARNING: {KNOWN_FAILED_V11701_MESSAGE}", file=sys.stderr)
            elif (
                steps[KNOWN_FAILED_V11701_STEP16 - 1].target_sha256
                == KNOWN_FAILED_V11701_STEP16_IMAGE_SHA256
            ):
                print(
                    f"WARNING: {KNOWN_FAILED_V11701_STEP16_MESSAGE}",
                    file=sys.stderr,
                )
            elif (
                len(steps) == 27
                and steps[-1].target_sha256 == SUPERSEDED_27_FINAL_IMAGE_SHA256
            ):
                print(f"WARNING: {SUPERSEDED_27_MESSAGE}", file=sys.stderr)
            return 0

        # This must precede meshcli, password handling, source preflight, and
        # every radio/watchdog mutation. Structural checks passing does not
        # make either physically failed chain safe to deploy.
        require_live_release_safe(args, steps)

        require_meshcli_version(args.meshcli)
        password = os.environ.get("MESHCORE_ADMIN_PASSWORD", "")
        if not password:
            if not sys.stdin.isatty():
                raise ota.OtaError("set MESHCORE_ADMIN_PASSWORD for non-interactive use")
            password = getpass.getpass("RAK3401 admin password: ")
        if any(character in password for character in "\r\n\0"):
            raise ota.OtaError("admin password contains an unsupported control character")

        source_args = source_namespace(args)
        ota.preflight_source_cli(source_args)
        controller = ota.Controller(controller_namespace(args), password)
        ota.verify_shared_source_identity(controller, source_args)
        target_name, full_key = resolve_target_by_key(controller, args.target_key)
        target = query_live_target(controller, args, target_name)
        first_index = find_resume_index(target, steps, final_body_hash)
        require_rescue_capability_before_next_transition(
            controller, target_name, first_index, len(steps)
        )
        confirm_chain(args, target_name, full_key, target, first_index, steps)
        if args.preflight_only:
            return 0

        if first_index == len(steps):
            restore_persisted_source_rxps(work_dir, source_args)
            transfer_path = work_dir / TRANSFER_SETTINGS_FILE
            if transfer_path.exists():
                transfer_settings = load_or_capture_transfer_settings(
                    controller, target_name, full_key, work_dir
                )
                restore_and_retire_transfer_settings(
                    controller, target_name, transfer_settings, work_dir
                )
            if not args.keep_watchdog_off:
                enabled = controller.remote_command(target_name, "set system.watchdog on")
                if not enabled.lower().startswith("ok - system watchdog enabled"):
                    raise ota.OtaError(f"could not enable system watchdog: {enabled}")
                require_watchdog_state(controller, target_name, "on")
            print("RAK3401 already matches the verified final endpoint.")
            return 0

        transfer_settings = load_or_capture_transfer_settings(
            controller, target_name, full_key, work_dir
        )
        participant_args = source_namespace(args)
        participant_args.relay_values = [
            ota.parse_relay(value, password) for value in args.relay
        ]
        participant_versions = ota.read_lora_ota_participant_versions(
            controller, participant_args, target
        )

        def all_current_participants_support_adaptive_preamble(
            current_target: ota.TargetInfo,
        ) -> bool:
            participant_versions["destination"] = (
                ota.parse_version(current_target.current_version)
                if current_target.current_version else None
            )
            return ota.participants_support_adaptive_preamble(
                participant_versions
            )

        prepare_watchdog(controller, target_name)
        enforce_transfer_guardrails(
            controller,
            target_name,
            saved=transfer_settings,
            current_version=target.current_version,
            temp_values=args.temp_values,
            all_participants_support_adaptive_preamble=(
                all_current_participants_support_adaptive_preamble(target)
            ),
            legacy_full_airtime=args.legacy_full_airtime,
        )
        enforce_ota_hops(controller, target_name, args.ota_hops)
        for index in range(first_index, len(steps)):
            step = steps[index]
            resolved_name, current_key = resolve_target_by_key(controller, args.target_key)
            if current_key != full_key or resolved_name != target_name:
                raise ota.OtaError("target contact identity changed during the chain")
            target = query_live_target(controller, args, target_name)
            current_index = find_resume_index(target, steps, final_body_hash)
            if current_index > index:
                print(f"[chain] step {step.number:02d} is already installed; continuing")
                continue
            if current_index != index:
                raise ota.OtaError(
                    f"live target is at chain index {current_index}, expected {index}"
                )
            require_watchdog_state(controller, target_name, "off")
            enforce_transfer_guardrails(
                controller,
                target_name,
                saved=transfer_settings,
                current_version=target.current_version,
                temp_values=args.temp_values,
                all_participants_support_adaptive_preamble=(
                    all_current_participants_support_adaptive_preamble(target)
                ),
                legacy_full_airtime=args.legacy_full_airtime,
            )
            enforce_ota_hops(controller, target_name, args.ota_hops)
            print(
                f"\n[chain] step {step.number:02d}/{len(steps)}: "
                f"{step.from_version} -> {step.to_version}"
            )
            previous_step = steps[index - 1] if index > 0 else None
            expected_hash = expected_hash_after(steps, final_body_hash, index)
            run_step(
                args, target_name, step, previous_step, expected_hash,
                work_dir, controller
            )

            target = query_live_target(controller, args, target_name)
            if target.base_hash != expected_hash:
                raise ota.OtaError(
                    f"step {step.number} returned body hash {target.base_hash.hex().upper()}, "
                    f"expected {expected_hash.hex().upper()}"
                )
            if (
                target.current_version_source == "ota stats"
                and version_number(target.current_version or "")
                != version_number(step.to_version)
            ):
                raise ota.OtaError(
                    f"step {step.number} EndF metadata reports version "
                    f"{target.current_version}, expected {step.to_version}"
                )
            if (
                target.current_version_source == "ver"
                and version_number(target.current_version or "")
                != version_number(step.to_version)
            ):
                print(
                    f"[chain] step {step.number:02d} exact body hash is verified; "
                    f"runtime label {target.current_version} is historical and is "
                    f"not the EndF chain version {step.to_version}"
                )
            require_rescue_capability_before_next_transition(
                controller, target_name, index + 1, len(steps)
            )
            clear_completed_download(controller, target_name, step)
            require_watchdog_state(controller, target_name, "off")
            append_progress(work_dir, step, target.base_hash)
            print(f"[chain] step {step.number:02d} verified")

        final_target = query_live_target(controller, args, target_name)
        if find_resume_index(final_target, steps, final_body_hash) != len(steps):
            raise ota.OtaError("final target identity did not match the release endpoint")
        restore_persisted_source_rxps(work_dir, source_args)
        restore_and_retire_transfer_settings(
            controller, target_name, transfer_settings, work_dir
        )
        if not args.keep_watchdog_off:
            enabled = controller.remote_command(target_name, "set system.watchdog on")
            if not enabled.lower().startswith("ok - system watchdog enabled"):
                raise ota.OtaError(f"could not enable system watchdog: {enabled}")
            require_watchdog_state(controller, target_name, "on")
            print(f"[watchdog] re-enabled after verified step {len(steps)} boot")
        print(
            f"RAK3401 update complete: {final_target.current_version} "
            f"{final_target.base_hash.hex().upper()}"
        )
        return 0
    except KnownUnsafeReleaseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("No device connection or setting change was attempted.", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print(
            "\nInterrupted. Any partial mOTA remains resumable. The target watchdog "
            "and transfer guardrails may intentionally remain off until the chain completes.",
            file=sys.stderr,
        )
        return 130
    except (ota.OtaError, OSError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "The target watchdog and transfer guardrails may intentionally remain off. "
            "Rerun the same command and work directory to resume and restore the saved "
            "settings, including OTA reach and airtime factor; do not skip a chain step.",
            file=sys.stderr,
        )
        return 2
    finally:
        if snapshot_parent is not None:
            shutil.rmtree(snapshot_parent, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
