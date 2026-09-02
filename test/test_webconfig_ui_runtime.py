#!/usr/bin/env python3

"""Optional real-browser checks for WebConfig's asynchronous setup races."""

import gzip
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "src" / "helpers" / "esp32" / "WebConfigHtml.h"


def chromium_path():
    candidates = [
        shutil.which(name)
        for name in ("google-chrome", "chromium", "chromium-browser", "chrome", "msedge")
    ]
    for base, rest in (
        (os.environ.get("ProgramFiles"), ("Google", "Chrome", "Application", "chrome.exe")),
        (os.environ.get("ProgramFiles(x86)"), ("Microsoft", "Edge", "Application", "msedge.exe")),
        (os.environ.get("LocalAppData"), ("Google", "Chrome", "Application", "chrome.exe")),
    ):
        if base:
            candidates.append(str(Path(base).joinpath(*rest)))
    return next((path for path in candidates if path and Path(path).is_file()), None)


BROWSER = chromium_path()


def embedded_page():
    header = HEADER.read_text(encoding="utf-8")
    length = int(re.search(r"WEBCONFIG_HTML_GZ_LEN = (\d+);", header).group(1))
    array = header.split("const uint8_t WEBCONFIG_HTML_GZ[] PROGMEM = {", 1)[1]
    blob = bytes(
        int(value, 16) for value in re.findall(r"0x([0-9a-f]{2})", array)
    )[:length]
    return gzip.decompress(blob).decode("utf-8")


