"""Remove a legacy final OTA undefine before PlatformIO parses build flags."""

Import("env")  # noqa: F821

import os
import re


if os.environ.get("MESHCORE_FORCE_LORA_OTA") == "1":
    cleaned = []
    for item in env.get("BUILD_FLAGS", []):  # noqa: F821
        text = re.sub(r"(?:^|\s)-U\s*ENABLE_OTA(?=\s|$)", " ", str(item))
        text = " ".join(text.split())
        if text:
            cleaned.append(text)
    env.Replace(BUILD_FLAGS=cleaned)  # noqa: F821
    print("LoRa OTA overlay: removed legacy -UENABLE_OTA before flag processing")
