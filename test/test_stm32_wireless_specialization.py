"""The STM32WL no-service shortcut must not mask any wireless owner."""
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class Stm32WirelessSpecializationTest(unittest.TestCase):
    def test_only_plain_stm32wl_uses_no_service_handler(self):
        compiler = shutil.which("gcc")
        if not compiler:
            self.skipTest("host GCC required")
        source = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        anchor = source.index("if (handleBeaconCommand(command, reply, 157))")
        start = source.index("#if defined(STM32_PLATFORM) && defined(STM32WLxx)", anchor)
        end = source.index("#endif", start) + len("#endif")
        branch = source[start:end]
        base = ("STM32_PLATFORM", "STM32WLxx", "USE_STM32WL")
        cases = [(base, True),
                 (base[:-1], False),
                 (("ESP32_PLATFORM",), False),
                 (base + ("WITH_WEBCONFIG",), False),
                 (base + ("WITH_MQTT_BRIDGE",), False),
                 (base + ("WITH_UDP_BRIDGE",), False),
                 (base + ("WITH_ESPNOW_BRIDGE",), False),
                 (base + ("WITH_BRIDGE",), False),
                 (base + ("ETHERNET_ENABLED",), False),
                 (base + ("BLE_PIN_CODE=123456",), False),
                 (base + ("WIFI_SSID=custom",), False),
                 (base + ("MESH_PRIMARY_ESPNOW=1",), False),
                 (base + ("MESH_ENABLE_EMERGENCY_POWER=1",), False)]
        for defines, expected in cases:
            with self.subTest(defines=defines):
                result = subprocess.run([compiler, "-E", "-P", "-x", "c", "-",
                                         *("-D" + value for value in defines)],
                                        input=branch, text=True, capture_output=True,
                                        check=True)
                self.assertEqual("handleNoServices" in result.stdout, expected)
                self.assertEqual("control().handle" in result.stdout, not expected)


if __name__ == "__main__":
    unittest.main()
