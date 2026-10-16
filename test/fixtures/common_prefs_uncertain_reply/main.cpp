
// Included after the extracted production CommonCLI methods and serializer.
#include <cstdio>

static const char* oldSecret(bool guest) { return guest ? "old-guest" : "old-admin"; }
static const char* newSecret(bool guest) { return guest ? "new-guest" : "new-admin"; }
static const char* retrySecret(bool guest) { return guest ? "retry-guest" : "retry-admin"; }
static const char* currentSecret(const CommonCLI& cli, bool guest) {
  return guest ? cli.prefs.guest_password : cli.prefs.password;
}

static void seed(CommonCLI& cli) {
  strcpy(cli.prefs.password, oldSecret(false));
  strcpy(cli.prefs.guest_password, oldSecret(true));
  cli.savePrefs(&cli.fs, PrefsSaveRouting::Scope::Common);
  assert(cli._common_save_result_known && cli._common_save_succeeded);
}

static void change(CommonCLI& cli, bool guest, const char* secret, char* reply) {
  char command[80] = {};
  snprintf(command, sizeof(command), "%s%s", guest ? "guest.password " : "password ", secret);
  memset(reply, 0, 160);
  if (guest) cli.guestPassword(command, reply);
  else cli.password(command, reply);
}

static void assertError(const char* reply, bool guest, const char* attempted) {
  assert(strncmp(reply, "Error:", 6) == 0);
  assert(strstr(reply, "unconfirmed") != nullptr);
  assert(strstr(reply, "reboot") != nullptr);
  assert(strstr(reply, "unchanged") == nullptr);
  assert(strstr(reply, "OK") == nullptr);
  // Neither an old credential nor any attempted credential belongs in an
  // error, even though the successful admin command intentionally echoes it.
  for (const char* secret : {oldSecret(false), oldSecret(true),
                            newSecret(false), newSecret(true), attempted}) {
    assert(strstr(reply, secret) == nullptr);
  }
  (void)guest;
}

static void assertSuccess(const CommonCLI& cli, bool guest,
                          const char* secret, const char* reply) {
  assert(cli._common_save_result_known && cli._common_save_succeeded);
  assert(strcmp(currentSecret(cli, guest), secret) == 0);
  if (guest) assert(strcmp(reply, "OK") == 0);
  else {
    char expected[80];
    snprintf(expected, sizeof(expected), "password now: %s", secret);
    assert(strcmp(reply, expected) == 0);
  }
}

static void assertReboot(const CommonCLI& cli, bool guest, const char* expected) {
  CommonCLI reboot;
  reboot.fs.files = cli.fs.files;
  reboot.loadPrefsInt(&reboot.fs, "/com_prefs");
  assert(strcmp(currentSecret(reboot, guest), expected) == 0);
  assert(strcmp(currentSecret(reboot, !guest), oldSecret(!guest)) == 0);
}

static void clearFaults(CommonCLI& cli) {
  cli.fs.fail_write = false;
  cli.fs.fail_write_after = -1;
  cli.fs.fail_read_open = false;
  cli.fs.fail_after_publication = false;
  cli.fs.fail_rename = 0;
}

static void assertLegacyError(const CommonCLI& cli, const char* reply) {
  assert(!cli._common_save_succeeded && cli._prefs_save_failures == 1);
  assert(cli.prefs.dirty);
  assert(strncmp(reply, "Error:", 6) == 0);
  assert(strstr(reply, "unconfirmed") != nullptr);
  assert(strstr(reply, "active") != nullptr);
  assert(strstr(reply, "reboot") != nullptr);
  assert(strstr(reply, "unchanged") == nullptr && strstr(reply, "OK") == nullptr);
  assert(strstr(reply, "unsaved") == nullptr);
}

static void assertLegacyReboot(const CommonCLI& cli, uint8_t expected) {
  CommonCLI reboot;
  reboot.fs.files = cli.fs.files;
  reboot.loadPrefsInt(&reboot.fs, "/com_prefs");
  assert(reboot.prefs.multi_acks == expected);
  assert(strcmp(reboot.prefs.password, oldSecret(false)) == 0);
  assert(strcmp(reboot.prefs.guest_password, oldSecret(true)) == 0);
}

