#!/usr/bin/env python3
"""Static regression contract for ClientACL-backed login handlers."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROFILES = (
    (ROOT / "examples/simple_repeater/MyMesh.cpp", 4, "perms"),
    (ROOT / "examples/simple_sensor/SensorMesh.cpp", 4, "role_permissions"),
    (ROOT / "examples/simple_room_server/MyMesh.cpp", 8, "perm"),
)


def handler_body(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    marker = "handleLoginReq" if "simple_room_server" not in str(path) else "onAnonDataRecv"
    start = source.index(marker)
    end = source.index("RESP_SERVER_LOGIN_OK", start)
    return source[start:end]


for profile, minimum_anon_length, login_permissions in PROFILES:
    body = handler_body(profile)
    freshness = body.index("authorizeLoginTimestamp")
    allocation = body.index("putClient")
    assert freshness < allocation, (
        f"{profile}: durable replay authorization must precede allocation"
    )
    assert "applySuccessfulClientLogin" in body, f"{profile}: unmasked role transition"
    assert "PERM_ACL_ROLE_MASK" in body, f"{profile}: missing role mask"
    assert "OUT_PATH_FORCE_FLOOD" in body, f"{profile}: flood login destroys force-flood"
    authorization = body[
        freshness : body.index(")", freshness) + 1
    ]
    assert re.search(
        rf"previous_timestamp\s*,\s*{re.escape(login_permissions)}\s*\)",
        authorization,
    ), f"{profile}: replay admission is not bound to the authenticated role"

    source = profile.read_text(encoding="utf-8")
    anon_start = source.index("onAnonDataRecv")
    first_field_read = source.index("memcpy(", anon_start)
    length_guard = source.index(f"len < {minimum_anon_length}", anon_start)
    assert length_guard < first_field_read, (
        f"{profile}: anonymous request fields read before minimum-length guard"
    )

print("client login profile contract: PASS")
