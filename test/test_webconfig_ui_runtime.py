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
    def test_four_display_controls_and_pairing_capability(self):
        status, config = self.setup_values()
        config["display"] = {
            "mode": "button", "timeout": 23,
            "usb_mode": "on", "usb_timeout": 55, "pairing": False,
        }
        prelude = r'''<script>
window.fetch=function(path){
  var value=path==="/api/status"?%s:%s;
  return Promise.resolve({ok:true,status:200,json:function(){return Promise.resolve(value)}});
};
window.addEventListener("load",function(){
  setTimeout(function(){enterApp()},50);
  setTimeout(function(){
    var card=document.getElementById("display-settings"), body=document.body;
    body.setAttribute("data-test-display-visible",!card.classList.contains("hide"));
    body.setAttribute("data-test-display-count",card.querySelectorAll("[data-k]").length);
    body.setAttribute("data-test-battery-timeout",card.querySelector('[data-k="display.timeout"]').value);
    body.setAttribute("data-test-usb-timeout",card.querySelector('[data-k="display.usb.timeout"]').value);
    body.setAttribute("data-test-usb-disabled",card.querySelector('[data-k="display.usb.timeout"]').disabled);
    body.setAttribute("data-test-pairing-disabled",card.querySelector('option[value="pairing"]').disabled);
    var mode=card.querySelector('[data-k="display.mode"]');
    mode.value="off";mode.dispatchEvent(new Event("input",{bubbles:true}));
    body.setAttribute("data-test-battery-disabled",card.querySelector('[data-k="display.timeout"]').disabled);
    body.setAttribute("data-test-dirty-display",st.dirty["display.mode"]);
  },650);
});
</script>''' % (json.dumps(status), json.dumps(config))
        dom = self.run_page(prelude)
        for attribute, value in (
            ("display-visible", "true"), ("display-count", "4"),
            ("battery-timeout", "23"), ("usb-timeout", "55"),
            ("usb-disabled", "true"), ("pairing-disabled", "true"),
            ("battery-disabled", "true"), ("dirty-display", "off"),
        ):
            self.assertIn('data-test-%s="%s"' % (attribute, value), dom)

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

    def test_companion_console_runs_commands_and_uses_role_suggestions(self):
        status, config = self.setup_values()
        status.update(mode="lan", cli=True, mqtt=True, active_slots=5, runtime_slots=1,
                      password_supported=False)
        prelude = """
<script>
(function(){
  var status=%s,config=%s,submitted=null;
  function response(value){
    return Promise.resolve({ok:true,status:200,json:function(){return Promise.resolve(value)}});
  }
  window.fetch=function(path,options){
    if(path==="/api/status")return response(status);
    if(path==="/api/config")return response(config);
    if(path==="/api/presets")return response({presets:[]});
    if(path==="/api/cli"){
      submitted=JSON.parse(options.body);
      return response({state:"running",reqid:submitted.reqid,total:1});
    }
    if(path.indexOf("/api/cli/result?")===0)return response({
      state:"done",reqid:submitted.reqid,results:[{ok:true,reply:"companion-console-test-version"}]
    });
    return response({});
  };
  window.addEventListener("load",function(){
    setTimeout(function(){
      var button=document.querySelector('#tabs button[data-t="cli"]');
      document.body.setAttribute("data-test-cli-tab",button?"yes":"no");
      if(!button)return;
      button.click();
      document.getElementById("term-in").value="ver";
      cliSubmit();
    },75);
    setTimeout(function(){
      var table=cliTable().map(function(row){return row[0]});
      document.body.setAttribute("data-test-suggestions",JSON.stringify(table));
      document.body.setAttribute("data-test-submitted",submitted?submitted.cmds.join(","):"none");
      document.body.setAttribute("data-test-reply",document.getElementById("term-out").textContent);
      document.body.setAttribute("data-test-enum",cliEnum("wifi.cli").join(","));
      document.body.setAttribute("data-test-busy",String(cli.busy));
      var master=document.querySelector('[data-k="mqtt.enabled"]');
      document.body.setAttribute("data-test-mqtt-enabled",master?String(master.checked):"missing");
      document.body.setAttribute("data-test-mqtt-enum",cliEnum("mqtt.enabled").join(","));
      cliHelp();
      document.body.setAttribute("data-test-companion-help",document.getElementById("term-out").textContent);
      // Switching role must invalidate the cached table even with one slot.
      st.role="repeater";
      document.body.setAttribute("data-test-repeater-erase",String(cliTable().some(function(row){return row[0]==="erase"})));
    },500);
  });
})();
</script>
""" % (json.dumps(status), json.dumps(config))
        dom = self.run_page(prelude)
        self.assertIn('data-test-cli-tab="yes"', dom)
        self.assertIn('data-test-submitted="ver"', dom)
        self.assertIn("companion-console-test-version", dom)
        self.assertIn('data-test-busy="false"', dom)
        self.assertIn('data-test-enum="on,off"', dom)
        self.assertIn('data-test-repeater-erase="true"', dom)
        self.assertIn('data-test-mqtt-enabled="false"', dom)
        self.assertIn('data-test-mqtt-enum="on,off"', dom)
        import html
        help_text = html.unescape(re.search(
            r'data-test-companion-help="([^"]*)"', dom).group(1))
        self.assertIn("set mqtt.enabled on|off", help_text)
        self.assertNotIn("neighbors", help_text)
        self.assertNotIn("stats-core", help_text)
        suggestions = json.loads(html.unescape(re.search(
            r'data-test-suggestions="([^"]*)"', dom).group(1)))
        for command in ("set powersaving off", "get usb.logging", "set wifi.cli ",
                        "set mqtt1.preset ", "get name", "get radio", "erase",
                        "get prv.key", "get mqtt1.password", "get wifi.pwd",
                        "stats-core", "stats-radio", "stats-packets", "set freq "):
            self.assertIn(command, suggestions)
        for command in ("password ", "setperm ", "get acl",
                        "set bridge.enabled ", "set mqtt2.preset "):
            self.assertNotIn(command, suggestions)

    def test_stream_terminal_imports_long_cards_pages_lists_and_receives_late_replies(self):
        status, config = self.setup_values()
        status.update(mode="lan", cli=True, terminal_stream=True,
                      terminal_max_command=541, password_supported=False)
        prelude = """
<script>
(function(){
  var status=%s,config=%s,commands=[],sequence=0,output="",session="";
  function response(value){return Promise.resolve({ok:true,status:200,json:function(){return Promise.resolve(value)}})}
  window.fetch=function(path,options){
    if(path==="/api/status")return response(status);
    if(path==="/api/config")return response(config);
    if(path==="/api/presets")return response({presets:[]});
    if(path==="/api/terminal"){
      var p=JSON.parse(options.body);commands.push(p.command);sequence=p.seq;session=p.session;
      if(p.command.indexOf("import ")===0){output+="OK - contact import queued\\n";return Promise.reject(new Error("lost POST response"))}
      if(p.command==="list")for(var i=0;i<350;i++)output+="contact-"+i+" (Repeater)\\n";
      if(p.command.indexOf("to ")===0)output+="Selected recipient test\\n";
      if(p.command==="help")output+="firmware help: import, list, to, send\\n";
      return response({accepted:true});
    }
    if(path.indexOf("/api/terminal?")===0){
      var q=new URLSearchParams(path.split("?")[1]),from=Number(q.get("after")),text=output.slice(from,from+1024);
      return response({output:text,cursor:from+text.length,seq:sequence,done:true,closed:false,lost:false,more:from+text.length<output.length});
    }
    if(path.indexOf("/api/cli")===0)throw new Error("wrong CLI parser");
    return response({});
  };
  window.addEventListener("load",function(){
    setTimeout(function(){
      document.querySelector('#tabs button[data-t="cli"]').click();
      cliStreamRun(["import meshcore://"+"ab".repeat(255),"list","to test"]);
    },75);
    setTimeout(function(){output+="Late RF reply and ACK\\n"},300);
    setTimeout(function(){cliHelp()},550);
    setTimeout(function(){
      document.body.setAttribute("data-test-commands",JSON.stringify(commands));
      document.body.setAttribute("data-test-stream-output",document.getElementById("term-out").textContent);
      document.body.setAttribute("data-test-stream-busy",String(cli.busy));
      document.body.setAttribute("data-test-stream-session",session);
    },1000);
  });
})();
</script>
""" % (json.dumps(status), json.dumps(config))
        dom = self.run_page(prelude, virtual_time=1200)
        import html
        commands = json.loads(html.unescape(re.search(
            r'data-test-commands="([^"]*)"', dom).group(1)))
        self.assertEqual(commands, ["import meshcore://" + "ab" * 255,
                                    "list", "to test", "help"])
        output = html.unescape(re.search(
            r'data-test-stream-output="([^"]*)"', dom).group(1))
        self.assertIn("contact-0 (Repeater)", output)
        self.assertIn("contact-349 (Repeater)", output)
        self.assertEqual(output.count("Late RF reply and ACK"), 1)
        self.assertIn("firmware help: import, list, to, send", output)
        self.assertIn('data-test-stream-busy="false"', dom)

    def test_streamed_raw_log_keeps_entire_file_and_allows_slow_progress(self):
        status, config = self.setup_values()
        status.update(mode="lan", role="Repeater", cli=True, terminal_stream=True,
                      terminal_max_command=159, password_supported=False)
        prelude = """
<script>
(function(){
  var status=%s,config=%s,sequence=0,output="",offset=0;
  var dump="log-start\\n"+"packet-row\\n".repeat(10000)+"log-end\\n   EOF\\n";
  var now=Date.now;Date.now=function(){return now()+offset};
  function response(value){return Promise.resolve({ok:true,status:200,json:function(){return Promise.resolve(value)}})}
  window.fetch=function(path,options){
    if(path==="/api/status")return response(status);
    if(path==="/api/config")return response(config);
    if(path==="/api/presets")return response({presets:[]});
    if(path==="/api/terminal"){
      sequence=JSON.parse(options.body).seq;output=dump;
      return response({accepted:true});
    }
    if(path.indexOf("/api/terminal?")===0){
      var from=Number(new URLSearchParams(path.split("?")[1]).get("after"));
      var chunk=output.slice(from,from+3072),cursor=from+chunk.length;
      offset+=2000; // A complete dump takes over 30 seconds, but keeps progressing.
      return response({output:chunk,cursor:cursor,seq:sequence,done:cursor>=dump.length,
                       closed:false,lost:false,more:false});
    }
    return response({});
  };
  window.addEventListener("load",function(){
    setTimeout(function(){cliStreamRun(["log"])},75);
    setTimeout(function(){output+="later-output\\n"},6000);
    setTimeout(function(){
      var text=document.getElementById("term-out").textContent;
      document.body.setAttribute("data-test-complete-log",String(text.includes(dump)));
      document.body.setAttribute("data-test-later-output",String(text.includes("later-output")));
      document.body.setAttribute("data-test-log-busy",String(cli.busy));
      document.body.setAttribute("data-test-log-timeout",String(text.includes("could not be confirmed")));
    },6800);
  });
})();
</script>
""" % (json.dumps(status), json.dumps(config))
        dom = self.run_page(prelude, virtual_time=7000)
        self.assertIn('data-test-complete-log="true"', dom)
        self.assertIn('data-test-later-output="true"', dom)
        self.assertIn('data-test-log-busy="false"', dom)
        self.assertIn('data-test-log-timeout="false"', dom)

    def test_console_stays_hidden_when_disabled_or_on_setup_ap(self):
        for mode in ("lan", "setup"):
            with self.subTest(mode=mode):
                status, config = self.setup_values()
                status.update(mode=mode, cli=False)
                prelude = """
<script>
(function(){
  var status=%s,config=%s;
  window.fetch=function(path){
    var value=path==="/api/status"?status:path==="/api/config"?config:{state:"done",networks:[]};
    return Promise.resolve({ok:true,status:200,json:function(){return Promise.resolve(value)}});
  };
  window.addEventListener("load",function(){setTimeout(function(){
    document.body.setAttribute("data-test-cli-hidden",String(!document.querySelector('#tabs button[data-t="cli"]')));
  },500)});
})();
</script>
""" % (json.dumps(status), json.dumps(config))
                dom = self.run_page(prelude)
                self.assertIn('data-test-cli-hidden="true"', dom)

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

    def test_bluetooth_stealth_toggle_preserves_custom_mac(self):
        status, config = self.setup_values()
        status["mode"] = "lan"
        status["capabilities"] = (1 << 16) | (1 << 17)
        config["radio"]["bluetooth_mac"] = "C2:11:22:33:44:55"
        config["radio"]["bluetooth_stealth"] = False
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
    return Promise.resolve({ok:false,status:404,json:function(){return Promise.resolve({})}});
  };
  window.addEventListener("load",function(){
    setTimeout(function(){
      var flag=document.querySelector('#v-app [data-k="bluetooth.stealth"]');
      var mac=document.querySelector('#v-app [data-k="bluetooth.mac"]');
      var body=document.body;
      body.setAttribute("data-test-initial-flag",flag.value);
      flag.value="on";
      flag.dispatchEvent(new Event("input",{bubbles:true}));
      flag.dispatchEvent(new Event("change",{bubbles:true}));
      body.setAttribute("data-test-dirty-flag",st.dirty["bluetooth.stealth"]||"");
      body.setAttribute("data-test-dirty-keys",Object.keys(st.dirty).join(","));
      body.setAttribute("data-test-mac",mac.value);
      body.setAttribute("data-test-config-mac",cfgVal("bluetooth.mac"));
      flag.value="off";
      flag.dispatchEvent(new Event("input",{bubbles:true}));
      flag.dispatchEvent(new Event("change",{bubbles:true}));
      body.setAttribute("data-test-restored-dirty-count",Object.keys(st.dirty).length);
    },500);
  });
})();
</script>
""" % (json.dumps(status), json.dumps(config))

        dom = self.run_page(prelude)
        self.assertIn('data-test-initial-flag="off"', dom)
        self.assertIn('data-test-dirty-flag="on"', dom)
        self.assertIn('data-test-dirty-keys="bluetooth.stealth"', dom)
        self.assertIn('data-test-mac="C2:11:22:33:44:55"', dom)
        self.assertIn('data-test-config-mac="C2:11:22:33:44:55"', dom)
        self.assertIn('data-test-restored-dirty-count="0"', dom)

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