class WebConfigUiRuntimeTest(unittest.TestCase):
    def setup_values(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            import webconfig_mock_server as mock
        finally:
            sys.path.pop(0)

        config = mock.default_config(True)
        config["wifi"]["ssid"] = "StoredNet"
        config["wifi"]["pwd"] = "********"
        status = {
            "mode": "setup",
            "auth": True,
            "mqtt": False,
            "cli": False,
            "wifi_psk64": True,
            "password_supported": True,
            "needs_password": False,
            "radio_optional": True,
            "needs_setup": False,
            "capabilities": 0,
            "max_cmds": 16,
            "name": "runtime-test",
            "role": "Companion",
            "board": "mock",
            "fw": "v1.17.1-test",
            "build_date": "runtime",
        }
        return status, config

    def run_page(self, prelude, virtual_time=1500):
        if not BROWSER:
            self.skipTest("Chromium-family browser is unavailable")

        source = embedded_page()
        markers = ('<script>\n"use strict";', '<script>"use strict";')
        marker = next((candidate for candidate in markers if candidate in source), None)
        self.assertIsNotNone(marker)
        page = source.replace(marker, prelude + marker, 1)

        with tempfile.TemporaryDirectory(prefix="meshcore-webconfig-runtime-") as tmp:
            tmp_path = Path(tmp)
            html = tmp_path / "index.html"
            profile = tmp_path / "profile"
            profile.mkdir()
            html.write_text(page, encoding="utf-8")
            args = [
                BROWSER,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--user-data-dir=" + str(profile),
                "--virtual-time-budget=" + str(virtual_time),
                "--dump-dom",
                html.as_uri(),
            ]
            if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
                args.insert(1, "--no-sandbox")
            result = subprocess.run(args, capture_output=True, timeout=30)

        stderr = result.stderr.decode("utf-8", "replace")
        self.assertEqual(result.returncode, 0, stderr)
        dom = result.stdout.decode("utf-8", "replace")
        self.assertNotIn("data-test-error=", dom)
        return dom

    def run_early_selection_case(self, explicit_password=None):
        status, config = self.setup_values()

        prelude = """
<script>
(function(){
  var status=%s,config=%s;
  function response(value){
    return {ok:true,status:200,json:function(){return Promise.resolve(value)}};
  }
  window.fetch=function(path){
    if(path==="/api/status")return Promise.resolve(response(status));
    if(path==="/api/config")return new Promise(function(resolve){
      setTimeout(function(){resolve(response(config))},500);
    });
    if(path.indexOf("/api/scan")===0)return Promise.resolve(response({
      state:"done",networks:[{ssid:"EarlyNet",rssi:-40,enc:true,channel:6}]
    }));
    return Promise.resolve({ok:false,status:404,json:function(){return Promise.resolve({})}});
  };
  window.addEventListener("load",function(){
    setTimeout(function(){
      openScan("wz-ssid");
      setTimeout(function(){
        var network=document.querySelector("#scan-list .net");
        if(!network){document.body.setAttribute("data-test-error","missing-network");return}
        network.click();
        var explicit=%s;
        if(explicit!==null){
          var pwd=document.getElementById("wz-wifi-pwd");
          pwd.value=explicit;
          pwd.dispatchEvent(new Event("input",{bubbles:true}));
        }
      },50);
    },50);
    setTimeout(function(){
      var body=document.body,owns=Object.prototype.hasOwnProperty;
      body.setAttribute("data-test-ssid",document.getElementById("wz-ssid").value);
      body.setAttribute("data-test-wz-pwd",document.getElementById("wz-wifi-pwd").value);
      body.setAttribute("data-test-app-pwd",document.getElementById("app-wifi-pwd").value);
      body.setAttribute("data-test-orig-pwd",st.orig["wifi.pwd"]);
      body.setAttribute("data-test-dirty-ssid",st.dirty["wifi.ssid"]||"");
      body.setAttribute("data-test-dirty-pwd-present",
                        owns.call(st.dirty,"wifi.pwd")?"yes":"no");
      body.setAttribute("data-test-dirty-pwd",st.dirty["wifi.pwd"]||"");
    },900);
  });
})();
</script>
""" % (json.dumps(status), json.dumps(config), json.dumps(explicit_password))

        return self.run_page(prelude)

    def test_early_new_ssid_clears_password_loaded_later(self):
        dom = self.run_early_selection_case()
        self.assertIn('data-test-ssid="EarlyNet"', dom)
        self.assertIn('data-test-orig-pwd="********"', dom)
        self.assertIn('data-test-wz-pwd=""', dom)
        self.assertIn('data-test-app-pwd=""', dom)
        self.assertIn('data-test-dirty-ssid="EarlyNet"', dom)
        self.assertIn('data-test-dirty-pwd-present="yes"', dom)
        self.assertIn('data-test-dirty-pwd=""', dom)

    def test_early_explicit_password_is_preserved(self):
        dom = self.run_early_selection_case("new-secret")
        self.assertIn('data-test-ssid="EarlyNet"', dom)
        self.assertIn('data-test-wz-pwd="new-secret"', dom)
        self.assertIn('data-test-app-pwd="new-secret"', dom)
        self.assertIn('data-test-dirty-pwd-present="yes"', dom)
        self.assertIn('data-test-dirty-pwd="new-secret"', dom)

    def test_setup_automatically_opens_embedded_scan_picker(self):
        status, config = self.setup_values()
        prelude = """
<script>
(function(){
  var status=%s,config=%s;
  function response(value){
    return {ok:true,status:200,json:function(){return Promise.resolve(value)}};
  }
  window.fetch=function(path){
    if(path==="/api/status")return Promise.resolve(response(status));
    if(path==="/api/config")return new Promise(function(resolve){
      setTimeout(function(){resolve(response(config))},100);
    });
    if(path.indexOf("/api/scan")===0)return Promise.resolve(response({
      state:"done",networks:[{ssid:"NearbyNet",rssi:-35,enc:true,channel:11}]
    }));
    return Promise.resolve({ok:false,status:404,json:function(){return Promise.resolve({})}});
  };
  window.addEventListener("load",function(){
    setTimeout(function(){
      var body=document.body,panel=document.getElementById("scan-panel");
      body.setAttribute("data-test-scan-open",panel.classList.contains("hide")?"no":"yes");
      body.setAttribute("data-test-scan-hidden",panel.getAttribute("aria-hidden"));
      body.setAttribute("data-test-scan-expanded",document.getElementById("wz-scan-btn").getAttribute("aria-expanded"));
      body.setAttribute("data-test-scan-busy",document.getElementById("scan-list").getAttribute("aria-busy"));
      body.setAttribute("data-test-network-count",document.querySelectorAll("#scan-list .net").length);
      body.setAttribute("data-test-ssid",document.getElementById("wz-ssid").value);
      body.setAttribute("data-test-pwd",document.getElementById("wz-wifi-pwd").value);
      body.setAttribute("data-test-dirty-count",Object.keys(st.dirty).length);
    },700);
  });
})();
</script>
""" % (json.dumps(status), json.dumps(config))

        dom = self.run_page(prelude)
        self.assertIn('data-test-scan-open="yes"', dom)
        self.assertIn('data-test-scan-hidden="false"', dom)
        self.assertIn('data-test-scan-expanded="true"', dom)
        self.assertIn('data-test-scan-busy="false"', dom)
        self.assertIn('data-test-network-count="1"', dom)
        self.assertIn('data-test-ssid="StoredNet"', dom)
        self.assertIn('data-test-pwd="********"', dom)
        self.assertIn('data-test-dirty-count="0"', dom)

    def test_typed_new_ssid_clears_inherited_password_and_review(self):
        status, config = self.setup_values()
        prelude = """
<script>
(function(){
  var status=%s,config=%s;
  function response(value){
    return {ok:true,status:200,json:function(){return Promise.resolve(value)}};
  }
  window.fetch=function(path){
    if(path==="/api/status")return Promise.resolve(response(status));
    if(path==="/api/config")return Promise.resolve(response(config));
    if(path.indexOf("/api/scan")===0)return Promise.resolve(response({state:"done",networks:[]}));
    return Promise.resolve({ok:false,status:404,json:function(){return Promise.resolve({})}});
  };
  window.addEventListener("load",function(){
    setTimeout(function(){
      var ssid=document.getElementById("wz-ssid");
      ssid.value="TypedNet";
      ssid.dispatchEvent(new Event("input",{bubbles:true}));
      buildReview();
      var body=document.body,owns=Object.prototype.hasOwnProperty;
      body.setAttribute("data-test-ssid",ssid.value);
      body.setAttribute("data-test-pwd",document.getElementById("wz-wifi-pwd").value);
      body.setAttribute("data-test-dirty-pwd",owns.call(st.dirty,"wifi.pwd")?"yes":"no");
      body.setAttribute("data-test-review-open",document.getElementById("wz-review").textContent.indexOf("(open network)")>=0?"yes":"no");
    },400);
  });
})();
</script>
""" % (json.dumps(status), json.dumps(config))

        dom = self.run_page(prelude)
        self.assertIn('data-test-ssid="TypedNet"', dom)
        self.assertIn('data-test-pwd=""', dom)
        self.assertIn('data-test-dirty-pwd="yes"', dom)
        self.assertIn('data-test-review-open="yes"', dom)

    def test_returning_to_original_ssid_restores_masked_password(self):
        status, config = self.setup_values()
        prelude = """
<script>
(function(){
  var status=%s,config=%s;
  function response(value){
    return {ok:true,status:200,json:function(){return Promise.resolve(value)}};
  }
  window.fetch=function(path){
    if(path==="/api/status")return Promise.resolve(response(status));
    if(path==="/api/config")return Promise.resolve(response(config));
    if(path.indexOf("/api/scan")===0)return Promise.resolve(response({state:"done",networks:[]}));
    return Promise.resolve({ok:false,status:404,json:function(){return Promise.resolve({})}});
  };
  window.addEventListener("load",function(){
    setTimeout(function(){
      var ssid=document.getElementById("wz-ssid");
      ssid.value="OtherNet";
      ssid.dispatchEvent(new Event("input",{bubbles:true}));
      ssid.value="StoredNet";
      ssid.dispatchEvent(new Event("input",{bubbles:true}));
      var body=document.body,owns=Object.prototype.hasOwnProperty;
      body.setAttribute("data-test-pwd",document.getElementById("wz-wifi-pwd").value);
      body.setAttribute("data-test-dirty-ssid",owns.call(st.dirty,"wifi.ssid")?"yes":"no");
      body.setAttribute("data-test-dirty-pwd",owns.call(st.dirty,"wifi.pwd")?"yes":"no");
      body.setAttribute("data-test-auto-cleared",st.wifiPasswordAutoCleared?"yes":"no");
    },400);
  });
})();
</script>
""" % (json.dumps(status), json.dumps(config))

        dom = self.run_page(prelude)
        self.assertIn('data-test-pwd="********"', dom)
        self.assertIn('data-test-dirty-ssid="no"', dom)
        self.assertIn('data-test-dirty-pwd="no"', dom)
        self.assertIn('data-test-auto-cleared="no"', dom)

    def test_advanced_editor_edit_during_config_load_is_preserved(self):
        status, config = self.setup_values()
        status["mode"] = "lan"
        prelude = """
<script>
(function(){
  var status=%s,config=%s;
  function response(value){
    return {ok:true,status:200,json:function(){return Promise.resolve(value)}};
  }
  window.fetch=function(path){
    if(path==="/api/status")return Promise.resolve(response(status));
    if(path==="/api/config")return new Promise(function(resolve){
      setTimeout(function(){resolve(response(config))},500);
    });
    return Promise.resolve({ok:false,status:404,json:function(){return Promise.resolve({})}});
  };
  window.addEventListener("load",function(){
    setTimeout(function(){
      var name=document.querySelector('#v-app [data-k="name"]');
      name.value="Early App Edit";
      name.dispatchEvent(new Event("input",{bubbles:true}));
    },75);
    setTimeout(function(){
      var body=document.body,name=document.querySelector('#v-app [data-k="name"]');
      body.setAttribute("data-test-name",name.value);
      body.setAttribute("data-test-dirty-name",st.dirty.name||"");
      body.setAttribute("data-test-capture",st.configLoadCapture===null?"clear":"set");
    },850);
  });
})();
</script>
""" % (json.dumps(status), json.dumps(config))

        dom = self.run_page(prelude)
        self.assertIn('data-test-name="Early App Edit"', dom)
        self.assertIn('data-test-dirty-name="Early App Edit"', dom)
        self.assertIn('data-test-capture="clear"', dom)

    def test_second_load_auto_password_clear_remains_restorable(self):
        status, config = self.setup_values()
        prelude = """
<script>
(function(){
  var status=%s,config=%s,configCalls=0;
  function response(value){
    return {ok:true,status:200,json:function(){return Promise.resolve(value)}};
  }
  window.fetch=function(path){
    if(path==="/api/status")return Promise.resolve(response(status));
    if(path==="/api/config"){
      configCalls++;
      if(configCalls===1)return Promise.resolve(response(config));
      return new Promise(function(resolve){
        setTimeout(function(){resolve(response(config))},400);
      });
    }
    if(path.indexOf("/api/scan")===0)return Promise.resolve(response({state:"done",networks:[]}));
    return Promise.resolve({ok:false,status:404,json:function(){return Promise.resolve({})}});
  };
  window.addEventListener("load",function(){
    // The setup wizard's first load has populated both password fields with
    // the stored-secret sentinel. Entering the advanced editor starts a second
    // captured load; change its SSID while that request is still outstanding.
    setTimeout(function(){enterApp()},75);
    setTimeout(function(){
      var ssid=document.getElementById("app-ssid");
      ssid.value="OtherNet";
      ssid.dispatchEvent(new Event("input",{bubbles:true}));
    },125);
    setTimeout(function(){
      var body=document.body,owns=Object.prototype.hasOwnProperty;
      var ssid=document.getElementById("app-ssid");
      var pwd=document.getElementById("app-wifi-pwd");

      ssid.value="StoredNet";
      ssid.dispatchEvent(new Event("input",{bubbles:true}));
      body.setAttribute("data-test-restored-pwd",pwd.value);
      body.setAttribute("data-test-restored-dirty-ssid",
                        owns.call(st.dirty,"wifi.ssid")?"yes":"no");
      body.setAttribute("data-test-restored-dirty-pwd",
                        owns.call(st.dirty,"wifi.pwd")?"yes":"no");
      body.setAttribute("data-test-restored-auto",
                        st.wifiPasswordAutoCleared?"yes":"no");

      // A real password edit after an automatic clear is authoritative. It
      // must not be replaced with the sentinel when the SSID returns to the
      // stored network.
      ssid.value="OtherNet";
      ssid.dispatchEvent(new Event("input",{bubbles:true}));
      pwd.value="manual-secret";
      pwd.dispatchEvent(new Event("input",{bubbles:true}));
      ssid.value="StoredNet";
      ssid.dispatchEvent(new Event("input",{bubbles:true}));
      body.setAttribute("data-test-manual-pwd",pwd.value);
      body.setAttribute("data-test-manual-dirty-ssid",
                        owns.call(st.dirty,"wifi.ssid")?"yes":"no");
      body.setAttribute("data-test-manual-dirty-pwd",
                        owns.call(st.dirty,"wifi.pwd")?"yes":"no");
      body.setAttribute("data-test-manual-auto",
                        st.wifiPasswordAutoCleared?"yes":"no");
      body.setAttribute("data-test-config-calls",String(configCalls));
      body.setAttribute("data-test-capture",
                        st.configLoadCapture===null?"clear":"set");
    },700);
  });
})();
</script>
""" % (json.dumps(status), json.dumps(config))

        dom = self.run_page(prelude)
        self.assertIn('data-test-restored-pwd="********"', dom)
        self.assertIn('data-test-restored-dirty-ssid="no"', dom)
        self.assertIn('data-test-restored-dirty-pwd="no"', dom)
        self.assertIn('data-test-restored-auto="no"', dom)
        self.assertIn('data-test-manual-pwd="manual-secret"', dom)
        self.assertIn('data-test-manual-dirty-ssid="no"', dom)
        self.assertIn('data-test-manual-dirty-pwd="yes"', dom)
        self.assertIn('data-test-manual-auto="no"', dom)
        self.assertIn('data-test-config-calls="2"', dom)
        self.assertIn('data-test-capture="clear"', dom)

    def test_delayed_app_load_preserves_only_edited_radio_field(self):
        status, config = self.setup_values()
        status["mode"] = "lan"
        prelude = """
<script>
(function(){
  var status=%s,config=%s;
  function response(value){
    return {ok:true,status:200,json:function(){return Promise.resolve(value)}};
  }
  window.fetch=function(path){
    if(path==="/api/status")return Promise.resolve(response(status));
    if(path==="/api/config")return new Promise(function(resolve){
      setTimeout(function(){resolve(response(config))},500);
    });
    return Promise.resolve({ok:false,status:404,json:function(){return Promise.resolve({})}});
  };
  window.addEventListener("load",function(){
    setTimeout(function(){
      var sf=document.querySelector('#v-app [data-rg="sf"]');
      sf.value="9";
      sf.dispatchEvent(new Event("input",{bubbles:true}));
    },75);
    setTimeout(function(){
      var body=document.body;
      function radioValue(key){
        return document.querySelector('#v-app [data-rg="'+key+'"]').value;
      }
      body.setAttribute("data-test-radio-freq",radioValue("freq"));
      body.setAttribute("data-test-radio-bw",radioValue("bw"));
      body.setAttribute("data-test-radio-sf",radioValue("sf"));
      body.setAttribute("data-test-radio-cr",radioValue("cr"));
      body.setAttribute("data-test-radio-orig",st.orig.radio);
      body.setAttribute("data-test-radio-dirty",st.dirty.radio||"");
      body.setAttribute("data-test-capture",
                        st.configLoadCapture===null?"clear":"set");
    },850);
  });
})();
</script>
""" % (json.dumps(status), json.dumps(config))

        dom = self.run_page(prelude)
        self.assertIn('data-test-radio-freq="910.525"', dom)
        self.assertIn('data-test-radio-bw="62.5"', dom)
        self.assertIn('data-test-radio-sf="9"', dom)
        self.assertIn('data-test-radio-cr="5"', dom)
        self.assertIn('data-test-radio-orig="910.525,62.5,7,5"', dom)
        self.assertIn('data-test-radio-dirty="910.525,62.5,9,5"', dom)
        self.assertIn('data-test-capture="clear"', dom)

    def test_closed_picker_ignores_late_scan_response(self):
        status, config = self.setup_values()
        prelude = """
<script>
(function(){
  var status=%s,config=%s;
  function response(value){
    return {ok:true,status:200,json:function(){return Promise.resolve(value)}};
  }
  window.fetch=function(path){
    if(path==="/api/status")return Promise.resolve(response(status));
    if(path==="/api/config")return Promise.resolve(response(config));
    if(path.indexOf("/api/scan")===0)return new Promise(function(resolve){
      setTimeout(function(){resolve(response({
        state:"done",networks:[{ssid:"LateNet",rssi:-40,enc:true,channel:6}]
      }))},450);
    });
    return Promise.resolve({ok:false,status:404,json:function(){return Promise.resolve({})}});
  };
  window.addEventListener("load",function(){
    setTimeout(function(){
      closeScan();
      document.getElementById("scan-list").textContent="closed";
    },100);
    setTimeout(function(){
      var body=document.body,panel=document.getElementById("scan-panel");
      body.setAttribute("data-test-scan-hidden",panel.getAttribute("aria-hidden"));
      body.setAttribute("data-test-scan-expanded",document.getElementById("wz-scan-btn").getAttribute("aria-expanded"));
      body.setAttribute("data-test-network-count",document.querySelectorAll("#scan-list .net").length);
      body.setAttribute("data-test-scan-text",document.getElementById("scan-list").textContent);
    },800);
  });
})();
</script>
""" % (json.dumps(status), json.dumps(config))

        dom = self.run_page(prelude)
        self.assertIn('data-test-scan-hidden="true"', dom)
        self.assertIn('data-test-scan-expanded="false"', dom)
        self.assertIn('data-test-network-count="0"', dom)
        self.assertIn('data-test-scan-text="closed"', dom)

    def test_newer_config_load_wins_when_older_reply_finishes_last(self):
        status, old_config = self.setup_values()
        new_config = json.loads(json.dumps(old_config))
        new_config["wifi"]["ssid"] = "AppNet"
        prelude = """
<script>
(function(){
  var status=%s,oldConfig=%s,newConfig=%s,configCalls=0;
  function response(value){
    return {ok:true,status:200,json:function(){return Promise.resolve(value)}};
  }
  window.fetch=function(path){
    if(path==="/api/status")return Promise.resolve(response(status));
    if(path==="/api/config"){
      configCalls++;
      if(configCalls===1)return new Promise(function(resolve){
        setTimeout(function(){resolve(response(oldConfig))},500);
      });
      return Promise.resolve(response(newConfig));
    }
    if(path.indexOf("/api/scan")===0)return Promise.resolve(response({state:"done",networks:[]}));
    return Promise.resolve({ok:false,status:404,json:function(){return Promise.resolve({})}});
  };
  window.addEventListener("load",function(){
    setTimeout(function(){enterApp()},75);
    setTimeout(function(){
      var body=document.body;
      body.setAttribute("data-test-config-calls",configCalls);
      body.setAttribute("data-test-orig-ssid",st.orig["wifi.ssid"]);
      body.setAttribute("data-test-field-ssid",document.getElementById("app-ssid").value);
      body.setAttribute("data-test-scan-hidden",document.getElementById("scan-panel").getAttribute("aria-hidden"));
      body.setAttribute("data-test-capture",st.configLoadCapture===null?"clear":"set");
    },800);
  });
})();
</script>
""" % (json.dumps(status), json.dumps(old_config), json.dumps(new_config))

        dom = self.run_page(prelude)
        self.assertIn('data-test-config-calls="2"', dom)
        self.assertIn('data-test-orig-ssid="AppNet"', dom)
        self.assertIn('data-test-field-ssid="AppNet"', dom)
        self.assertIn('data-test-scan-hidden="true"', dom)
        self.assertIn('data-test-capture="clear"', dom)


if __name__ == "__main__":
    unittest.main()
