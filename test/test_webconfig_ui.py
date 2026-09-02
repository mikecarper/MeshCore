#!/usr/bin/env python3

import gzip
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "webui" / "index.html"
HEADER = ROOT / "src" / "helpers" / "esp32" / "WebConfigHtml.h"


class WebConfigUiTest(unittest.TestCase):
    def test_wifi_password_fields_have_local_show_controls(self):
        page = SOURCE.read_text(encoding="utf-8")
        self.assertEqual(page.count('onclick="toggleWiFiPassword(this)"'), 2)
        self.assertEqual(page.count('class="pwrow"'), 2)
        self.assertIn('type="password" data-k="wifi.pwd" id="wz-wifi-pwd"', page)
        self.assertIn('type="password" data-k="wifi.pwd" id="app-wifi-pwd"', page)
        self.assertIn('input.type=show?"text":"password"', page)
        self.assertIn('btn.textContent=show?"Hide":"Show"', page)
        self.assertIn('btn.setAttribute("aria-pressed",show?"true":"false")', page)

    def test_setup_wizard_can_skip_mqtt_and_usa_radio(self):
        page = SOURCE.read_text(encoding="utf-8")
        self.assertIn('id="wz-radio-skip" onclick="wzSkipRadio()"', page)
        self.assertIn('onclick="wzSkipMqtt()">Skip MQTT</button>', page)
        self.assertIn("st.radioOptional=!!s.radio_optional", page)

        radio_skip = page[page.index("function wzSkipRadio()"):
                          page.index("function wzUseMqtt()")]
        self.assertIn("if(!st.radioOptional)return", radio_skip)
        self.assertIn("delete st.dirty.radio", radio_skip)
        self.assertIn("delete st.dirty.tx", radio_skip)

        mqtt_skip = page[page.index("function wzSkipMqtt()"):
                         page.index("function popOut()")]
        self.assertIn("st.skipMqtt=true", mqtt_skip)
        self.assertIn("/^mqtt(?:\\.|[1-9]\\.)/", mqtt_skip)
        self.assertIn("syncPacketFilters()", mqtt_skip)

        save = page[page.index("function wizardSave()"):
                    page.index("/* ---------- reboot ---------- */")]
        self.assertIn('if(!st.skipRadio&&$("#wz-rp").value==="")', save)
        self.assertIn("if(st.hasMqtt&&!st.skipMqtt&&", save)

    def test_setup_wizard_opens_existing_wifi_scan_once(self):
        page = SOURCE.read_text(encoding="utf-8")
        self.assertIn("wizardScanOpened:false", page)
        self.assertIn('onclick="openScan(\'wz-ssid\')">Scan</button>', page)
        self.assertIn('onclick="startScan(true)">Rescan</button>', page)

        open_scan = page[page.index("function openScan(target)"):
                         page.index("function closeScan()")]
        self.assertIn('if(target==="wz-ssid")st.wizardScanOpened=true', open_scan)

        auto_open = page[page.index("function openWizardScanOnce()"):
                         page.index("function wzGo(n)")]
        self.assertIn("if(st.wizardScanOpened)return", auto_open)
        self.assertIn('openScan("wz-ssid")', auto_open)
        self.assertNotIn("startScan(true)", auto_open)

        show_wizard = page[page.index("function showWizard()"):
                           page.index("function wzGo(n)")]
        self.assertLess(show_wizard.index("loadConfig(capture)"),
                        show_wizard.index("openWizardScanOnce()"))
        self.assertIn("wzRadioFill();openWizardScanOnce()", show_wizard)

        # Scan completion may replace only the result list. Credentials change
        # only in the existing explicit network-click handler below it.
        poll_scan = page[page.index("function pollScan(url,epoch)"):
                         page.index("function clearInheritedWiFiPassword")]
        self.assertNotIn(".value=", poll_scan)
        self.assertNotIn("dispatchEvent", poll_scan)

    def test_scan_epoch_invalidates_old_timers_and_responses(self):
        page = SOURCE.read_text(encoding="utf-8")
        self.assertIn("scanEpoch:0", page)

        close_scan = page[page.index("function closeScan()"):
                          page.index("function startScan(force)")]
        self.assertIn("clearTimeout(st.scanTimer);st.scanTimer=0", close_scan)
        self.assertIn("st.scanEpoch++", close_scan)

        start_scan = page[page.index("function startScan(force)"):
                          page.index("function bars(rssi)")]
        self.assertIn("var epoch=++st.scanEpoch", start_scan)
        self.assertIn('pollScan(force?"/api/scan?rescan=1":"/api/scan",epoch)',
                      start_scan)

        poll_scan = page[page.index("function pollScan(url,epoch)"):
                         page.index("function clearInheritedWiFiPassword")]
        # Check before issuing a request, after a response, and in the error
        # path so neither a stale timer nor a stale reply can alter the panel.
        self.assertEqual(poll_scan.count("if(epoch!==st.scanEpoch)return"), 3)
        self.assertIn('pollScan("/api/scan",epoch)', poll_scan)

    def test_late_config_load_replays_visible_form_edits(self):
        page = SOURCE.read_text(encoding="utf-8")
        self.assertIn("configLoadCapture:null", page)
        self.assertIn("configEpoch:0", page)

        listener = page[page.index('document.addEventListener("input"'):
                        page.index("function updateSaveBar()")]
        self.assertIn("el.closest(capture.root)", listener)
        self.assertIn("capture.values[el.dataset.k]=elVal(el)", listener)
        self.assertIn("capture.confirms[el.dataset.cfm]=el.value", listener)
        self.assertIn("capture.wifiPasswordAutoCleared=!!ev.meshAutoWiFiPassword", listener)
        self.assertIn("capture.radio[el.dataset.rg]=elVal(el)", listener)
        self.assertIn("capture.rxps[el.dataset.rxps]=elVal(el)", listener)

        load_config = page[page.index("function loadConfig(loadCapture)"):
                           page.index("function markDirty")]
        self.assertIn("var epoch=++st.configEpoch", load_config)
        self.assertIn("loadCapture.loadEpoch=epoch", load_config)
        self.assertIn("if(epoch!==st.configEpoch)return false", load_config)
        self.assertIn("restoreConfigLoadEdits(loadCapture)", load_config)
        self.assertIn("return true", load_config)

        restore = page[page.index("function restoreConfigLoadEdits"):
                       page.index("function rxpsCfgValue")]
        self.assertIn('owns.call(capture.values,"wifi.ssid")', restore)
        self.assertIn('capture.values["wifi.ssid"]!==st.orig["wifi.ssid"]',
                      restore)
        self.assertIn('!owns.call(capture.values,"wifi.pwd")', restore)
        self.assertIn('st.orig["wifi.pwd"]===SENTINEL', restore)
        self.assertIn('capture.values["wifi.pwd"]=""', restore)
        self.assertIn("capture.wifiPasswordAutoCleared=true", restore)
        self.assertIn("st.wifiPasswordAutoCleared=true", restore)
        self.assertIn("v!==st.orig[k]", restore)
        self.assertIn("if(dirty)st.dirty[k]=v;else delete st.dirty[k]", restore)
        self.assertIn("capture.confirms[k]", restore)
        self.assertIn("Object.keys(capture.radio)", restore)
        self.assertIn("var restoredRadio=radioCombo()", restore)
        self.assertIn("if(capture.rxps)", restore)

        show_wizard = page[page.index("function showWizard()"):
                           page.index("function wzGo(n)")]
        self.assertLess(show_wizard.index("st.configLoadCapture=capture"),
                         show_wizard.index("loadConfig(capture)"))
        self.assertIn("if(st.configLoadCapture===capture)", show_wizard)
        self.assertIn("if(!applied)return", show_wizard)
        self.assertIn("st.configEpoch===capture.loadEpoch", show_wizard)

        enter_app = page[page.index("function enterApp()"):
                         page.index("function enterConsole()")]
        self.assertIn('root:"#v-app"', enter_app)
        self.assertIn("st.configLoadCapture=capture", enter_app)
        self.assertIn("loadConfig(capture)", enter_app)

    def test_new_ssid_clears_only_inherited_masked_password(self):
        page = SOURCE.read_text(encoding="utf-8")
        clear_pwd = page[page.index("function clearInheritedWiFiPassword"):
                         page.index('$("#scan-list").addEventListener')]
        self.assertIn('target==="wz-ssid"?"wz-wifi-pwd"', clear_pwd)
        self.assertIn('target==="app-ssid"?"app-wifi-pwd"', clear_pwd)
        self.assertIn('nextSsid===st.orig["wifi.ssid"]', clear_pwd)
        self.assertIn("pwd.value!==SENTINEL", clear_pwd)
        self.assertIn('pwd.value=""', clear_pwd)
        self.assertIn("st.wifiPasswordAutoCleared=true", clear_pwd)
        self.assertIn("st.wifiPasswordAutoCleared=false", clear_pwd)
        self.assertIn("pwd.value=SENTINEL", clear_pwd)
        self.assertIn("meshAutoWiFiPassword=true", clear_pwd)

        choose = page[page.index('$("#scan-list").addEventListener'):
                      page.index("/* ---------- wizard ---------- */")]
        self.assertLess(
            choose.index("clearInheritedWiFiPassword(st.scanTarget,selected)"),
            choose.index("el.value=selected"),
        )

        listener = page[page.index('document.addEventListener("input"'):
                        page.index("function updateSaveBar()")]
        self.assertIn(
            'if(el.dataset.k==="wifi.ssid")clearInheritedWiFiPassword(el.id,elVal(el))',
            listener,
        )

    def test_password_review_distinguishes_explicit_clear(self):
        page = SOURCE.read_text(encoding="utf-8")
        review = page[page.index("function buildReview()"):
                      page.index("function wizardSave()")]
        self.assertIn(
            'Object.prototype.hasOwnProperty.call(d,"wifi.pwd")', review
        )
        self.assertIn(
            'pwdChanged?(d["wifi.pwd"]?"******":"(open network)")', review
        )

    def test_scan_panel_exposes_automatic_updates_accessibly(self):
        page = SOURCE.read_text(encoding="utf-8")
        self.assertIn('id="wz-scan-btn"', page)
        self.assertIn('id="app-scan-btn"', page)
        self.assertEqual(page.count('aria-controls="scan-panel"'), 2)
        self.assertIn('id="scan-panel" role="region"', page)
        self.assertIn('id="scan-list" aria-live="polite" aria-busy="true"', page)
        open_scan = page[page.index("function openScan(target)"):
                         page.index("function closeScan()")]
        self.assertIn('setAttribute("aria-expanded"', open_scan)
        close_scan = page[page.index("function closeScan()"):
                          page.index("function startScan(force)")]
        self.assertIn('setAttribute("aria-hidden","true")', close_scan)

    def test_generated_asset_contains_show_controls(self):
        header = HEADER.read_text(encoding="utf-8")
        length = int(re.search(r"WEBCONFIG_HTML_GZ_LEN = (\d+);", header).group(1))
        array = header.split(
            "const uint8_t WEBCONFIG_HTML_GZ[] PROGMEM = {", 1
        )[1]
        blob = bytes(
            int(value, 16) for value in re.findall(r"0x([0-9a-f]{2})", array)
        )[:length]
        page = gzip.decompress(blob).decode("utf-8")
        self.assertEqual(page.count('onclick="toggleWiFiPassword(this)"'), 2)
        self.assertIn("function toggleWiFiPassword(btn)", page)
        self.assertIn("function wzSkipRadio()", page)
        self.assertIn("function wzSkipMqtt()", page)
        self.assertIn("function openWizardScanOnce()", page)
        self.assertIn('openScan("wz-ssid")', page)
        self.assertIn("function restoreConfigLoadEdits(capture)", page)
        self.assertIn("function pollScan(url,epoch)", page)
        self.assertIn("function clearInheritedWiFiPassword(target,nextSsid)", page)

    def test_skipped_mqtt_does_not_pin_display_in_setup_mode(self):
        main = (ROOT / "examples" / "companion_radio" / "main.cpp").read_text(
            encoding="utf-8"
        )
        display_route = main[main.index("void loop()") : main.index("rtc_clock.tick();")]
        self.assertIn("the_mesh.isWebConfigSetupActive()", display_route)
        self.assertNotIn("isMQTTConfigured()", display_route)


if __name__ == "__main__":
    unittest.main()
