#!/usr/bin/env python3

"""Optional real-browser checks for WebConfig's asynchronous setup races."""

import gzip
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


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


def run_browser(args):
    options = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(args, **options)
    try:
        stdout, stderr = process.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        # Chrome can leave renderer processes writing to its temporary profile.
        # Kill the whole tree before TemporaryDirectory tries to remove it.
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=False)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            process.kill()
        process.communicate()
        raise
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


def embedded_page():
    header = HEADER.read_text(encoding="utf-8")
    length = int(re.search(r"WEBCONFIG_HTML_GZ_LEN = (\d+);", header).group(1))
    array = header.split("const uint8_t WEBCONFIG_HTML_GZ[] PROGMEM = {", 1)[1]
    blob = bytes(
        int(value, 16) for value in re.findall(r"0x([0-9a-f]{2})", array)
    )[:length]
    return gzip.decompress(blob).decode("utf-8")


class WebConfigUiRuntimeTest(unittest.TestCase):
    def test_saved_network_recovery_requires_login_before_setup_wizard(self):
        status, config = self.setup_values()
        status.update(auth=False, mode="setup", needs_setup=False,
                      needs_password=False, password_supported=True)
        prelude = r'''<script>
(function(){
  var status=%s,config=%s,configReads=0,logins=0;
  window.fetch=function(path,options){
    var value=config;
    if(path==="/api/status")value=status;
    else if(path==="/api/config")configReads++;
    else if(path==="/api/login"){
      logins++;
      if(JSON.parse(options.body).password!=="existing-secret")throw new Error("wrong password submitted");
      value={ok:true};
    }else if(path.indexOf("/api/scan")===0)value={networks:[]};
    return Promise.resolve({ok:true,status:200,json:function(){return Promise.resolve(value)}});
  };
  window.addEventListener("load",function(){
    setTimeout(function(){
      var body=document.body;
      body.setAttribute("data-test-recovery-login-visible",!document.getElementById("v-login").classList.contains("hide"));
      body.setAttribute("data-test-recovery-wizard-before",!document.getElementById("v-wizard").classList.contains("hide"));
      body.setAttribute("data-test-recovery-config-before",configReads);
      document.getElementById("login-pwd").value="existing-secret";
      document.getElementById("login-form").dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}));
    },200);
    setTimeout(function(){
      var body=document.body;
      body.setAttribute("data-test-recovery-wizard-after",!document.getElementById("v-wizard").classList.contains("hide"));
      body.setAttribute("data-test-recovery-app-after",!document.getElementById("v-app").classList.contains("hide"));
      body.setAttribute("data-test-recovery-logins",logins);
      body.setAttribute("data-test-recovery-config-after",configReads);
      body.setAttribute("data-test-recovery-password-cleared",document.getElementById("login-pwd").value==="");
    },800);
  });
})();
</script>''' % (json.dumps(status), json.dumps(config))
        dom = self.run_page(prelude)
        for attribute, value in (("login-visible", "true"), ("wizard-before", "false"),
                                 ("config-before", "0"), ("wizard-after", "true"),
                                 ("app-after", "false"), ("logins", "1"),
                                 ("config-after", "1"), ("password-cleared", "true")):
            self.assertIn('data-test-recovery-%s="%s"' % (attribute, value), dom)

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

        for attempt in range(2):
            with tempfile.TemporaryDirectory(prefix="meshcore-webconfig-runtime-",
                                             ignore_cleanup_errors=True) as tmp:
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
                try:
                    result = run_browser(args)
                except subprocess.TimeoutExpired:
                    if attempt:
                        self.fail("Headless browser timed out twice")
                    continue
            break

        stderr = result.stderr.decode("utf-8", "replace")
        self.assertEqual(result.returncode, 0, stderr)
        dom = result.stdout.decode("utf-8", "replace")
        self.assertNotIn("data-test-error=", dom)
        return dom

    def test_browser_timeout_retries_with_fresh_profile(self):
        completed = subprocess.CompletedProcess(
            ["browser"], 0, b"<html><body>retry succeeded</body></html>", b""
        )
        with mock.patch(__name__ + ".BROWSER", "browser"), mock.patch(
            __name__ + ".run_browser",
            side_effect=[subprocess.TimeoutExpired("browser", 30), completed],
        ) as launch:
            self.assertIn("retry succeeded", self.run_page(""))
        profiles = [
            next(arg for arg in call.args[0] if arg.startswith("--user-data-dir="))
            for call in launch.call_args_list
        ]
        self.assertEqual(len(profiles), 2)
        self.assertNotEqual(*profiles)
        for profile in profiles:
            self.assertFalse(Path(profile.split("=", 1)[1]).exists())

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


    def test_password_save_receipt_reauth_preserves_results_and_never_resends(self):
        for case in ("saved", "partial", "failed", "legacy401", "login_retry", "reboot"):
            with self.subTest(case=case):
                status, config = self.setup_values()
                status.update(mode="setup" if case == "legacy401" else "lan")
                prelude = r'''<script>
(function(){
  var kind=%s,status=%s,config=%s,submitted=null,posts=0,polls=0,logins=0;
  var authorized=true,wrongLogin=false,earlyRefresh=0,storageWrites=0,late=null;
  if(window.Storage)Storage.prototype.setItem=function(){storageWrites++;throw new Error("persistent password storage")};
  function response(value,code){code=code||200;return Promise.resolve({ok:code<400,status:code,json:function(){return Promise.resolve(value)}})}
  window.fetch=function(path,options){
    if(path==="/api/status")return response(status);
    if(path==="/api/late")return new Promise(function(resolve){late=resolve});
    if(path==="/api/config"&&options&&options.method==="POST"){
      posts++;submitted=JSON.parse(options.body);authorized=kind==="failed";
      return response({state:"pending",reqid:submitted.reqid},202);
    }
    if(path.indexOf("/api/config/result?")===0){
      polls++;
      if(path.indexOf("reqid="+submitted.reqid)<0)throw new Error("lost request id");
      if(kind==="legacy401"&&!authorized)return response({error:"auth"},401);
      if(polls===1)return response({state:"pending",reqid:submitted.reqid});
      return response({state:"done",reqid:submitted.reqid,all_ok:kind!=="partial"&&kind!=="failed",
        reboot:kind==="reboot",reauth_required:kind!=="failed"&&!authorized,results:[
          {key:"password",reply:kind==="failed"?"Error: save unconfirmed; reboot may differ":"OK"},
          {key:"name",reply:kind==="partial"?(authorized?"Error: rejected name":"Error"):"OK"}]});
    }
    if(path==="/api/login"){
      logins++;
      if(JSON.parse(options.body).password!=="new-admin")wrongLogin=true;
      if(kind==="login_retry"&&logins===1)return response({error:"try the current password"},401);
      authorized=true;return response({ok:true});
    }
    if(path==="/api/config"){
      if(submitted&&!authorized){earlyRefresh++;return response({error:"auth"},401)}
      return response(config);
    }
    if(path.indexOf("/api/scan")===0)return response({state:"done",networks:[]});
    return response({},404);
  };
  window.addEventListener("load",function(){
    setTimeout(function(){
      if(kind==="saved")api("/api/late").catch(function(){});
      st.dirty.password="new-admin";st.dirty.name="ChangedName";
      document.querySelectorAll('[data-k="password"],[data-cfm="password"]').forEach(function(el){el.value="new-admin"});
      saveChanges({reboot:kind==="partial"||kind==="failed"});
    },120);
    setTimeout(function(){
      if(late)late({ok:false,status:401,json:function(){return Promise.resolve({error:"auth"})}});
      if(kind==="login_retry"){
        document.body.setAttribute("data-test-intermediate-note",document.getElementById("login-note").textContent);
        document.body.setAttribute("data-test-intermediate-login",!document.getElementById("v-login").classList.contains("hide"));
        document.getElementById("login-pwd").value="new-admin";
        document.getElementById("login-form").dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}));
      }
    },950);
    setTimeout(function(){
      var body=document.body,fields=document.querySelectorAll('[data-k="password"],[data-cfm="password"]');
      body.setAttribute("data-test-posts",posts);body.setAttribute("data-test-polls",polls);
      body.setAttribute("data-test-logins",logins);body.setAttribute("data-test-wrong-login",wrongLogin);
      body.setAttribute("data-test-early-refresh",earlyRefresh);body.setAttribute("data-test-storage-writes",storageWrites);
      body.setAttribute("data-test-login-visible",!document.getElementById("v-login").classList.contains("hide"));
      body.setAttribute("data-test-wizard-visible",!document.getElementById("v-wizard").classList.contains("hide"));
      body.setAttribute("data-test-password-cleared",Array.prototype.every.call(fields,function(el){return el.value===""}));
      body.setAttribute("data-test-password-dirty",Object.prototype.hasOwnProperty.call(st.dirty,"password"));
      body.setAttribute("data-test-name-dirty",Object.prototype.hasOwnProperty.call(st.dirty,"name"));
      body.setAttribute("data-test-name-error",!!document.querySelector('#v-app [data-k="name"]').closest(".f").querySelector(".chip.err"));
      body.setAttribute("data-test-name-detail",document.querySelector('#v-app [data-k="name"]').closest(".f").textContent.indexOf("rejected name")>=0);
      body.setAttribute("data-test-password-error",!!document.querySelector('#v-app [data-k="password"]').closest(".f").querySelector(".chip.err"));
      body.setAttribute("data-test-reboot",document.getElementById("reboot-ov").classList.contains("show"));
      body.setAttribute("data-test-login-note",document.getElementById("login-note").textContent);
    },1500);
  });
})();
</script>''' % (json.dumps(case), json.dumps(status), json.dumps(config))
                dom = self.run_page(prelude, virtual_time=1900)
                for attr, expected in (("posts", "1"), ("wrong-login", "false"),
                                       ("early-refresh", "0"), ("storage-writes", "0"),
                                       ("login-visible", "false")):
                    self.assertIn(f'data-test-{attr}="{expected}"', dom)
                self.assertIn('data-test-logins="%s"' % (
                    "0" if case == "failed" else "2" if case == "login_retry" else "1"), dom)
                self.assertIn('data-test-polls="%s"' % ("2" if case in ("failed", "legacy401") else "3"), dom)
                self.assertIn('data-test-reboot="%s"' % str(case == "reboot").lower(), dom)
                self.assertIn('data-test-password-cleared="%s"' % str(case != "failed").lower(), dom)
                self.assertIn('data-test-password-dirty="%s"' % str(case == "failed").lower(), dom)
                self.assertIn('data-test-wizard-visible="%s"' % str(case == "legacy401").lower(), dom)
                if case == "partial":
                    self.assertIn('data-test-name-dirty="true"', dom)
                    self.assertIn('data-test-name-error="true"', dom)
                    self.assertIn('data-test-name-detail="true"', dom)
                    self.assertIn("some settings were rejected. The node will not reboot.", dom)
                if case == "failed":
                    self.assertIn('data-test-password-error="true"', dom)
                if case == "login_retry":
                    self.assertIn('data-test-intermediate-login="true"', dom)
                    self.assertIn('data-test-intermediate-note="Settings saved.', dom)

    def test_cli_password_receipt_uses_last_success_and_stream_never_replays(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                status, config = self.setup_values()
                status.update(mode="lan", cli=True, terminal_stream=streaming)
                prelude = r'''<script>
(function(){
  var streaming=%s,status=%s,config=%s,submitted=null,posts=0,logins=0,wrongLogin=false,authorized=true,earlyRefresh=0;
  function response(value,code){code=code||200;return Promise.resolve({ok:code<400,status:code,json:function(){return Promise.resolve(value)}})}
  window.fetch=function(path,options){
    if(path==="/api/status")return response(status);
    if(path==="/api/config"){
      if(!authorized){earlyRefresh++;throw new Error("config read before reauthentication")}
      return response(config);
    }
    if((path==="/api/cli"||path==="/api/terminal")&&options&&options.method==="POST"){
      posts++;submitted=JSON.parse(options.body);authorized=false;return response({state:"pending"},202);
    }
    if(path.indexOf("/api/terminal?")===0)return response({error:"auth"},401);
    if(path.indexOf("/api/cli/result?")===0){
      var second=path.indexOf("from=2")>=0;
      return response({state:second?"done":"running",reqid:submitted.reqid,reauth_required:!authorized,
        all_ok:!second,reboot:false,reboot_withheld:second,results:second?[
          {ok:false,reply:authorized?"Error: rejected password":"Error"},{ok:true,reply:"OK - reboot queued"}]:[
          {ok:true,reply:"OK"},{ok:true,reply:"> ******** (sign in again)"}]});
    }
    if(path==="/api/login"){
      logins++;wrongLogin=wrongLogin||JSON.parse(options.body).password!=="first-new";
      authorized=true;return response({ok:true});
    }
    return response({},404);
  };
  window.addEventListener("load",function(){
    setTimeout(function(){cliRun(streaming?["password first-new","set freq 123"]:["password first-new","get wifi.pwd","password rejected-new","reboot"])},120);
    setTimeout(function(){
      var body=document.body;
      body.setAttribute("data-test-posts",posts);body.setAttribute("data-test-logins",logins);body.setAttribute("data-test-early-refresh",earlyRefresh);
      body.setAttribute("data-test-wrong-login",wrongLogin);body.setAttribute("data-test-busy",cli.busy);
      body.setAttribute("data-test-login-visible",!document.getElementById("v-login").classList.contains("hide"));
      body.setAttribute("data-test-reboot",document.getElementById("reboot-ov").classList.contains("show"));
      body.setAttribute("data-test-stream-reset",!cliStream.session&&!cliStream.job);
      body.setAttribute("data-test-withheld",document.getElementById("term-out").textContent.indexOf("Not rebooting")>=0);
      body.setAttribute("data-test-error-detail",document.getElementById("term-out").textContent.indexOf("rejected password")>=0);
    },1400);
  });
})();
</script>''' % (json.dumps(streaming), json.dumps(status), json.dumps(config))
                dom = self.run_page(prelude, virtual_time=1800)
                for attr, expected in (("posts", "1"), ("logins", "1"),
                                       ("wrong-login", "false"), ("early-refresh", "0"), ("busy", "false"),
                                       ("login-visible", "false"), ("reboot", "false")):
                    self.assertIn(f'data-test-{attr}="{expected}"', dom)
                self.assertIn('data-test-stream-reset="true"', dom)
                if not streaming:
                    self.assertIn('data-test-withheld="true"', dom)
                    self.assertIn('data-test-error-detail="true"', dom)

    def test_typed_and_pasted_cli_password_preserves_argument_bytes_for_reauth(self):
        for entry in ("typed", "whole_paste", "partial_single", "partial_block"):
            with self.subTest(entry=entry):
                status, config = self.setup_values()
                status.update(mode="lan", cli=True, terminal_stream=False)
                prelude = r'''<script>
(function(){
  var entry=%s,status=%s,config=%s,submitted=null,posts=0,logins=0,polls=0,authorized=true;
  var expected=entry==="typed"?["password  typed  "]:entry==="whole_paste"?["PaSsWoRd  pasted  ","get name"]
    :entry==="partial_single"?["password suffix  "]:["password suffix  ","get name"];
  var exactCommands=false,exactLogin=false,exactContext=false;
  function response(value,code){code=code||200;return Promise.resolve({ok:code<400,status:code,json:function(){return Promise.resolve(value)}})}
  window.fetch=function(path,options){
    if(path==="/api/status")return response(status);
    if(path==="/api/config")return response(config);
    if(path==="/api/cli"&&options&&options.method==="POST"){
      posts++;submitted=JSON.parse(options.body);authorized=false;
      exactCommands=JSON.stringify(submitted.cmds)===JSON.stringify(expected);
      exactContext=cliPasswordContext(submitted.cmds).password===expected[0].slice(9);
      return response({state:"pending",reqid:submitted.reqid},202);
    }
    if(path.indexOf("/api/cli/result?")===0){
      polls++;
      if(path.indexOf("reqid="+submitted.reqid)<0)throw new Error("lost request id");
      return response({state:"done",reqid:submitted.reqid,all_ok:true,reauth_required:!authorized,
        results:submitted.cmds.map(function(){return{ok:true,reply:"OK"}})});
    }
    if(path==="/api/login"){
      logins++;exactLogin=JSON.parse(options.body).password===expected[0].slice(9);
      authorized=exactLogin;return response(exactLogin?{ok:true}:{error:"wrong password"},exactLogin?200:401);
    }
    return response({},404);
  };
  window.addEventListener("load",function(){
    setTimeout(function(){
      var input=document.getElementById("term-in");
      if(entry!=="typed"){
        input.value=entry==="whole_paste"?"":"password ";
        var event=new Event("paste",{bubbles:true,cancelable:true});
        Object.defineProperty(event,"clipboardData",{value:{getData:function(){return entry==="whole_paste"
          ?"  > PaSsWoRd  pasted  \r\n  get name  \t\r\n # comment\r\n"
          :entry==="partial_single"?"suffix  \n":"suffix  \nget name"}}});
        input.dispatchEvent(event);
        if(entry==="partial_single")input.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true,cancelable:true}));
      }else{
        input.value="  password  typed  ";
        input.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true,cancelable:true}));
      }
      var go=document.querySelector(".term-cfm .go");if(go)go.click();
    },120);
    setTimeout(function(){
      var body=document.body;
      body.setAttribute("data-test-posts",posts);body.setAttribute("data-test-logins",logins);body.setAttribute("data-test-polls",polls);
      body.setAttribute("data-test-exact-commands",exactCommands);body.setAttribute("data-test-exact-context",exactContext);
      body.setAttribute("data-test-exact-login",exactLogin);body.setAttribute("data-test-busy",cli.busy);
      body.setAttribute("data-test-login-visible",!document.getElementById("v-login").classList.contains("hide"));
      body.setAttribute("data-test-normal-trim",cliCommandLine("  get name  \t")==="get name");
      body.setAttribute("data-test-empty-password",cliParse("  PASSWORD   \n")[0]==="PASSWORD   ");
      body.setAttribute("data-test-mixedcase-risk",cliRisks(["PaSsWoRd secret  "]).length===1);
      body.setAttribute("data-test-password-seen",cli.pwd===true);
    },600);
  });
})();
</script>''' % (json.dumps(entry), json.dumps(status), json.dumps(config))
                dom = self.run_page(prelude, virtual_time=900)
                for attr, expected in (("posts", "1"), ("logins", "1"), ("polls", "2"),
                                       ("exact-commands", "true"), ("exact-context", "true"),
                                       ("exact-login", "true"), ("busy", "false"),
                                       ("login-visible", "false"), ("normal-trim", "true"),
                                       ("empty-password", "true"), ("mixedcase-risk", "true"),
                                       ("password-seen", "true")):
                    self.assertIn(f'data-test-{attr}="{expected}"', dom)

    def test_setup_security_banner_and_live_password_preview(self):
        banners = {
            "admin_password": "Setup WiFi uses this node's admin password.",
            "open_admin_login": "Setup WiFi is open; web configuration and firmware uploads still require this node's admin password.",
            "open_no_password": "Setup WiFi is open. This build has no admin password configured.",
            "unavailable": "Setup WiFi security is unavailable.",
            "missing": "Setup WiFi security is unavailable.",
            "pending": "Setup WiFi restart pending. Reconnect after the hotspot restarts to check its security.",
            "lan": "Setup WiFi uses this node's admin password.",
        }
        for security, banner in banners.items():
            with self.subTest(security=security):
                status, config = self.setup_values()
                status.update(mode="lan" if security == "lan" else "setup")
                if security != "missing":
                    status["ap_security"] = "admin_password" if security in ("lan", "pending") else security
                status["ap_restart_pending"] = security == "pending"
                vectors = ["abcdefgh", "a" * 15, "a" * 7, "a" * 16,
                           "é" * 4, "abc\x1fdefg", "~~~~~~~~", " " * 8, "abcd\x7fefg", ""]
                prelude = r'''<script>
(function(){
  var status=%s,config=%s,values=%s;
  window.fetch=function(path){var value=path==="/api/status"?status:path==="/api/config"?config:{};return Promise.resolve({ok:true,status:200,json:function(){return Promise.resolve(value)}})};
  window.addEventListener("load",function(){setTimeout(function(){
    var body=document.body,input=document.querySelector('#v-wizard [data-k="password"]');
    var previews=values.map(function(value){
      input.value=value;input.dispatchEvent(new Event("input",{bubbles:true}));
      var hints=Array.prototype.slice.call(document.querySelectorAll("[data-admin-ap-hint]"));
      var hidden=hints.every(function(h){return h.classList.contains("hide")});
      return hidden?"hidden":hints.every(function(h){return h.textContent==="Setup WiFi will use this password."})?"protected":hints.every(function(h){return h.textContent==="WiFi will be open; web configuration and firmware uploads still require this password."})?"open":"wrong";
    });
    body.setAttribute("data-test-banner",document.getElementById("setup-security").textContent);
    body.setAttribute("data-test-banner-hidden",document.getElementById("setup-security").classList.contains("hide"));
    body.setAttribute("data-test-previews",previews.join(","));
    body.setAttribute("data-test-login-default",document.querySelector("#v-login code").textContent);
  },200)});
})();
</script>''' % (json.dumps(status), json.dumps(config), json.dumps(vectors))
                dom = self.run_page(prelude, virtual_time=500)
                self.assertIn('data-test-banner="%s"' % banner, dom)
                self.assertIn('data-test-banner-hidden="%s"' % str(security == "lan").lower(), dom)
                self.assertIn('data-test-previews="%s"' % (
                    ",".join(["hidden"] * 10) if security == "lan" else
                    "protected,protected,open,open,open,open,protected,protected,open,hidden"), dom)
                self.assertIn('data-test-login-default="password"', dom)

    def test_setup_password_receipt_reconnect_guidance_matches_new_ap_policy(self):
        for password, protected, legacy in (("new-admin", True, False), ("éééé", False, False),
                                            ("éééé", False, True)):
            with self.subTest(protected=protected, legacy=legacy):
                status, config = self.setup_values()
                status.update(mode="setup", ap_security="admin_password")
                prelude = r'''<script>
(function(){
  var status=%s,config=%s,password=%s,legacy=%s,submitted=null,authSeen=null,posts=0,logins=0,polls=0,authorized=true;
  function response(value,code){code=code||200;return Promise.resolve({ok:code<400,status:code,json:function(){return Promise.resolve(value)}})}
  window.fetch=function(path,options){
    if(path==="/api/status")return response(status);
    if(path==="/api/config"&&options&&options.method==="POST"){
      posts++;submitted=JSON.parse(options.body);authorized=false;return response({state:"pending",reqid:submitted.reqid},202);
    }
    if(path.indexOf("/api/config/result?")===0){
      polls++;if(legacy&&!authorized)return response({error:"auth"},401);
      return response({state:"done",reqid:submitted.reqid,all_ok:true,reauth_required:!authorized,results:[{key:"password",reply:"OK"}]});
    }
    if(path==="/api/login"){
      logins++;authorized=JSON.parse(options.body).password===password;return response(authorized?{ok:true}:{error:"wrong password"},authorized?200:401);
    }
    if(path==="/api/config")return response(config);
    if(path.indexOf("/api/scan")===0)return response({state:"done",networks:[]});
    return response({},404);
  };
  window.addEventListener("load",function(){
    setTimeout(function(){
      var realReauth=reauthenticateOnce;reauthenticateOnce=function(auth,note,resume,keepView){authSeen=auth;return realReauth(auth,note,resume,keepView)};
      st.dirty.password=password;saveChanges();
    },120);
    setTimeout(function(){
      var body=document.body;
      body.setAttribute("data-test-posts",posts);body.setAttribute("data-test-logins",logins);body.setAttribute("data-test-polls",polls);
      body.setAttribute("data-test-pending-banner",document.getElementById("setup-security").textContent);
      body.setAttribute("data-test-note",document.getElementById("login-note").textContent);
      body.setAttribute("data-test-ram-secret-cleared",!!authSeen&&authSeen.password==="");
      body.setAttribute("data-test-ap-protected",authSeen&&authSeen.apProtected);
    },500);
  });
})();
</script>''' % (json.dumps(status), json.dumps(config), json.dumps(password), json.dumps(legacy))
                dom = self.run_page(prelude, virtual_time=800)
                for attr, expected in (("posts", "1"), ("logins", "1"), ("polls", "2"),
                                       ("ram-secret-cleared", "true"), ("ap-protected", str(protected).lower())):
                    self.assertIn(f'data-test-{attr}="{expected}"', dom)
                reconnect = ("Rejoin it with the new admin password." if protected else
                             "Join it without a WiFi password, then sign in with the new admin password.")
                initial_note = ("Settings were sent. Sign in to read the saved result." if legacy else
                                "Settings saved. The admin password changed.")
                self.assertIn('data-test-note="%s The setup WiFi may restart. %s"' %
                              (initial_note, reconnect), dom)
                target = ("rejoin with the new admin password." if protected else
                          "join without a WiFi password; web configuration and firmware uploads still require the new admin password.")
                self.assertIn('data-test-pending-banner="Setup WiFi restart pending. '
                              'After it restarts, %s"' % target, dom)

    def test_admin_password_validation_counts_utf8_bytes(self):
        status, config = self.setup_values()
        status.update(mode="lan")
        vectors = ["", "a" * 15, "a" * 16, "é" * 7 + "a", "é" * 8,
                   "🙂" * 3 + "abc", "🙂" * 4, "line\nbreak", "line\rbreak", "\ud800"]
        prelude = r'''<script>
(function(){
  var status=%s,config=%s,values=%s;
  window.fetch=function(path){var value=path==="/api/status"?status:path==="/api/config"?config:{};return Promise.resolve({ok:true,status:200,json:function(){return Promise.resolve(value)}})};
  window.addEventListener("load",function(){setTimeout(function(){
    var result=values.map(function(value){st.dirty.password=value;document.querySelectorAll('[data-cfm="password"]').forEach(function(el){el.value=value});return adminPwdError(false)?"invalid":"valid"});
    document.body.setAttribute("data-test-password-validation",result.join(","));
  },200)});
})();
</script>''' % (json.dumps(status), json.dumps(config), json.dumps(vectors))
        dom = self.run_page(prelude)
        self.assertIn('data-test-password-validation="valid,valid,invalid,valid,invalid,valid,invalid,invalid,invalid,invalid"', dom)


if __name__ == "__main__":
    unittest.main()