int main() {
  unsigned scenarios = 0;
  for (bool guest : {false, true}) {
    char reply[160] = {};
    CommonCLI healthy;
    seed(healthy);
    const auto previous = healthy.fs.files.at("/com_prefs");
    change(healthy, guest, newSecret(guest), reply);
    assertSuccess(healthy, guest, newSecret(guest), reply);
    assertReboot(healthy, guest, newSecret(guest));
    const auto candidate = healthy.fs.files.at("/com_prefs");
    assert(candidate != previous && candidate.size() == previous.size());
    ++scenarios;

    // Each ordinary rejection leaves the old image on disk. The bool API
    // cannot promise that for every false result, so its reply stays cautious.
    for (unsigned fault = 0; fault != 4; ++fault) {
      CommonCLI cli;
      seed(cli);
      if (fault == 0) cli.fs.fail_write = true;
      if (fault == 1) cli.fs.fail_write_after = 64;
      if (fault == 2) cli.fs.fail_read_open = true;
      if (fault == 3) cli.fs.fail_rename = 1;
      change(cli, guest, newSecret(guest), reply);
      assert(!cli._common_save_succeeded);
      assertError(reply, guest, newSecret(guest));
      assert(strcmp(currentSecret(cli, guest), oldSecret(guest)) == 0);
      assert(cli.fs.files.at("/com_prefs") == previous);
      clearFaults(cli);
      assertReboot(cli, guest, oldSecret(guest));
      change(cli, guest, retrySecret(guest), reply);
      assertSuccess(cli, guest, retrySecret(guest), reply);
      assertReboot(cli, guest, retrySecret(guest));
      ++scenarios;
    }

#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
    // Atomic publication succeeded, then every public-image readback failed.
    // The real writer returns false/Indeterminate, runtime credentials roll
    // back, but fresh boot must read the new durable credential. An "unchanged"
    // promise here is both false and dangerous for remote administration.
    {
      CommonCLI cli;
      seed(cli);
      cli.fs.fail_after_publication = true;
      change(cli, guest, newSecret(guest), reply);
      assert(!cli._common_save_succeeded);
      assertError(reply, guest, newSecret(guest));
      assert(strcmp(currentSecret(cli, guest), oldSecret(guest)) == 0);
      assert(cli.fs.files.at("/com_prefs") == candidate);
      clearFaults(cli);
      assertReboot(cli, guest, newSecret(guest));
      change(cli, guest, retrySecret(guest), reply);
      assertSuccess(cli, guest, retrySecret(guest), reply);
      assertReboot(cli, guest, retrySecret(guest));
      ++scenarios;
    }
#endif
  }

  // Legacy setters retain their live changes after save failure. Exercise the
  // actual outer reply guard so the branch's final OK cannot hide either
  // known rejection or post-publication uncertainty.
  {
    char reply[160] = {};
    CommonCLI healthy;
    seed(healthy);
    assert(healthy.prefs.multi_acks == 0);
    const auto previous = healthy.fs.files.at("/com_prefs");
    healthy.legacySet("multi.acks 1", reply);
    assert(strcmp(reply, "OK") == 0 && healthy.prefs.multi_acks == 1);
    assert(healthy._common_save_succeeded && healthy._prefs_save_failures == 0);
    assert(!healthy.prefs.dirty);
    assertLegacyReboot(healthy, 1);
    const auto candidate = healthy.fs.files.at("/com_prefs");
    assert(candidate != previous && candidate.size() == previous.size());
    ++scenarios;

    for (unsigned fault = 0; fault != 4; ++fault) {
      CommonCLI cli;
      seed(cli);
      if (fault == 0) cli.fs.fail_write = true;
      if (fault == 1) cli.fs.fail_write_after = 64;
      if (fault == 2) cli.fs.fail_read_open = true;
      if (fault == 3) cli.fs.fail_rename = 1;
      cli.legacySet("multi.acks 1", reply);
      assertLegacyError(cli, reply);
      assert(cli.prefs.multi_acks == 1);
      assert(cli.fs.files.at("/com_prefs") == previous);
      clearFaults(cli);
      assertLegacyReboot(cli, 0);
      // Retry the same live value: an unchanged assignment must still save.
      cli.legacySet("multi.acks 1", reply);
      assert(strcmp(reply, "OK") == 0 && cli._common_save_succeeded);
      assert(cli._prefs_save_failures == 1 && !cli.prefs.dirty);
      assertLegacyReboot(cli, 1);
      ++scenarios;
    }

#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
    {
      CommonCLI cli;
      seed(cli);
      cli.fs.fail_after_publication = true;
      cli.legacySet("multi.acks 1", reply);
      assertLegacyError(cli, reply);
      assert(cli.prefs.multi_acks == 1);
      assert(cli.fs.files.at("/com_prefs") == candidate);
      clearFaults(cli);
      assertLegacyReboot(cli, 1);
      cli.legacySet("multi.acks 0", reply);
      assert(strcmp(reply, "OK") == 0 && cli._common_save_succeeded);
      assert(cli._prefs_save_failures == 1 && !cli.prefs.dirty);
      assertLegacyReboot(cli, 0);
      ++scenarios;
    }
#endif
  }
  printf("%u actual CommonCLI save scenarios passed\n", scenarios);
}
