#!/usr/bin/env python3
"""Exercise the real password reveal, authentication and rotation gates."""
from pathlib import Path
import unittest

import test_webconfig_recovery_auth as auth
from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]


class WebConfigPasswordRevealTest(unittest.TestCase):
    def test_explicit_session_origin_expiry_and_rotation(self):
        cpp = (ROOT / 'src/helpers/esp32/WebConfigServer.cpp').read_text()
        fixture = auth.WebConfigRecoveryAuthTest().fixture()
        fixture = fixture[:fixture.index('int main() {')]
        fixture = fixture.replace('  Header header; std::string body, reply; void* _tempObject=nullptr;',
            '  Header header; std::map<std::string, Header> headers; std::string body, reply; void* _tempObject=nullptr;')
        fixture = fixture.replace('struct AsyncWebServerRequest {',
            'static bool rotate_on_cookie = false;\nstruct AsyncWebServerRequest {')
        fixture = fixture.replace('  bool hasHeader(const char*) const { return !header.cookies.empty(); }\n'
            '  Header* getHeader(const char*) { return &header; }',
            '  bool hasHeader(const char* name) const { return !strcmp(name,"Cookie") ? !header.cookies.empty() : headers.count(name); }\n'
            '  Header* getHeader(const char* name) { if (!strcmp(name,"Cookie") && rotate_on_cookie) { rotate_on_cookie=false; commitCredential("rotated-secret"); } return !strcmp(name,"Cookie") ? &header : &headers.at(name); }')
        fixture = fixture.replace('  void handleLogin(AsyncWebServerRequest*);',
            '  void handleLogin(AsyncWebServerRequest*);\n  void handleAdminPasswordReveal(AsyncWebServerRequest*);')
        fixture = fixture.replace('struct AsyncWebServerResponse {',
            'static bool corrupt_cache_header = false;\nstruct AsyncWebServerResponse {')
        fixture = fixture.replace('bool addHeader(const char* key, const char* value) { headers[key]=value; return true; }',
            'bool addHeader(const char* key, const char* value) { headers[key]=(corrupt_cache_header && !strcmp(key,"Cache-Control")) ? "broken" : value; return true; }')
        # Force a real external credential rotation during response production.
        fixture = fixture.replace('struct AsyncResponseStream : AsyncWebServerResponse {',
            'static bool rotate_during_reveal = false, rotation_blocked = false;\nstruct AsyncResponseStream : AsyncWebServerResponse {')
        fixture = fixture.replace('size_t write(uint8_t value) { body.push_back(char(value)); return 1; }',
            'size_t write(uint8_t value) { if (rotate_during_reveal) { rotate_during_reveal=false; password::ChangeGuard change; rotation_blocked=!change.acquired(); if (change.acquired()) change.publish("rotated-secret"); } body.push_back(char(value)); return 1; }')
        fixture = fixture.replace('size_t write(const uint8_t* bytes, size_t count) { body.append(reinterpret_cast<const char*>(bytes), count); return count; }',
            'size_t write(const uint8_t* bytes, size_t count) { if (rotate_during_reveal) { rotate_during_reveal=false; password::ChangeGuard change; rotation_blocked=!change.acquired(); if (change.acquired()) change.publish("rotated-secret"); } body.append(reinterpret_cast<const char*>(bytes), count); return count; }')
        fixture += '\n' + extract_braced(cpp, 'void WebConfigServer::handleAdminPasswordReveal(')
        fixture += r'''
static void revealHeaders(AsyncWebServerRequest& req) {
  req.headers["X-WebConfig-Reveal"].cookies = "1";
  req.headers["Host"].cookies = "192.168.4.1";
  req.headers["Origin"].cookies = "http://192.168.4.1";
  req.headers["Sec-Fetch-Site"].cookies = "same-origin";
}
static void useCookie(AsyncWebServerRequest& req, const WebConfigServer& portal) {
  req.header.cookies = std::string("wcs=") + portal._session_token;
}
int main() {
  WebConfigServer portal;
  portal._mode = WebConfigServer::MODE_LAN;
  AsyncWebServerRequest login; login.password("existing-secret");
  portal.handleLogin(&login); assert(login.status == 200 && portal._session_token[0]);

  AsyncWebServerRequest empty; portal.handleAdminPasswordReveal(&empty); assert(empty.status == 403 && empty.reply.empty());
  revealHeaders(empty); portal.handleAdminPasswordReveal(&empty); assert(empty.status == 401 && empty.reply.empty());
  useCookie(empty, portal); portal.handleAdminPasswordReveal(&empty);
  assert(empty.status == 200 && empty.reply == "{\"password\":\"existing-secret\"}");
  assert(empty.response.headers["Cache-Control"] == "no-store");
  assert(empty.response.headers["X-Content-Type-Options"] == "nosniff");
  AsyncWebServerRequest cache_failure; revealHeaders(cache_failure); useCookie(cache_failure, portal);
  corrupt_cache_header = true; portal.handleAdminPasswordReveal(&cache_failure); corrupt_cache_header = false;
  assert(cache_failure.status == 503 && cache_failure.reply.empty());
  {
    password::ChangeGuard concurrent; assert(concurrent.acquired());
    AsyncWebServerRequest busy; revealHeaders(busy); useCookie(busy, portal);
    portal.handleAdminPasswordReveal(&busy); assert(busy.status == 503 && busy.reply.empty());
  }

  AsyncWebServerRequest cross; revealHeaders(cross); useCookie(cross, portal);
  cross.headers["Origin"].cookies = "http://attacker.invalid";
  portal.handleAdminPasswordReveal(&cross); assert(cross.status == 403 && cross.reply.empty());
  cross.headers["Origin"].cookies = "http://192.168.4.1";
  cross.headers["Sec-Fetch-Site"].cookies = "cross-site";
  portal.handleAdminPasswordReveal(&cross); assert(cross.status == 403 && cross.reply.empty());
  now_ms += WEBCONFIG_SESSION_TTL_MS + 1;
  AsyncWebServerRequest expired; revealHeaders(expired); useCookie(expired, portal);
  portal.handleAdminPasswordReveal(&expired); assert(expired.status == 401 && expired.reply.empty());

  // AP association allows provisioning, never access to the saved secret.
  WebConfigServer setup; setup._mode = WebConfigServer::MODE_SETUP;
  setup._setup_provisioning_privilege = true;
  password::Snapshot snapshot; assert(password::snapshot(snapshot));
  setup._ap_auth_generation = snapshot.generation;
  AsyncWebServerRequest ap; revealHeaders(ap);
  assert(setup.checkAuth(&ap)); setup.handleAdminPasswordReveal(&ap);
  assert(ap.status == 401 && ap.reply.empty());
  // Existing implicit-setup login remains unchanged; explicit login must prove
  // the password and create a real session.
  AsyncWebServerRequest implicit; implicit.password("wrong"); setup.handleLogin(&implicit);
  assert(implicit.status == 200 && !setup._session_token[0]);
  AsyncWebServerRequest explicit_login; explicit_login.password("wrong");
  explicit_login.headers["X-WebConfig-Explicit-Login"].cookies = "1";
  setup.handleLogin(&explicit_login); assert(explicit_login.status == 401 && !setup._session_token[0]);
  explicit_login.password("existing-secret"); setup.handleLogin(&explicit_login);
  assert(explicit_login.status == 200 && setup._session_token[0]);
  useCookie(ap, setup); setup.handleAdminPasswordReveal(&ap);
  assert(ap.status == 200 && ap.reply.find("existing-secret") != std::string::npos);
  AsyncWebServerRequest foreign; revealHeaders(foreign); useCookie(foreign, setup);
  foreign.socket.local_ip=9; setup.handleAdminPasswordReveal(&foreign);
  assert(foreign.status == 401 && foreign.reply.empty());

  // Forced explicit login still uses the normal per-client throttle.
  WebConfigServer throttled; throttled._mode=WebConfigServer::MODE_SETUP;
  throttled._setup_provisioning_privilege=true;
  assert(password::snapshot(snapshot)); throttled._ap_auth_generation=snapshot.generation;
  AsyncWebServerRequest guessing; guessing.socket.remote=42;
  guessing.headers["X-WebConfig-Explicit-Login"].cookies="1";
  for (int i=0; i<5; ++i) { guessing.password("wrong"); throttled.handleLogin(&guessing); assert(guessing.status==401); }
  guessing.password("existing-secret"); throttled.handleLogin(&guessing);
  assert(guessing.status==429 && !throttled._session_token[0]);

  // A setter outside WebConfig cannot change the credential while the
  // authorized response is produced.
  AsyncWebServerRequest race; revealHeaders(race); useCookie(race, setup);
  rotate_during_reveal = true; setup.handleAdminPasswordReveal(&race);
  assert(race.status == 200 && rotation_blocked && setup._session_token[0]);
  // A rotation between authentication and guard acquisition is detected.
  AsyncWebServerRequest pre_guard; revealHeaders(pre_guard); useCookie(pre_guard, setup);
  rotate_on_cookie = true; setup.handleAdminPasswordReveal(&pre_guard);
  assert(pre_guard.status == 401 && pre_guard.reply.empty());

  // JSON must escape arbitrary supported password bytes.
  WebConfigServer escaped; escaped._mode=WebConfigServer::MODE_LAN;
  resetCredential("a\"b\\cdef"); AsyncWebServerRequest escaped_login;
  escaped_login.body = "{\"password\":\"a\\\"b\\\\cdef\"}";
  escaped_login._tempObject = escaped_login.body.data();
  escaped.handleLogin(&escaped_login); assert(escaped_login.status == 200);
  AsyncWebServerRequest escaped_req; revealHeaders(escaped_req); useCookie(escaped_req, escaped);
  escaped.handleAdminPasswordReveal(&escaped_req);
  assert(escaped_req.status == 200);
  DynamicJsonDocument json(128); assert(!deserializeJson(json, escaped_req.reply));
  assert(!strcmp(json["password"], "a\"b\\cdef"));

  // Legacy passwordless auto-access cannot mint an authenticated secret view.
  WebConfigServer passwordless; passwordless._mode=WebConfigServer::MODE_LAN;
  resetCredential(""); AsyncWebServerRequest blank_login;
  blank_login.headers["X-WebConfig-Explicit-Login"].cookies="1";
  blank_login.password(""); passwordless.handleLogin(&blank_login);
  assert(blank_login.status==200 && !passwordless._session_token[0]);
  AsyncWebServerRequest blank_view; revealHeaders(blank_view);
  passwordless.handleAdminPasswordReveal(&blank_view);
  assert(blank_view.status==401 && blank_view.reply.empty());
}
'''
        auth.WebConfigRecoveryAuthTest().compile_run(fixture)


if __name__ == '__main__':
    unittest.main()
