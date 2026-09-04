#include <helpers/ReplayResetCommand.h>

#include <cstdlib>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>

using mesh::ReplayResetKind;
using mesh::ReplayResetNonce;
using mesh::ReplayResetRequest;
using Result = mesh::ReplayResetNonce::IssueResult;

static void check(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}

static ReplayResetKind parse(const std::string& command) {
  ReplayResetRequest request;
  const auto result = mesh::parseReplayResetCommand(command.c_str(), request);
  check(result == request.kind, "parser result disagrees with request");
  return result;
}

static void parserTests() {
  const std::string key =
      "0123456789abcdef0123456789ABCDEF0123456789abcdef0123456789ABCDEF";
  const std::string token = "0123456789abcdefFEDCBA9876543210";
  const std::string base = "replay reset " + key;
  check(parse(base) == ReplayResetKind::ExactKey, "exact key");
  check(parse(base + " " + token) == ReplayResetKind::ExactKeyConfirm, "exact confirmation");
  check(parse(" \tQ7| RePlAy\tRESET\t" + key + "\r\n") == ReplayResetKind::ExactKey,
        "normalization and companion prefix");
  check(parse("ab|replay reset " + key + " " + token + " \t\r\n")
        == ReplayResetKind::ExactKeyConfirm, "prefixed confirmation");
  check(parse("replay reset all CONFIRM") == ReplayResetKind::AllConfirm, "all confirmation");
  check(parse(" REPLAY reset ALL confirm\r\n") == ReplayResetKind::AllConfirm, "all case fold");

  ReplayResetRequest request;
  memset(&request, 0xff, sizeof(request));
  check(mesh::parseReplayResetCommand(nullptr, request) == ReplayResetKind::NotReplay,
        "null command");
  for (size_t i = 0; i < sizeof(request.key); ++i) check(request.key[i] == 0, "request key initialized");
  for (size_t i = 0; i < sizeof(request.token); ++i) check(request.token[i] == 0, "request token initialized");
  mesh::parseReplayResetCommand((base + " " + token).c_str(), request);
  check(request.key[0] == 0x01 && request.key[7] == 0xef && request.key[31] == 0xef,
        "decoded exact full key");
  check(request.token[0] == 0x01 && request.token[8] == 0xfe && request.token[15] == 0x10,
        "decoded full nonce");

  const std::string invalid[] = {
    "replay", "replay ", "replay reset", "replay reset all", "replay reset all 123",
    "replay reset all CONFIRM extra", "replay RESET " + key.substr(2),
    "replay reset " + key + "00", "replay reset g" + key.substr(1),
    "replay reset " + key.substr(0, 20) + " " + key.substr(20),
    base + " " + token.substr(2), base + " " + token + "00",
    base + " " + token + " extra", base + " g" + token.substr(1),
    base + " CONFIRM", "replay reset0 " + key, "replay bogus", "replay.reset " + key,
    " ab|replay reset all", "replay reset all\nCONFIRM extra",
  };
  for (const auto& command : invalid) {
    check(parse(command) == ReplayResetKind::Invalid, command.c_str());
  }
  const std::string unrelated[] = {"", " ", "a", "ab", "ab|", "replayx", "replay-reset", "get replay", "reboot"};
  for (const auto& command : unrelated) {
    check(parse(command) == ReplayResetKind::NotReplay, command.c_str());
  }
}

struct Identities {
  uint8_t issuer[32] = {1};
  uint8_t target[32] = {2};
  uint8_t other[32] = {3};
  uint8_t random[16] = {4};
  uint8_t later_random[16] = {5};
  uint8_t zero[16] = {};
};

