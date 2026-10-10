#!/usr/bin/env python3
"""Execute the production room-login helper with the actual ACL role constants."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

PRELUDE = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <helpers/RoomLoginAuthorization.h>
@ROLES@

static mesh::RoomLoginAuthorization authorize(
    const char* password, const char* admin, const char* guest,
    bool known, uint8_t permissions, bool read_only) {
  return mesh::authorizeRoomLogin(password, admin, guest, known, permissions,
      read_only, PERM_ACL_ROLE_MASK, PERM_ACL_GUEST, PERM_ACL_READ_WRITE,
      PERM_ACL_ADMIN);
}
'''

AUTH_MATRIX = r'''
struct Case {
  const char* password;
  const char* admin;
  const char* guest;
  bool read_only;
  bool accepted;
  uint8_t role;
};
int main() {
  // Explicit expectations keep disabled credentials distinct from a valid
  // blank-password ACL login. The same rows also detect partial/normalized
  // comparisons and public-password elevation to Admin.
  const Case cases[] = {
    {"admin", "admin", "guest", false, true, PERM_ACL_ADMIN},
    {"admin", "admin", "guest", true, true, PERM_ACL_ADMIN},
    {"guest", "admin", "guest", false, true, PERM_ACL_READ_WRITE},
    {"guest", "admin", "guest", true, true, PERM_ACL_READ_WRITE},
    {"wrong", "admin", "guest", false, false, PERM_ACL_GUEST},
    {"wrong", "admin", "guest", true, true, PERM_ACL_GUEST},
    {"", "admin", "guest", false, false, PERM_ACL_GUEST},
    {"", "admin", "guest", true, true, PERM_ACL_GUEST},
    {nullptr, "admin", "guest", false, false, PERM_ACL_GUEST},
    {nullptr, "admin", "guest", true, true, PERM_ACL_GUEST},
    {"", "", "", false, false, PERM_ACL_GUEST},
    {"", "", "", true, true, PERM_ACL_GUEST},
    {nullptr, "", "", false, false, PERM_ACL_GUEST},
    {nullptr, "", "", true, true, PERM_ACL_GUEST},
    {"admin", "", "guest", false, false, PERM_ACL_GUEST},
    {"admin", "", "guest", true, true, PERM_ACL_GUEST},
    {"guest", "", "guest", false, true, PERM_ACL_READ_WRITE},
    {"guest", nullptr, "guest", false, true, PERM_ACL_READ_WRITE},
    {"admin", "admin", "", false, true, PERM_ACL_ADMIN},
    {"admin", "admin", nullptr, false, true, PERM_ACL_ADMIN},
    {"guest", "admin", "", false, false, PERM_ACL_GUEST},
    {"guest", "admin", nullptr, true, true, PERM_ACL_GUEST},
    {"anything", nullptr, nullptr, false, false, PERM_ACL_GUEST},
    {"anything", nullptr, nullptr, true, true, PERM_ACL_GUEST},
    {nullptr, nullptr, nullptr, false, false, PERM_ACL_GUEST},
    {nullptr, nullptr, nullptr, true, true, PERM_ACL_GUEST},
    {"same", "same", "same", false, true, PERM_ACL_ADMIN},
    {"same", "same", "same", true, true, PERM_ACL_ADMIN},
    {"admi", "admin", "guest", false, false, PERM_ACL_GUEST},
    {"admin-more", "admin", "guest", false, false, PERM_ACL_GUEST},
    {"ADMIN", "admin", "guest", false, false, PERM_ACL_GUEST},
    {"admin ", "admin", "guest", false, false, PERM_ACL_GUEST},
    {" admin", "admin", "guest", false, false, PERM_ACL_GUEST},
    {"gues", "admin", "guest", false, false, PERM_ACL_GUEST},
    {"guest-more", "admin", "guest", false, false, PERM_ACL_GUEST},
    {"GUEST", "admin", "guest", false, false, PERM_ACL_GUEST},
    {"guest ", "admin", "guest", false, false, PERM_ACL_GUEST},
    {" guest", "admin", "guest", true, true, PERM_ACL_GUEST},
    {" ", " ", "guest", false, true, PERM_ACL_ADMIN},
    {" ", "admin", " ", false, true, PERM_ACL_READ_WRITE},
  };
  for (const auto& c : cases) {
    // Even an irrelevant nonzero permissions argument cannot invent an ACL
    // identity. This checks callers use identity presence independently.
    for (const uint8_t stale : {uint8_t(0), uint8_t(3), uint8_t(255)}) {
      const auto actual = authorize(c.password, c.admin, c.guest,
                                   false, stale, c.read_only);
      assert(actual.accepted == c.accepted && actual.role == c.role);
    }
  }
  puts("120 explicit unknown-identity credential scenarios passed");
}
'''

NON_DEMOTION = r'''
int main() {
  const char* passwords[] = {nullptr, "", "wrong", "guest", "old-admin"};
  unsigned preserved = 0;
  for (unsigned permissions = 0; permissions < 256; ++permissions) {
    for (const auto* password : passwords) {
      for (bool read_only : {false, true}) {
        const auto actual = authorize(password, "admin", "guest", true,
                                      permissions, read_only);
        assert(actual.accepted);
        assert(actual.role == (permissions & PERM_ACL_ROLE_MASK));
        ++preserved;
      }
    }
    // Admin has explicit credential precedence even for roles 4/5/6/7. A
    // numeric max() or >= comparison would retain the wrong delegated role.
    const auto admin = authorize("admin", "admin", "guest", true,
                                 permissions, false);
    assert(admin.accepted && admin.role == PERM_ACL_ADMIN);
  }
  assert(preserved == 2560);
  puts("2560 exact ACL-role preservation and 256 admin-upgrade scenarios passed");
}
'''

NULL_EMPTY = r'''
int main() {
  const char* absent[] = {nullptr, ""};
  const char* requests[] = {nullptr, "", "not-a-credential"};
  unsigned disabled = 0;
  for (const char* admin : absent) {
    for (const char* guest : absent) {
      for (const char* password : requests) {
        assert(!mesh::roomLoginPasswordMatches(password, admin));
        assert(!mesh::roomLoginPasswordMatches(password, guest));
        const auto rejected = authorize(password, admin, guest, false, 255, false);
        assert(!rejected.accepted && rejected.role == PERM_ACL_GUEST);
        const auto public_reader = authorize(password, admin, guest, false, 255, true);
        assert(public_reader.accepted && public_reader.role == PERM_ACL_GUEST);
        for (uint8_t role = 0; role <= PERM_ACL_ROLE_MASK; ++role) {
          const auto known = authorize(password, admin, guest, true, role, false);
          assert(known.accepted && known.role == role);
        }
        ++disabled;
      }
    }
  }
  assert(disabled == 12);
  char request[] = "admin";
  char admin[] = "admin";
  char guest[] = "guest";
  const auto authenticated = authorize(request, admin, guest, false, 0, false);
  assert(authenticated.accepted && authenticated.role == PERM_ACL_ADMIN);
  assert(!strcmp(request, "admin") && !strcmp(admin, "admin") && !strcmp(guest, "guest"));
  puts("null/empty disabled credentials and read-only string access passed");
}
'''


class RoomLoginAuthorizationTests(unittest.TestCase):
    def compile_and_run(self, checks, expected):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        # Keep role labels synchronized with production without pulling the
        # Arduino/Mesh dependencies into this standalone authorization helper.
        roles = "\n".join(line for line in (ROOT / "src/helpers/ClientACL.h").read_text().splitlines()
                          if line.startswith("#define PERM_ACL_"))
        source = PRELUDE.replace("@ROLES@", roles) + checks
        with tempfile.TemporaryDirectory(prefix="room-login-authorization-") as directory:
            work = Path(directory)
            cpp = work / "test.cpp"
            cpp.write_text(source)
            binary = work / "room-login"
            command = [compiler, "-std=c++11", "-O1", "-Wall", "-Wextra", "-Werror",
                       "-include", "initializer_list", "-I" + str(ROOT / "src"),
                       str(cpp), "-o", str(binary)]
            if sys.platform.startswith("linux"):
                command[1:1] = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
            built = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertIn(expected, checked.stdout)

    def test_unknown_identity_password_authorization_matrix(self):
        self.compile_and_run(AUTH_MATRIX, "120 explicit unknown-identity credential scenarios passed")

    def test_every_acl_role_is_preserved_without_numeric_privilege_ranking(self):
        self.compile_and_run(NON_DEMOTION, "2560 exact ACL-role preservation and 256 admin-upgrade scenarios passed")

    def test_null_empty_credentials_and_const_input_safety(self):
        self.compile_and_run(NULL_EMPTY, "null/empty disabled credentials and read-only string access passed")


if __name__ == "__main__":
    unittest.main()
