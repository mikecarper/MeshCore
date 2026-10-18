"""Execute the vendored uploader and pinned TCP listener restart path."""
import importlib.util
from pathlib import Path
import re
import urllib.request
import unittest

from test_replay_reset_integration import extract_braced
import test_wifi_ota_start as wifi_start

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("async_web_fix", ROOT/"scripts/esp32_async_web_fix.py")
fix=importlib.util.module_from_spec(spec)
spec.loader.exec_module(fix)


class AsyncWebLifecycleTest(unittest.TestCase):
    compile_and_run=wifi_start.WiFiOtaStartTest.compile_and_run

    def uploader_fixture(self):
        source=(ROOT/"arch/esp32/AsyncElegantOTA/src/AsyncElegantOTA.cpp").read_text()
        fixture=(ROOT/"test/fixtures/async_ota_completion.cpp").read_text()
        state="""static std::atomic<bool> ota_reboot_pending{false};
static std::atomic<bool> ota_uploads_enabled{false};
static std::atomic<bool> ota_upload_busy{false};"""
        # The two preprocessor branches for Update.begin each open the same
        # logical block, so extract this method by its next declaration.
        begin=source[source.index("void AsyncElegantOtaClass::begin("):source.index("// deprecated")]
        cache=ROOT/".pio/libdeps/Station_G2_repeater_observer_mqtt"
        web_file=cache/"ESPAsyncWebServer/src/WebServer.cpp"
        tcp_file=cache/"AsyncTCP/src/AsyncTCP.cpp"
        web=web_file.read_text() if web_file.exists() else urllib.request.urlopen(
            "https://raw.githubusercontent.com/ESP32Async/ESPAsyncWebServer/v3.12.0/src/WebServer.cpp",
            timeout=30).read().decode("ascii")
        tcp=tcp_file.read_text() if tcp_file.exists() else urllib.request.urlopen(
            "https://raw.githubusercontent.com/ESP32Async/AsyncTCP/v3.5.0/src/AsyncTCP.cpp",
            timeout=30).read().decode("ascii")
        # Exercise the pinned listener's actual default and AsyncTCP's close
        # condition while running the production multipart upload callbacks.
        accept=re.search(r"c->setRxTimeout\(\d+\);",web).group(0)
        rx=tcp[tcp.index("  // RX Timeout"):tcp.index("  // Everything is fine")]
        return (fixture.replace("@REBOOT_STATE@",state)
            .replace("@UPLOAD_STATE@",extract_braced(source,"struct OtaUploadState")+";")
            .replace("@AUTH@",extract_braced(source,"bool AsyncElegantOtaClass::authenticateRequest("))
            .replace("@BEGIN@",begin)
            .replace("@RESTART@",extract_braced(source,"void AsyncElegantOtaClass::restart()"))
            .replace("@ACCEPT_TIMEOUT@",accept).replace("@RX_TIMEOUT@",rx))

    def test_upload_response_drains_before_deferred_reboot(self):
        fixture=self.uploader_fixture()
        self.compile_and_run(fixture,"-DESP32=1")
        old=fixture.replace(" || request->getResponse()","")
        self.assertNotEqual(fixture,old,"The negative control must remove queued-response guards")
        with self.assertRaises(AssertionError):
            self.compile_and_run(old,"-DESP32=1")
        for body in ("OTA could not begin","Could not end OTA","OTA flash write failed"):
            with self.subTest(raw_serial_error=body):
                error_return='return request->send(400, "text/plain", "'+body+'");'
                blocked=fixture.replace(error_return,'Update.printError(Serial);\n'+error_return)
                self.assertNotEqual(fixture,blocked,"The negative control must restore a raw Serial print")
                with self.assertRaises(AssertionError):
                    self.compile_and_run(blocked,"-DESP32=1")

    def test_admitted_upload_survives_body_gap_but_abandoned_owner_expires(self):
        fixture=self.uploader_fixture()
        self.compile_and_run(fixture,"-DESP32=1","-DTEST_OTA_BODY_TIMEOUT=1")
        old=fixture.replace("request->client()->setRxTimeout(30);","")
        self.assertNotEqual(fixture,old,"The negative control must remove the production timeout fix")
        with self.assertRaises(AssertionError):
            self.compile_and_run(old,"-DESP32=1","-DTEST_OTA_BODY_TIMEOUT=1")

    def test_management_auth_precedes_all_routes_and_upload_side_effects(self):
        fixture = self.uploader_fixture()
        self.compile_and_run(fixture, "-DESP32=1", "-DTEST_OTA_MANAGEMENT_PASSWORD=1")
        # Exercise the actual chunk gate: omitting authentication must admit a
        # wrong password and fail the zero-flash-effects assertion above.
        gate = "        if (!authenticateRequest(request, &authenticated_generation)) return;"
        old = fixture.replace(gate, "        authenticated_generation = mesh::esp32::managementPassword::detail::state().current.generation;")
        self.assertNotEqual(old, fixture)
        with self.assertRaises(AssertionError):
            self.compile_and_run(old, "-DESP32=1", "-DTEST_OTA_MANAGEMENT_PASSWORD=1")

    def premature_commit_fixture(self, fixture):
        commit=extract_braced(fixture,"if (state && state->file_final")
        end=extract_braced(commit,"if (!Update.end(true))")
        final="static_cast<OtaUploadState*>(request->_tempObject)->file_final = true;"
        # Restore the old policy using the actual end/error block: commit at
        # file-final, before the complete multipart POST has been accepted.
        old=fixture.replace(commit,"",1).replace(final,end+"\n"
            +"static_cast<OtaUploadState*>(request->_tempObject)->committed = true;\n"
            +final,1)
        self.assertNotEqual(fixture,old,"The negative control must restore premature file commit")
        return old

    def test_second_file_rejection_does_not_commit_first_file_or_reboot(self):
        fixture=self.uploader_fixture()
        flags=("-DESP32=1","-DTEST_OTA_SECOND_FILE=1")
        self.compile_and_run(fixture,*flags)
        with self.assertRaisesRegex(AssertionError,r"Assertion (?:.* failed|failed:)") as failure:
            self.compile_and_run(self.premature_commit_fixture(fixture),*flags)
        self.assertRegex(str(failure.exception),r"Update\.ends==0 && Update\.boot_commits==0")

    def test_file_final_disconnect_before_post_completion_does_not_commit_or_reboot(self):
        fixture=self.uploader_fixture()
        flags=("-DESP32=1","-DTEST_OTA_INCOMPLETE_POST=1")
        self.compile_and_run(fixture,*flags)
        with self.assertRaisesRegex(AssertionError,r"Assertion (?:.* failed|failed:)") as failure:
            self.compile_and_run(self.premature_commit_fixture(fixture),*flags)
        self.assertRegex(str(failure.exception),r"Update\.ends==0 && Update\.boot_commits==0")

    def test_md5_survives_begin_and_rejected_digest_releases_update(self):
        fixture=self.uploader_fixture()
        self.compile_and_run(fixture,"-DESP32=1")
        guard=extract_braced(fixture,"if(!Update.setMD5(")
        marker="            #if defined(ESP8266)\n                int cmd"
        before_begin=fixture.replace(guard,"",1).replace(marker,guard+"\n"+marker,1)
        self.assertNotEqual(fixture,before_begin,"The negative control must set MD5 before begin")
        with self.assertRaises(AssertionError):
            self.compile_and_run(before_begin,"-DESP32=1")
        for cleanup in ("Update.abort();", "ota_upload_busy.store(false);"):
            with self.subTest(rejected_digest_cleanup=cleanup):
                missing=fixture.replace(guard,guard.replace(cleanup,"",1),1)
                self.assertNotEqual(fixture,missing,"The negative control must remove rejection cleanup")
                with self.assertRaises(AssertionError):
                    self.compile_and_run(missing,"-DESP32=1")

    def test_tcp_listener_can_rebind_with_completed_connections(self):
        cached=ROOT/".pio/libdeps/heltec_v4_repeater_observer_mqtt/AsyncTCP/src/AsyncTCP.cpp"
        source=(cached.read_text() if cached.exists() else urllib.request.urlopen(
            "https://raw.githubusercontent.com/ESP32Async/AsyncTCP/v3.5.0/src/AsyncTCP.cpp",
            timeout=30).read().decode("ascii"))
        patched=fix.patched_async_tcp(source)
        fixture=r'''
#include <cassert>
#define SO_REUSE 1
constexpr int SOF_REUSEADDR=4,ERR_OK=0,ERR_USE=-8;
using err_t=int;
struct tcp_pcb { int flags=0; };
struct tcpip_api_call_data {};
struct tcp_api_call_t : tcpip_api_call_data {
  tcp_pcb** pcb;
  struct { void* addr=nullptr; unsigned short port=80; } bind;
  int err=0;
};
bool active_listener=false,time_wait=true;
void ip_set_option(tcp_pcb* pcb,int flags) { pcb->flags|=flags; }
int tcp_bind(tcp_pcb* pcb,void*,unsigned short) {
  return active_listener || (time_wait && !(pcb->flags&SOF_REUSEADDR)) ? ERR_USE : ERR_OK;
}
int tcp_close(tcp_pcb*) { return ERR_OK; }
void tcp_abort(tcp_pcb*) { assert(false); }
@BIND@
int main() {
  tcp_pcb pcb,*pointer=&pcb;
  tcp_api_call_t message; message.pcb=&pointer;
  assert(_tcp_bind_api(&message)==ERR_OK && pointer==&pcb);
  active_listener=true;
  assert(_tcp_bind_api(&message)==ERR_USE && pointer==nullptr);
}
'''
        self.compile_and_run(fixture.replace("@BIND@",extract_braced(patched,"static err_t _tcp_bind_api(")))
        with self.assertRaises(AssertionError):
            self.compile_and_run(fixture.replace("@BIND@",extract_braced(source,"static err_t _tcp_bind_api(")))
        with self.assertRaisesRegex(RuntimeError,"changed pinned source"):
            fix.patched_async_tcp(source+"\n// unreviewed change\n")


if __name__=="__main__":
    unittest.main()