static void lifecycleTests() {
  Identities ids;
  ReplayResetNonce nonce;
  check(!nonce.matches(ids.issuer, ids.target, ids.random, 100, 2000000000), "unissued nonce");
  check(nonce.issue(ids.issuer, ids.target, ids.random, 100, 2000000000) == Result::Issued,
        "issue nonce");
  check(memcmp(nonce.token(), ids.random, 16) == 0, "exposes original token");
  check(nonce.matches(ids.issuer, ids.target, ids.random, 1100, 2000000001), "matches issuer and target");
  check(!nonce.matches(ids.other, ids.target, ids.random, 1100, 2000000001), "different issuer");
  check(!nonce.matches(ids.issuer, ids.other, ids.random, 1100, 2000000001), "different target");
  check(!nonce.matches(ids.issuer, ids.target, ids.later_random, 1100, 2000000001), "wrong token");
  check(nonce.issue(ids.issuer, ids.target, ids.later_random, 20100, 2000000020) == Result::Reused,
        "prepare retry reuses existing token");
  check(memcmp(nonce.token(), ids.random, 16) == 0, "retry cannot replace token");
  check(nonce.issue(ids.other, ids.target, ids.later_random, 20100, 2000000020) == Result::Busy,
        "other issuer cannot replace challenge");
  check(nonce.issue(ids.issuer, ids.other, ids.later_random, 20100, 2000000020) == Result::Busy,
        "other target cannot replace challenge");
  check(!nonce.consume(ids.issuer, ids.target, ids.later_random, 20100, 2000000020),
        "bad token does not consume challenge");
  check(nonce.consume(ids.issuer, ids.target, ids.random, 20100, 2000000020), "valid token consumed");
  check(!nonce.consume(ids.issuer, ids.target, ids.random, 20100, 2000000020), "cannot consume twice");
  check(!nonce.matches(ids.issuer, ids.target, ids.random, 21100, 2000000021), "captured confirmation rejected");
  for (size_t i = 0; i < 16; ++i) check(nonce.token()[i] == 0, "consumption clears token");
  check(nonce.issue(ids.issuer, ids.target, ids.later_random, 21100, 2000000021) == Result::Issued,
        "reissue after consumption");
  check(!nonce.matches(ids.issuer, ids.target, ids.random, 21100, 2000000021), "old token rejected after reissue");
  nonce.clear();
  check(!nonce.matches(ids.issuer, ids.target, ids.later_random, 21100, 2000000021), "USB invalidation");
  ReplayResetNonce rebooted;
  check(!rebooted.matches(ids.issuer, ids.target, ids.random, 100, 2000000000), "reboot invalidates captured token");
  check(rebooted.issue(ids.issuer, ids.issuer, ids.random, 100, 2000000000) == Result::Issued,
        "self reset allowed by nonce layer");
  check(rebooted.consume(ids.issuer, ids.issuer, ids.random, 100, 2000000000), "self reset consumed once");
}

