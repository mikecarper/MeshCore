"""Exercise regulator JSON, authenticated batch reboot and shipped browser UI."""
import json
from pathlib import Path
import unittest

from test_replay_reset_integration import extract_braced
import test_webconfig_lost_reply as batch_fixture
import test_webconfig_recovery_auth as auth_fixture
import test_webconfig_ui_runtime as browser_fixture

ROOT = Path(__file__).resolve().parents[1]


class WebConfigRegulatorTest(unittest.TestCase):
    compile_run = auth_fixture.WebConfigRecoveryAuthTest.compile_run
    setup_values = browser_fixture.WebConfigUiRuntimeTest.setup_values
    run_page = browser_fixture.WebConfigUiRuntimeTest.run_page

    def test_actual_snapshot_and_authenticated_reboot_batch(self):
        # Reuse the actual HTTP/config parser fixture, rather than a duplicate
        # batch model. The unrelated Lost-reply test main is replaced below.
        source = batch_fixture.LostReplyWebConfigTest.fixture(self)
        source = source[:source.index("String writeBody(")]
        server = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        snapshot = extract_braced(server, "static void webConfigRegulatorsSnapshot(")
        self.compile_run(source + r'''
#include <helpers/RegulatorMode.h>
namespace mesh { namespace power {
uint8_t modes[2] = {0, 3};
RegulatorMode requested[2] = {RegulatorMode::Default, RegulatorMode::Dcdc};
RegulatorMode active[2] = {RegulatorMode::Default, RegulatorMode::Ldo};
bool fallback[2] = {false, true}, recovery = true;
const char* regulatorModeName(RegulatorMode mode) {
  return mode == RegulatorMode::Dcdc ? "dcdc" : mode == RegulatorMode::Ldo ? "ldo" : "auto";
}
uint8_t configuredRegulatorModes(bool mcu) { return modes[mcu ? 0 : 1]; }
RegulatorMode requestedRegulatorMode(bool mcu) { return requested[mcu ? 0 : 1]; }
RegulatorMode activeRegulatorMode(bool mcu) { return active[mcu ? 0 : 1]; }
const char* activeRegulatorModeName(bool mcu) {
  auto mode = activeRegulatorMode(mcu);
  return mode == RegulatorMode::Default ? "unknown" : regulatorModeName(mode);
}
bool regulatorFallbackActive(bool mcu) { return fallback[mcu ? 0 : 1]; }
bool regulatorRecoveryAvailable() { return recovery; }
}}
''' + snapshot + r'''
int main() {
  using namespace mesh::power;
  JsonDocument doc;
  webConfigRegulatorsSnapshot(doc.to<JsonObject>());
  assert(doc["recovery"].as<bool>());
  assert(doc["mcu"]["modes"].as<int>() == 0);
  assert(strcmp(doc["mcu"]["active"].as<const char*>(), "unknown") == 0);
  assert(doc["radio"]["modes"].as<int>() == 3);
  assert(strcmp(doc["radio"]["requested"].as<const char*>(), "dcdc") == 0);
  assert(strcmp(doc["radio"]["active"].as<const char*>(), "ldo") == 0);
  assert(doc["radio"]["fallback"].as<bool>());
  modes[0] = 3; modes[1] = 2; recovery = false;
  requested[0] = active[0] = RegulatorMode::Dcdc;
  webConfigRegulatorsSnapshot(doc.to<JsonObject>());
  assert(!doc["recovery"].as<bool>() && doc["mcu"]["modes"].as<int>() == 3);
  assert(doc["radio"]["modes"].as<int>() == 2);
  assert(strcmp(doc["mcu"]["active"].as<const char*>(), "dcdc") == 0);
  assert(mesh::esp32::managementPassword::publish("password"));
  for (const char* key : {"mcu.regulator", "radio.regulator"}) {
    assert(wcIsAllowedSetKey(key) && wcSetKeyRequiresReboot(key));
    WebConfigServer server;
    String body = String("{\"reqid\":\"0123456789abcdef\",\"reboot\":false,\"set\":{\"") + key + "\":\"ldo\"}}";
    AsyncWebServerRequest unauthenticated(body); unauthenticated.header.cookies.clear();
    server.handleConfigPost(&unauthenticated);
    assert(unauthenticated.status == 401 && server._batch_count == 0);
    AsyncWebServerRequest save(body); server.handleConfigPost(&save);
    assert(save.status == 202 && server._batch_count == 1 && server._batch_reboot);
    assert(String(server._batch[0].cmd) == String("set ") + key + " ldo");
    assert(String(server._batch[0].key) == key);
    AsyncWebServerRequest replay(body); server.handleConfigPost(&replay);
    assert(replay.status == 202 && server._batch_count == 1 && server._batch_reboot);
  }
  for (const char* key : {"mcu.regulator.extra", "radio.regulator.extra", "mcu.regulator\nreboot",
                          "MCU.regulator", "radio.regulator ", "regulator"}) {
    assert(!wcIsAllowedSetKey(key) && !wcSetKeyRequiresReboot(key));
  }
  assert(!wcSetKeyRequiresReboot(nullptr));
}
''')

    def test_ldo_only_radio_hides_dc_dc_and_unsupported_processor(self):
        status, config = self.setup_values()
        status["mode"] = "lan"
        config["regulators"] = {
            "recovery": False,
            "mcu": {"modes": 0},
            "radio": {"modes": 2, "requested": "ldo", "active": "ldo", "fallback": False},
        }
        prelude = r'''<script>
(function(){
  var status=%s,config=%s;
  window.fetch=function(path){var value=path==="/api/status"?status:config;
    return Promise.resolve({ok:true,status:200,json:function(){return Promise.resolve(value)}})};
  window.addEventListener("load",function(){setTimeout(function(){
    var body=document.body,radio=document.querySelector('[data-k="radio.regulator"]');
    var dcdc=radio.querySelector('[value="dcdc"]');
    body.setAttribute("data-test-regulator-visible",!document.getElementById("regulator-settings").classList.contains("hide"));
    body.setAttribute("data-test-mcu-hidden",document.getElementById("mcu-regulator-setting").classList.contains("hide"));
    body.setAttribute("data-test-mcu-disabled",document.querySelector('[data-k="mcu.regulator"]').disabled);
    body.setAttribute("data-test-dcdc-unavailable",dcdc.hidden&&dcdc.disabled);
    body.setAttribute("data-test-radio-value",radio.value);
    body.setAttribute("data-test-radio-status",document.getElementById("radio-regulator-status").textContent);
    body.setAttribute("data-test-recovery-unavailable",document.getElementById("regulator-recovery-status").textContent.indexOf("unavailable")>=0);
  },400)});
})();</script>''' % (json.dumps(status), json.dumps(config))
        dom = self.run_page(prelude)
        for check in ("regulator-visible", "mcu-hidden", "mcu-disabled", "dcdc-unavailable", "recovery-unavailable"):
            self.assertIn(f'data-test-{check}="true"', dom)
        self.assertIn('data-test-radio-value="ldo"', dom)
        self.assertIn('data-test-radio-status="Active: LDO. Automatic fallback: off."', dom)

    def test_clear_fallback_and_mode_change_use_existing_save_and_reboot_flow(self):
        status, config = self.setup_values()
        status["mode"] = "lan"
        config["regulators"] = {
            "recovery": True,
            "mcu": {"modes": 3, "requested": "dcdc", "active": "dcdc", "fallback": False},
            "radio": {"modes": 3, "requested": "auto", "active": "ldo", "fallback": True},
        }
        prelude = r'''<script>
(function(){
  var status=%s,config=%s,submitted=null,posts=0;
  function response(value,code){return {ok:true,status:code||200,json:function(){return Promise.resolve(value)}}}
  window.fetch=function(path,options){
    if(path==="/api/status")return Promise.resolve(response(status));
    if(path==="/api/config"&&options&&options.method==="POST"){
      posts++;submitted=JSON.parse(options.body);return Promise.resolve(response({state:"pending"},202));
    }
    if(path.indexOf("/api/config/result")===0)return Promise.resolve(response({state:"done",reqid:submitted.reqid,all_ok:true,reboot:true,
      results:[{key:"mcu.regulator",reply:"OK - MCU regulator ldo saved; reboot required"},{key:"radio.regulator",reply:"OK - radio regulator auto saved; reboot required"}]}));
    return Promise.resolve(response(config));
  };
  window.addEventListener("load",function(){
    setTimeout(function(){
      var body=document.body,mcu=document.querySelector('[data-k="mcu.regulator"]');
      body.setAttribute("data-test-mcu-loaded",mcu.value);
      body.setAttribute("data-test-fallback-status",document.getElementById("radio-regulator-status").textContent);
      body.setAttribute("data-test-clear-visible",!document.getElementById("radio-regulator-clear").classList.contains("hide"));
      document.getElementById("radio-regulator-clear").click();
      body.setAttribute("data-test-unchanged-mode-resubmitted",st.dirty["radio.regulator"]);
      body.setAttribute("data-test-posts-before-save",posts);
      mcu.value="ldo";mcu.dispatchEvent(new Event("input",{bubbles:true}));appSave();
    },400);
    setTimeout(function(){
      var body=document.body;
      body.setAttribute("data-test-posts",posts);
      body.setAttribute("data-test-post-mcu",submitted&&submitted.set["mcu.regulator"]);
      body.setAttribute("data-test-post-radio",submitted&&submitted.set["radio.regulator"]);
      body.setAttribute("data-test-post-reboot",submitted&&submitted.reboot);
      body.setAttribute("data-test-reboot-visible",document.getElementById("reboot-ov").classList.contains("show"));
      body.setAttribute("data-test-regulator-reboot-message",document.getElementById("reboot-msg").textContent.indexOf("power regulator mode")>=0);
    },900);
  });
})();</script>''' % (json.dumps(status), json.dumps(config))
        dom = self.run_page(prelude)
        for key, value in (("mcu-loaded", "dcdc"), ("clear-visible", "true"),
                           ("unchanged-mode-resubmitted", "auto"), ("posts-before-save", "0"),
                           ("posts", "1"), ("post-mcu", "ldo"), ("post-radio", "auto"),
                           ("post-reboot", "true"), ("reboot-visible", "true"),
                           ("regulator-reboot-message", "true")):
            self.assertIn(f'data-test-{key}="{value}"', dom)
        self.assertIn('data-test-fallback-status="Active: LDO. Automatic fallback: on (LDO)."', dom)

    def test_older_firmware_without_regulator_metadata_hides_the_card(self):
        status, config = self.setup_values()
        status["mode"] = "lan"
        config.pop("regulators", None)
        prelude = r'''<script>
(function(){
  var status=%s,config=%s;
  window.fetch=function(path){return Promise.resolve({ok:true,status:200,json:function(){return Promise.resolve(path==="/api/status"?status:config)}})};
  window.addEventListener("load",function(){setTimeout(function(){
    document.body.setAttribute("data-test-regulator-hidden",document.getElementById("regulator-settings").classList.contains("hide"));
    document.body.setAttribute("data-test-radio-disabled",document.querySelector('[data-k="radio.regulator"]').disabled);
  },400)});
})();</script>''' % (json.dumps(status), json.dumps(config))
        dom = self.run_page(prelude)
        self.assertIn('data-test-regulator-hidden="true"', dom)
        self.assertIn('data-test-radio-disabled="true"', dom)


if __name__ == "__main__":
    unittest.main()
