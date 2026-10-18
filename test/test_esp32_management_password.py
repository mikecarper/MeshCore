"""Execute the shared credential mirror, Basic parser, and upload/change leases."""
import base64
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ManagementPasswordTest(unittest.TestCase):
    def compile_run(self, source, extra_source=None):
        with tempfile.TemporaryDirectory(prefix="management-password-") as temporary:
            directory = Path(temporary)
            files = []
            for name, text in (("test.cpp", source), ("other.cpp", extra_source)):
                if text is not None:
                    path = directory / name
                    path.write_text(text)
                    files.append(str(path))
            result = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++11", "-Wall", "-Wextra", "-Werror",
                "-pthread", "-DMESH_MANAGEMENT_PASSWORD_HOST_TEST=1", "-I", str(ROOT / "src"),
                *files, "-o", str(directory / "test")], capture_output=True, text=True, timeout=45)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(directory / "test")], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_parser_exact_boundaries_and_unusual_credentials(self):
        cases = []
        for password in ("a", "1234567", "12345678", "123456789012345", "a:b\\;\" ", "ééééééé"):
            encoded = base64.b64encode(("admin:" + password).encode()).decode()
            literal = '"' + ''.join('\\%03o' % byte for byte in password.encode()) + '"'
            cases.append(f'assert(basicAuthorizationMatches("Basic {encoded}", {literal}));')
            cases.append(f'assert(basicAuthorizationMatches("bAsIc {encoded}", {literal}));')
            cases.append(f'assert(!basicAuthorizationMatches("Basic {encoded}x", {literal}));')
            wrong = base64.b64encode(("other:" + password).encode()).decode()
            cases.append(f'assert(!basicAuthorizationMatches("Basic {wrong}", {literal}));')
        self.compile_run(r'''
#include <helpers/esp32/ManagementPassword.h>
#include <cassert>
using namespace mesh::esp32::managementPassword;
int main() {
  assert(!validPassword(nullptr) && !validPassword("") && !validPassword("1234567890123456"));
  assert(!validPassword("abc\rdef") && !validPassword("abc\ndef"));
  assert(validPassword("123456789012345") && validPassword("\303\251"));
  assert(!apPasswordEligible("1234567") && apPasswordEligible("12345678"));
  assert(apPasswordEligible("123456789012345") && !apPasswordEligible("1234567890123456"));
  assert(!apPasswordEligible("1234567\177") && !apPasswordEligible("1234567\303\251"));
  assert(!basicAuthorizationMatches(nullptr, "password"));
  assert(!basicAuthorizationMatches("Basic YWRtaW46", ""));
  @CASES@
}
'''.replace("@CASES@", "\n".join(cases)))

    def test_empty_legacy_mirror_then_committed_rotation_and_upload_reservations(self):
        self.compile_run(r'''
#include <helpers/esp32/ManagementPassword.h>
#include <cassert>
using namespace mesh::esp32::managementPassword;
int main() {
  Snapshot before, after;
  assert(!snapshot(before));
  assert(!publish("") && initialize("") && snapshot(before) && before.generation == 1);
  assert(!available() && authenticateBasic("Basic YWRtaW46", 100) == AuthResult::Unavailable);
  assert(!initialize("different") && !publish("bad\npassword"));
  assert(publish("password") && snapshot(before) && before.generation == 2);
  assert(available() && publish("password") && snapshot(after) && after.generation == before.generation);
  {
    ChangeGuard failed_save;
    assert(failed_save.acquired());
    assert(!beginUpload(before.generation));
    assert(!publish("unsaved"));
    ChangeGuard competing; assert(!competing.acquired());
    // Simulate the caller's persistent save failing: do not publish.
  }
  assert(snapshot(after) && !strcmp(after.password, "password") && after.generation == before.generation);
  {
    UploadGuard upload(before.generation);
    assert(upload.acquired() && uploadActive());
    ChangeGuard change; assert(!change.acquired());
    assert(!publish("different"));
    UploadGuard competitor(before.generation); assert(!competitor.acquired());
    assert(authenticateBasic("Basic YWRtaW46cGFzc3dvcmQ=", 100) == AuthResult::Authorized);
  }
  assert(!uploadActive());
  {
    ChangeGuard saved; assert(saved.acquired());
    assert(!saved.publish("") && saved.publish("different"));
    assert(!beginUpload(before.generation));
  }
  assert(snapshot(after) && after.generation == before.generation + 1);
  assert(!beginUpload(before.generation));
  assert(authenticateBasic("Basic YWRtaW46cGFzc3dvcmQ=", 101) == AuthResult::Unauthorized);
  uint32_t generation = 0;
  assert(authenticateBasic("Basic YWRtaW46ZGlmZmVyZW50", 102, &generation) == AuthResult::Authorized);
  assert(generation == after.generation);
}
''')

    def test_bounded_login_budget_and_prompt(self):
        self.compile_run(r'''
#include <helpers/esp32/ManagementPassword.h>
#include <cassert>
using namespace mesh::esp32::managementPassword;
int main() {
  assert(publish("password"));
  for (int i = 0; i != 20; ++i) assert(authenticateBasic(nullptr, 100) == AuthResult::Unauthorized);
  for (int i = 0; i != 5; ++i) assert(authenticateBasic("Basic wrong", 100) == AuthResult::Unauthorized);
  assert(authenticateBasic("Basic YWRtaW46cGFzc3dvcmQ=", 101) == AuthResult::Throttled);
  assert(authenticateBasic("Basic YWRtaW46cGFzc3dvcmQ=", 30100) == AuthResult::Authorized);
}
''')

    def test_deferred_ap_rotation_is_owner_bound_and_excludes_uploads(self):
        self.compile_run(r'''
#include <helpers/esp32/ManagementPassword.h>
#include <cassert>
using namespace mesh::esp32::managementPassword;
int calls = 0;
bool rotate(void* owner) {
  assert(owner == &calls); ++calls;
  Snapshot current; assert(snapshot(current) && !strcmp(current.password, "new-password"));
  ChangeGuard change; assert(!change.acquired());
  UploadGuard upload(current.generation); assert(!upload.acquired());
  return true;
}
int main() {
  assert(publish("password"));
  Snapshot current; snapshot(current);
  watchOtaAccessPoint(&calls, rotate, current.generation);
  assert(publish("new-password"));
  service(0xfffffff0U); service(733); assert(calls == 0);
  service(734); assert(calls == 1); service(5000); assert(calls == 1);
  snapshot(current); watchOtaAccessPoint(&calls, rotate, current.generation);
  assert(publish("third-value"));
  forgetOtaAccessPoint(reinterpret_cast<void*>(1));
  forgetOtaAccessPoint(&calls); service(6000); service(7000); assert(calls == 1);
}
''')

    def test_one_registry_across_translation_units_and_threaded_snapshots(self):
        other = r'''
#include <helpers/esp32/ManagementPassword.h>
bool other_publish() { return mesh::esp32::managementPassword::publish("from-other"); }
'''
        self.compile_run(r'''
#include <helpers/esp32/ManagementPassword.h>
#include <cassert>
#include <atomic>
#include <thread>
using namespace mesh::esp32::managementPassword;
bool other_publish();
int main() {
  assert(other_publish()); Snapshot current; assert(snapshot(current) && !strcmp(current.password, "from-other"));
  std::atomic<bool> done(false);
  std::thread writer([&] {
    for (int i = 0; i < 10000; ++i) assert(publish(i & 1 ? "AAAAAAAAAAAAAAA" : "BBBBBBBBBBBBBBB"));
    done.store(true);
  });
  while (!done.load()) {
    assert(snapshot(current));
    assert(!strcmp(current.password, "from-other") || !strcmp(current.password, "AAAAAAAAAAAAAAA")
           || !strcmp(current.password, "BBBBBBBBBBBBBBB"));
  }
  writer.join();
}
''', other)


if __name__ == "__main__":
    unittest.main()