static void timeTests() {
  Identities ids;
  ReplayResetNonce nonce;
  check(ReplayResetNonce::RESEND_WINDOW_MILLIS == 120000, "resend window is exactly 120 seconds");
  check(ReplayResetNonce::LIFETIME_MILLIS == 300000, "confirmation expires at exactly 300 seconds");
  check(nonce.issue(ids.issuer, ids.target, ids.random, 100, 2000000000) == Result::Issued, "time fixture");
  check(nonce.remainingSeconds(100, 2000000000) == 300, "initial TTL is 300 seconds");
  check(nonce.issue(ids.issuer, ids.target, ids.later_random, 120099, 2000000119) == Result::Reused,
        "119999ms remains in the resend window");
  check(nonce.matches(ids.issuer, ids.target, ids.random, 120099, 2000000119), "119999ms original token valid");
  check(nonce.remainingSeconds(120099, 2000000119) == 180, "last resend advertises original 180-second TTL");
  check(nonce.issue(ids.issuer, ids.target, ids.later_random, 120100, 2000000120)
        == Result::AwaitingConfirmation, "120000ms starts confirmation-only window");
  check(nonce.matches(ids.issuer, ids.target, ids.random, 120100, 2000000120), "120000ms token still valid");
  check(nonce.remainingSeconds(120100, 2000000120) == 180, "confirmation-only boundary retains original TTL");
  check(nonce.issue(ids.issuer, ids.target, ids.later_random, 300099, 2000000299)
        == Result::AwaitingConfirmation, "299999ms cannot reissue or resend original token");
  check(nonce.matches(ids.issuer, ids.target, ids.random, 300099, 2000000299), "299999ms original token valid");
  check(nonce.remainingSeconds(300099, 2000000299) == 0, "TTL rounds down during valid final subsecond");
  check(memcmp(nonce.token(), ids.random, 16) == 0, "confirmation-only retries cannot replace token");
  check(!nonce.matches(ids.issuer, ids.target, ids.random, 300100, 2000000300), "300000ms expiration boundary");
  check(nonce.remainingSeconds(300100, 2000000300) == 0, "expired TTL is zero");
  check(nonce.issue(ids.issuer, ids.target, ids.later_random, 300100, 2000000300) == Result::Issued,
        "expired challenge replaceable");
  check(memcmp(nonce.token(), ids.later_random, 16) == 0, "new lifetime uses fresh token");
  check(nonce.matches(ids.issuer, ids.target, ids.later_random, 301100, 2000000306), "positive clock tolerance");
  check(nonce.matches(ids.issuer, ids.target, ids.later_random, 301100, 2000000296), "negative clock tolerance");
  check(!nonce.matches(ids.issuer, ids.target, ids.later_random, 301100, 2000000307), "forward clock jump");
  check(nonce.remainingSeconds(301100, 2000000307) == 0, "clock-invalid TTL is zero");
  check(!nonce.matches(ids.issuer, ids.target, ids.later_random, 301100, 2000000295), "backward clock jump");
  check(!nonce.matches(ids.issuer, ids.target, ids.later_random, 301100, 0), "zero clock invalid");
  check(!nonce.matches(ids.issuer, ids.target, ids.later_random, 300099, 2000000300), "monotonic backwards fails closed");

  nonce.clear();
  nonce.issue(ids.issuer, ids.target, ids.random, 0, 2000000000);
  for (uint32_t age : {119999U, 120000U, 299999U}) {
    check(nonce.issue(ids.other, ids.target, ids.later_random, age, 2000000000 + age / 1000)
          == Result::Busy, "another issuer stays blocked through confirmation-only window");
    check(nonce.issue(ids.issuer, ids.other, ids.later_random, age, 2000000000 + age / 1000)
          == Result::Busy, "another target stays blocked through confirmation-only window");
  }
  check(nonce.issue(ids.other, ids.target, ids.later_random, 300000, 2000000300)
        == Result::Issued, "another identity can start only after 300000ms");

  nonce.clear();
  const uint32_t started = UINT32_MAX - 500;
  check(nonce.issue(ids.issuer, ids.target, ids.random, started, 2000000000) == Result::Issued, "rollover issue");
  check(nonce.matches(ids.issuer, ids.target, ids.random, 499, 2000000001), "millis rollover valid");
  check(nonce.issue(ids.issuer, ids.target, ids.later_random, started + 119999U, 2000000119)
        == Result::Reused, "rollover 119999ms resend");
  check(nonce.issue(ids.issuer, ids.target, ids.later_random, started + 120000U, 2000000120)
        == Result::AwaitingConfirmation, "rollover 120000ms confirmation-only");
  check(nonce.matches(ids.issuer, ids.target, ids.random, started + 299999U, 2000000299),
        "rollover 299999ms original confirmation valid");
  check(!nonce.matches(ids.issuer, ids.target, ids.random, started + 300000U, 2000000300),
        "rollover 300000ms expiration");

  nonce.clear();
  check(nonce.issue(ids.issuer, ids.target, ids.random, 0, UINT32_MAX) == Result::Issued, "epoch boundary issue");
  check(!nonce.matches(ids.issuer, ids.target, ids.random, 1000, UINT32_MAX), "epoch overflow rejected");
  check(!nonce.matches(ids.issuer, ids.target, ids.random, 1000, 1), "epoch wrap rejected");

  nonce.clear();
  nonce.issue(ids.issuer, ids.target, ids.random, 0, 2000000000);
  check(nonce.issue(ids.issuer, ids.target, ids.later_random, 119999, 2000000119) == Result::Reused,
        "last possible resend still reuses");
  check(nonce.consume(ids.issuer, ids.target, ids.random, 299999, 2000000299),
        "original confirmation works exactly 180 seconds after last possible resend");
  check(nonce.remainingSeconds(299999, 2000000299) == 0, "consumed token TTL is zero");
  nonce.issue(ids.issuer, ids.target, ids.random, 0, 2000000000);
  for (uint32_t age : {119000U, 119999U, 120000U, 200000U, 299999U}) {
    const auto expected = age < 120000 ? Result::Reused : Result::AwaitingConfirmation;
    check(nonce.issue(ids.issuer, ids.target, ids.later_random, age, 2000000000 + age / 1000)
          == expected, "repeated requests obey original window");
  }
  check(!nonce.matches(ids.issuer, ids.target, ids.random, 300000, 2000000300), "retries never extend lifetime");

  nonce.clear();
  nonce.issue(ids.issuer, ids.target, ids.random, 0, 2000000000);
  check(nonce.matches(ids.issuer, ids.target, ids.random, 180000, 2000000185),
        "confirmation-only phase allows positive clock tolerance");
  check(nonce.matches(ids.issuer, ids.target, ids.random, 180000, 2000000175),
        "confirmation-only phase allows negative clock tolerance");
  check(!nonce.matches(ids.issuer, ids.target, ids.random, 180000, 2000000186),
        "forward correction invalidates confirmation-only token");
  check(!nonce.matches(ids.issuer, ids.target, ids.random, 180000, 2000000174),
        "backward correction invalidates confirmation-only token");
}

