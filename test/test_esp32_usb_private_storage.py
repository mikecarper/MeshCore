"""Exercise the ESP32 lock command and flash records with the firmware's crypto library."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/esp32_usb_private_storage"
SPIFFS = ROOT / "test/fixtures/client_acl_spiffs/mocks"


class Esp32UsbPrivateStorage(unittest.TestCase):
    def test_lock_unlock_and_power_loss(self):
        crypto = os.environ.get("MESHCORE_TEST_CRYPTO_DIR")
        library = Path(crypto) if crypto else next((ROOT / ".pio/libdeps").glob("*/Crypto"), None)
        if not library or not (library / "ChaChaPoly.cpp").exists():
            self.skipTest("requires the firmware's PlatformIO Crypto dependency")
        if not shutil.which("c++") or not shutil.which("cc"):
            self.skipTest("requires a host C and C++ compiler")

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            for name in ("FS.h", "SPIFFS.h", "nvs.h", "nvs_flash.h", "Preferences.h", "esp_random.h",
                         "soc/soc_caps.h", "freertos/FreeRTOS.h", "mbedtls/platform_util.h"):
                path = work / name
                path.parent.mkdir(exist_ok=True)
                path.write_text('#include "hal.h"\n')
            (work / "Mesh.h").write_text('#include <Identity.h>\n#define MAX_PATH_SIZE 64\n')
            (work / "Stream.h").write_text("""#pragma once
#include <Arduino.h>
struct Stream {
  size_t readBytes(uint8_t*, size_t); size_t write(const uint8_t*, size_t);
  void print(const char*); void println();
};
""")
            (work / "Utils.h").write_text("""#pragma once
#include <Stream.h>
#define PUB_KEY_SIZE 32
#define PRV_KEY_SIZE 64
#define PATH_HASH_SIZE 1
#define SEED_SIZE 32
namespace mesh {
struct RNG { void random(uint8_t*, size_t); };
struct Utils { static void fromHex(uint8_t*, size_t, const char*);
               static void printHex(Stream&, const uint8_t*, size_t); };
}
""")
            (work / "pgmspace.h").write_text("""#pragma once
#define PROGMEM
#define pgm_read_byte(p) (*(const uint8_t*)(p))
#define pgm_read_word(p) (*(const uint16_t*)(p))
#define pgm_read_dword(p) (*(const uint32_t*)(p))
#define memcpy_P memcpy
""")
            ed = ROOT / "lib/ed25519"
            flags = ["-O1", "-ffunction-sections", "-fdata-sections"]
            subprocess.run(["cc", *flags, "-c", *(ed / f for f in
                            ("keypair.c", "ge.c", "fe.c", "sc.c", "key_exchange.c"))],
                           cwd=work, check=True)
            sources = [ROOT / "src/Identity.cpp", *(ROOT / "src/helpers" / f for f in
                       ("ClientACL.cpp", "IdentityStore.cpp", "ESP32PrivateStorage.cpp", "UsbDataLock.cpp"))]
            sources.extend(library / (f + ".cpp") for f in
                           ("ChaChaPoly", "ChaCha", "Poly1305", "Crypto", "Cipher", "AuthenticatedCipher"))
            binary = work / "private-storage"
            subprocess.run(["c++", "-std=c++17", *flags, "-DESP32", "-DESP32_PLATFORM",
                            "-DARDUINO_ARCH_ESP32", "-DHOST_BUILD", "-I", work, "-I", FIXTURE, "-I", ROOT / "src",
                            "-I", SPIFFS, "-I", library, "-I", ed, "-include", FIXTURE / "hal.h",
                            FIXTURE / "lock.cpp", *sources, *work.glob("*.o"), "-Wl,--gc-sections",
                            "-o", binary], check=True)
            subprocess.run([binary], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
