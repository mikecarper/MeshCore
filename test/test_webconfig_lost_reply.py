#!/usr/bin/env python3
"""Exercise the shipped Lost-status control and actual WebConfig JSON handlers."""

import json
import html
from pathlib import Path
import re
import unittest
from unittest import mock

from test_replay_reset_integration import extract_braced
import test_webconfig_recovery_auth as auth_fixture
import test_webconfig_ui_runtime as browser_fixture


ROOT = Path(__file__).resolve().parents[1]


class LostReplyWebConfigTest(unittest.TestCase):
    compile_run = auth_fixture.WebConfigRecoveryAuthTest.compile_run
    setup_values = browser_fixture.WebConfigUiRuntimeTest.setup_values

    def run_page(self, prelude, virtual_time=1500):
        # Snap Chromium has a private /tmp. Put the HTML in the shared
        # workspace, as the Room browser fixture does, so it can load it.
        temporary_directory = browser_fixture.tempfile.TemporaryDirectory
        cache = ROOT / ".pio"
        cache.mkdir(exist_ok=True)

        def workspace_temporary_directory(*args, **kwargs):
            kwargs.setdefault("dir", cache)
            return temporary_directory(*args, **kwargs)

        with mock.patch.object(browser_fixture.tempfile, "TemporaryDirectory",
                               workspace_temporary_directory):
            return browser_fixture.WebConfigUiRuntimeTest.run_page(self, prelude, virtual_time)

    def fixture(self):
        server = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        header = (ROOT / "src/helpers/esp32/WebConfigServer.h").read_text()
        capability = extract_braced(header, "enum Capability : uint32_t") + ";"
        snapshot = extract_braced(header, "struct NodeSnapshot") + ";"
        batch_entry = extract_braced(header, "struct BatchEntry") + ";"
        allowed = extract_braced(server, "static bool isAllowedSetKey(")
        secret = extract_braced(server, "static bool isSecretKey(")
        post = extract_braced(server, "void WebConfigServer::handleConfigPost(")
        get = extract_braced(server, "void WebConfigServer::handleConfigGet(")
        # Execute the actual snapshot-to-radio JSON branch. WiFi/MQTT fields
        # after this boundary are unrelated and require peripheral fixtures.
        get = get[:get.index('    JsonObject wifi =')] + r'''
  }
  String out;
  serializeJson(doc, out);
  req->send(200, "application/json", out);
}
'''
        return r'''
#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <ArduinoJson.h>
#include "helpers/WebConfigKeys.h"
#include "helpers/WebConfigBatch.h"
using String = std::string;
struct WCLock { explicit WCLock(int) {} };
uint32_t millis() { return 1; }
namespace mesh {
struct Port { template<typename... Args> void printf(const char*, Args...) {} };
Port& usbDebugPort() { static Port port; return port; }
namespace wifi { bool parseEspNowChannel(const char*, uint8_t&) { return false; } }
}
static const char SECRET_SENTINEL[] = "********";
@ALLOWED@
@SECRET@
struct AsyncWebServerRequest {
  String input, output;
  void* _tempObject = nullptr;
  int status = 0;
  explicit AsyncWebServerRequest(String body = "") : input(body) { _tempObject = input.data(); }
  size_t contentLength() const { return input.size(); }
  void send(int code, const char* = nullptr, const String& body = "") { status = code; output = body; }
};
struct WebConfigServer {
  @CAPABILITY@
  @SNAPSHOT@
  @BATCH_ENTRY@
  struct Callbacks {
    NodeSnapshot node = {};
    void getNodeSnapshot(NodeSnapshot& result) { result = node; }
  } callbacks;
  Callbacks* _cb = &callbacks;
  enum { MODE_OFF, MODE_SETUP, MODE_LAN, BATCH_CONFIG, BATCH_CLI };
  static constexpr size_t MAX_BODY = 4096;
  static constexpr int MAX_BATCH = WebConfigBatch::kMaxBatch;
  int _mode = MODE_LAN, _mux = 0, _batch_kind = BATCH_CONFIG;
  bool authorized = true, _initial_setup = false, _batch_reboot = false;
  bool _batch_reboot_armed = false, _batch_all_ok = true;
  bool _standalone_wifi_dirty = false, _setup_wifi_handoff_pending = false;
  uint32_t _setup_wifi_handoff_deadline = 0, _diag_until = 0;
  char _setup_wifi_handoff_ip[32] = {}, _batch_reqid[17] = {};
  int _batch_count = 0, _batch_next = 0;
  void* _mqtt_prefs = nullptr;
  BatchEntry _batch[MAX_BATCH] = {};
  WebConfigBatch::State _batch_state = WebConfigBatch::State::Idle;
  static constexpr auto BATCH_PENDING = WebConfigBatch::State::Pending;
  static WebConfigBatch::State toSpecState(WebConfigBatch::State state) { return state; }
  bool checkAuth(AsyncWebServerRequest*) { return authorized; }
  void handleConfigGet(AsyncWebServerRequest*);
  void handleConfigPost(AsyncWebServerRequest*);
};
@GET@
@POST@
String writeBody(const char* value) {
  return String("{\"reqid\":\"0123456789abcdef\",\"set\":{\"lost.reply\":") + value + "}}";
}
int main() {
  const char* modes[] = {"off", "no", "yes"};
  for (int value : {0, 1, 2, 255}) {
    WebConfigServer server;
    server.callbacks.node.lost_reply = uint8_t(value);
    server.callbacks.node.capabilities = WebConfigServer::CAP_LOST_REPLY;
    AsyncWebServerRequest get;
    server.handleConfigGet(&get);
    JsonDocument snapshot;
    assert(get.status == 200 && deserializeJson(snapshot, get.output) == DeserializationError::Ok);
    assert(strcmp(snapshot["radio"]["lost_reply"].as<const char*>(), modes[value <= 2 ? value : 0]) == 0);
    assert(snapshot["radio"]["capabilities"].as<uint32_t>() == (1UL << 18));
  }
  for (const char* mode : modes) {
    WebConfigServer server;
    server.callbacks.node.capabilities = WebConfigServer::CAP_LOST_REPLY;
    AsyncWebServerRequest save(writeBody((String("\"") + mode + "\"").c_str()));
    server.handleConfigPost(&save);
    assert(save.status == 202 && server._batch_count == 1 && !server._batch_reboot);
    assert(strcmp(server._batch[0].key, "lost.reply") == 0);
    assert(String(server._batch[0].cmd) == String("set lost.reply ") + mode);
    // A correlated retry retains the established batch, including when the
    // retry carries an invalid value. Classification precedes new validation.
    AsyncWebServerRequest retry(writeBody("\"invalid\""));
    server.handleConfigPost(&retry);
    assert(retry.status == 202 && String(server._batch[0].cmd) == String("set lost.reply ") + mode);
  }
  for (const char* invalid : {"null", "true", "2", "\"on\"", "\"YES\"", "\"yes\\n\""}) {
    WebConfigServer server;
    server.callbacks.node.capabilities = WebConfigServer::CAP_LOST_REPLY;
    AsyncWebServerRequest save(writeBody(invalid));
    server.handleConfigPost(&save);
    assert(save.status == 400 && save.output.find("expected off, no or yes") != String::npos);
    assert(server._batch_count == 0 && server._batch_state == WebConfigBatch::State::Idle);
  }
  WebConfigServer infrastructure;
  AsyncWebServerRequest forged(writeBody("\"yes\""));
  infrastructure.handleConfigPost(&forged);
  assert(forged.status == 400 && forged.output.find("setting unavailable") != String::npos);
  assert(infrastructure._batch_count == 0);
  infrastructure.authorized = false;
  AsyncWebServerRequest unauthorized(writeBody("\"yes\""));
  infrastructure.handleConfigPost(&unauthorized);
  assert(unauthorized.status == 401 && infrastructure._batch_count == 0);
  assert(wcIsAllowedSetKey("lost.reply") && !wcIsAllowedSetKey("lost.reply.extra"));
  assert(!wcSetKeyRequiresReboot("lost.reply"));
}
'''.replace("@CAPABILITY@", capability).replace("@SNAPSHOT@", snapshot).replace(
            "@BATCH_ENTRY@", batch_entry).replace("@ALLOWED@", allowed).replace(
            "@SECRET@", secret).replace("@GET@", get).replace("@POST@", post)

    def test_actual_json_strings_and_capability_gated_settings_batch(self):
        # The shared compiler helper needs the production helper include path.
        self.compile_run(self.fixture().replace('"helpers/', '"' + (ROOT / "src/helpers").as_posix() + '/'))

    def test_shipped_control_defaults_off_and_saves_no_yes_off(self):
        status, config = self.setup_values()
        status.update(mode="lan", capabilities=1 << 18)
        config["radio"]["lost_reply"] = "off"
        prelude = r'''<script>
(function(){
  var status=%s,config=%s,submitted=null,saves=[];
  function reply(value,code){return Promise.resolve({ok:true,status:code||200,json:function(){return Promise.resolve(value)}})}
  window.fetch=function(path,options){
    if(path==="/api/status")return reply(status);
    if(path==="/api/config"&&(!options||options.method!=="POST"))return reply(config);
    if(path==="/api/config"){
      submitted=JSON.parse(options.body);saves.push(submitted);
      config.radio.lost_reply=submitted.set["lost.reply"];
      return reply({state:"pending",reqid:submitted.reqid,count:1},202);
    }
    if(path.indexOf("/api/config/result?")===0)return reply({state:"done",reqid:submitted.reqid,results:[{key:"lost.reply",reply:"OK"}]});
    return reply({});
  };
  function mark(key,value){document.body.setAttribute("data-test-"+key,String(value))}
  function waitFor(check,next,attempt){
    if(check()){next();return}
    if((attempt||0)>100){mark("error","save did not complete");return}
    setTimeout(function(){waitFor(check,next,(attempt||0)+1)},20);
  }
  window.addEventListener("load",function(){setTimeout(function(){
    var field=document.querySelector('#v-app [data-k="lost.reply"]');
    mark("visible",!field.closest('[data-cap]').classList.contains("hide"));
    mark("default",field.value);mark("choices",Array.prototype.map.call(field.options,function(o){return o.value}).join(","));
    mark("enum",cliEnum("lost.reply").join(","));
    var values=["no","yes","off"],index=0;
    function saveNext(){
      var value=values[index];field.value=value;
      field.dispatchEvent(new Event("input",{bubbles:true}));field.dispatchEvent(new Event("change",{bubbles:true}));
      appSave();
      waitFor(function(){return saves.length===index+1&&st.orig["lost.reply"]===value&&!("lost.reply" in st.dirty)},function(){
        mark("saved-"+value,field.value);index++;
        if(index<values.length){saveNext();return}
        mark("saves",JSON.stringify(saves.map(function(s){return s.set})));mark("reboot",saves.some(function(s){return !!s.reboot}));
        mark("result",field.closest('.f').textContent);mark("done",true);
      });
    }
    saveNext();
  },300)});
})();
</script>''' % (json.dumps(status), json.dumps(config))
        dom = self.run_page(prelude, virtual_time=3500)
        for key, value in (("visible", "true"), ("default", "off"), ("choices", "off,no,yes"),
                           ("enum", "off,no,yes"), ("saved-no", "no"), ("saved-yes", "yes"),
                           ("saved-off", "off"), ("reboot", "false"), ("done", "true")):
            self.assertIn('data-test-%s="%s"' % (key, value), dom)
        saved = re.search(r'data-test-saves="([^"]*)"', dom)
        self.assertIsNotNone(saved)
        self.assertEqual(json.loads(html.unescape(saved.group(1))),
                         [{"lost.reply": "no"}, {"lost.reply": "yes"}, {"lost.reply": "off"}])
        self.assertIn("Lost-status auto reply", dom)
        self.assertIn("Am I lost?", dom)

    def test_other_roles_hide_setting_and_do_not_offer_cli_key(self):
        for role in ("Repeater", "Room Server"):
            with self.subTest(role=role):
                status, config = self.setup_values()
                status.update(mode="lan", role=role, capabilities=1)
                config["radio"]["lost_reply"] = "off"
                prelude = r'''<script>
window.fetch=function(path){var value=path==="/api/status"?%s:%s;return Promise.resolve({ok:true,status:200,json:function(){return Promise.resolve(value)}})};
window.addEventListener("load",function(){setTimeout(function(){
  document.body.setAttribute("data-test-hidden",document.getElementById("lost-reply-setting").classList.contains("hide"));
  document.body.setAttribute("data-test-no-cli",!cliTable().some(function(row){return row[0]==="get lost.reply"||row[0]==="set lost.reply "}));
},300)});
</script>''' % (json.dumps(status), json.dumps(config))
                dom = self.run_page(prelude)
                self.assertIn('data-test-hidden="true"', dom)
                self.assertIn('data-test-no-cli="true"', dom)


if __name__ == "__main__":
    unittest.main()