static void invalidTests() {
  Identities ids;
  ReplayResetNonce nonce;
  check(nonce.issue(nullptr, ids.target, ids.random, 0, 1) == Result::Invalid, "null issuer");
  check(nonce.issue(ids.issuer, nullptr, ids.random, 0, 1) == Result::Invalid, "null target");
  check(nonce.issue(ids.issuer, ids.target, nullptr, 0, 1) == Result::Invalid, "null random");
  check(nonce.issue(ids.issuer, ids.target, ids.zero, 0, 1) == Result::Invalid, "zero random");
  check(nonce.issue(ids.issuer, ids.target, ids.random, 0, 0) == Result::Invalid, "zero epoch");
  check(nonce.issue(ids.issuer, ids.target, ids.random, 0, 2000000000) == Result::Issued, "valid after invalid attempts");
  check(!nonce.matches(nullptr, ids.target, ids.random, 0, 2000000000), "null matching issuer");
  check(!nonce.matches(ids.issuer, nullptr, ids.random, 0, 2000000000), "null matching target");
  check(!nonce.matches(ids.issuer, ids.target, nullptr, 0, 2000000000), "null matching token");
  check(nonce.issue(ids.issuer, ids.target, nullptr, 0, 2000000000) == Result::Reused,
        "existing challenge does not require replacement randomness");
  check(nonce.issue(ids.issuer, ids.target, nullptr, 120000, 2000000120)
        == Result::AwaitingConfirmation, "confirmation-only phase needs no replacement randomness");
  for (size_t i = 0; i < 32; ++i) {
    uint8_t changed[32];
    memcpy(changed, ids.issuer, 32);
    changed[i] ^= 1;
    check(!nonce.matches(changed, ids.target, ids.random, 0, 2000000000), "every issuer byte bound");
    memcpy(changed, ids.target, 32);
    changed[i] ^= 1;
    check(!nonce.matches(ids.issuer, changed, ids.random, 0, 2000000000), "every target byte bound");
  }
  for (size_t i = 0; i < 16; ++i) {
    uint8_t changed[16];
    memcpy(changed, ids.random, 16);
    changed[i] ^= 1;
    check(!nonce.matches(ids.issuer, ids.target, changed, 0, 2000000000), "every token byte bound");
  }
}

int main(int argc, char** argv) {
  try {
    if (argc != 2) throw std::runtime_error("expected test case");
    const std::string test = argv[1];
    if (test == "parser") parserTests();
    else if (test == "lifecycle") lifecycleTests();
    else if (test == "time") timeTests();
    else if (test == "invalid") invalidTests();
    else throw std::runtime_error("unknown test case");
  } catch (const std::exception& error) {
    std::cerr << error.what() << std::endl;
    return EXIT_FAILURE;
  }
  return EXIT_SUCCESS;
}
