#include <helpers/PowerSavingDiagnostics.h>
#include <assert.h>
#include <limits.h>
#include <initializer_list>

using namespace mesh::powerdiag;
extern void powerDiagnosticsOtherTranslationUnit();

static bool command(const char* text, char* out, uint32_t now = 0) {
  return handleCommand(text, out, 157, now);
}

int main(int argc, char** argv) {
  assert(argc == 2);
  state() = Snapshot();
  const char* test = argv[1];
  if (!strcmp(test, "disabled")) {
    assert(!enabled());
    decision(mask(Reason::UsbSession), 10);
    sleepBegin(10); sleepEnd(20);
    boardVeto(Reason::Board, 20); boardError(-7, 30);
    const Snapshot value = snapshot(1000);
    assert(!value.observed && !value.current && !value.calls && !value.errors);
    assert(!value.board_vetoes && !value.eligible_ms && !value.union_blocked_ms);
  } else if (!strcmp(test, "overlap")) {
    setEnabled(true, 10);
    decision(mask(Reason::UsbSession) | mask(Reason::Bridge), 10);
    decision(mask(Reason::Bridge), 40);
    decision(mask(Reason::Bridge), 50);
    decision(0, 60);
    const Snapshot value = snapshot(80);
    assert(value.union_blocked_ms == 50 && value.eligible_ms == 20);
    assert(value.blocked_ms[(int)Reason::UsbSession] == 30);
    assert(value.blocked_ms[(int)Reason::Bridge] == 50);
    assert(value.transitions[(int)Reason::UsbSession] == 1);
    assert(value.transitions[(int)Reason::Bridge] == 1);
    assert(value.seen == (mask(Reason::UsbSession) | mask(Reason::Bridge)));
    assert(state().eligible_ms == 0); // Formatting/snapshot does not mutate state.
    assert(snapshot(80).eligible_ms == 20);
  } else if (!strcmp(test, "freeze")) {
    setEnabled(true, 0);
    decision(mask(Reason::UsbSession), 0);
    decision(mask(Reason::Calibration), 50);
    setEnabled(false, 100);
    decision(mask(Reason::Bridge), 200);
    const Snapshot value = snapshot(1000);
    assert(value.union_blocked_ms == 100 && value.eligible_ms == 0);
    assert(value.blocked_ms[(int)Reason::UsbSession] == 50);
    assert(value.blocked_ms[(int)Reason::Calibration] == 50);
    assert(value.current == mask(Reason::Calibration));
    setEnabled(true, 1100);
    assert(!state().observed && !state().current && !state().seen);
    assert(!state().union_blocked_ms && !state().eligible_ms);
  } else if (!strcmp(test, "reset")) {
    setEnabled(true, 0); decision(mask(Reason::Serial), 1);
    sleepBegin(1); sleepEnd(20); reset(10);
    assert(enabled() && !state().observed && !state().calls && !state().seen);
    decision(0, 11); assert(snapshot(20).eligible_ms == 9);
    setEnabled(false, 20); reset(30);
    assert(!enabled() && !state().observed && !state().eligible_ms);
  } else if (!strcmp(test, "wrap")) {
    setEnabled(true, UINT32_MAX - 9);
    decision(mask(Reason::Queue), UINT32_MAX - 9);
    decision(0, 10);
    assert(state().union_blocked_ms == 20);
    sleepBegin(UINT32_MAX - 49); sleepEnd(50);
    assert(state().last_call_us == 100 && !state().short_returns);
    sleepBegin(50); sleepEnd(149);
    assert(state().short_returns == 1 && state().last_call_us == 99);
    assert(state().calls == 2 && state().returns == 2 && state().call_us == 199);
  } else if (!strcmp(test, "veto_error")) {
    setEnabled(true, 0); decision(0, 0);
    sleepBegin(1); boardVeto(Reason::BridgeCleanup, 1, 7); sleepEnd(1001);
    assert(state().calls == 1 && state().returns == 0 && state().board_vetoes == 1);
    assert(state().current == mask(Reason::BridgeCleanup) && state().detail == 7);
    decision(0, 2); sleepBegin(2000); boardError(-17, 3); sleepEnd(3000);
    assert(state().errors == 1 && state().detail == -17 && state().returns == 0);
    assert(state().current == mask(Reason::Board));
    decision(0, 4); sleepBegin(4000); sleepEnd(4000); sleepEnd(5000);
    assert(state().returns == 1 && state().short_returns == 1);
    assert(state().call_us == 0 && state().board_vetoes == 1);
    decision(0, 5); sleepBegin(5000); sleepEnd(5010); boardError(-9, 6);
    // A reported API return is not a claim that its result was successful.
    assert(state().returns == 2 && state().errors == 2 && state().call_us == 10);
    assert(state().detail == -9 && state().current == mask(Reason::Board));
  } else if (!strcmp(test, "saturation")) {
    setEnabled(true, 0); decision(mask(Reason::Board), 0);
    state().union_blocked_ms = UINT32_MAX - 5;
    state().blocked_ms[(int)Reason::Board] = UINT32_MAX - 5;
    state().transitions[(int)Reason::Board] = UINT16_MAX;
    decision(0, 10); decision(mask(Reason::Board), 11);
    assert(state().union_blocked_ms == UINT32_MAX);
    assert(state().blocked_ms[(int)Reason::Board] == UINT32_MAX);
    assert(state().transitions[(int)Reason::Board] == UINT16_MAX);
    state().calls = state().returns = state().short_returns = UINT32_MAX;
    state().call_us = UINT32_MAX - 5;
    sleepBegin(20); sleepEnd(30);
    assert(state().calls == UINT32_MAX && state().returns == UINT32_MAX);
    assert(state().short_returns == UINT32_MAX && state().call_us == UINT32_MAX);
  } else if (!strcmp(test, "commands")) {
    char out[157];
    assert(command("set powersaving.diag on", out, 1) && enabled());
    assert(strstr(out, "RAM only") && strstr(out, "unchanged"));
    decision(mask(Reason::Calibration), 2);
    assert(command("set powersaving.diag on", out, 3));
    assert(state().seen == mask(Reason::Calibration)); // Repeated on is not a clear.
    assert(command("get powersaving.diag calibration", out, 5));
    assert(strstr(out, "active=1") && strstr(out, "blocked_ms=3"));
    assert(command("get powersaving.diag\t calls \t", out, 5));
    assert(strstr(out, "calls=0"));
    assert(command("set powersaving.diag off \t", out, 5) && !enabled());
    assert(command("clear powersaving.diag", out, 6) && !enabled());
    assert(!state().observed && !state().seen);
  } else if (!strcmp(test, "strict")) {
    char out[157] = "untouched";
    assert(!command("get powersaving.diagfoo", out));
    assert(!command("set powersaving.diagon", out));
    assert(!command("get powersaving", out));
    assert(!strcmp(out, "untouched"));
    for (const char* text : {"set powersaving.diag", "set powersaving.diag on junk",
         "set powersaving.diag 1", "clear powersaving.diag all",
         "get powersaving.diag 9", "get powersaving.diag 01",
         "get powersaving.diag 1junk", "get powersaving.diag state junk"}) {
      assert(command(text, out)); assert(strstr(out, "Error:"));
      assert(!enabled() && !state().observed && !state().calls);
    }
  } else if (!strcmp(test, "format")) {
    char out[157];
    assert(command("get powersaving.diag", out, 100));
    assert(strstr(out, "age_ms=unknown") && strstr(out, "why=unobserved"));
    assert(command("get powersaving.diag usb.session", out, 100));
    assert(strstr(out, "age_ms=unknown"));
    setEnabled(true, 100); decision(mask(Reason::UsbSession), 100);
    assert(command("get powersaving.diag state", out, 10000));
    assert(strstr(out, "age_ms=9900") && strstr(out, "why=usb.session"));
    assert(state().decision_at_ms == 100 && state().last_ms == 100);
    assert(command("get powersaving.diag time", out, 10000));
    assert(strstr(out, "NOT sleep residency"));
    assert(command("get powersaving.diag 3", out, 10000));
    assert(strstr(out, "usb.session=1/9900"));
  } else if (!strcmp(test, "capacity")) {
    setEnabled(true, 0); decision(ValidMask, 0);
    state().union_blocked_ms = state().eligible_ms = UINT32_MAX;
    state().calls = state().returns = state().short_returns = UINT32_MAX;
    state().board_vetoes = state().errors = UINT32_MAX;
    state().call_us = state().max_call_us = state().last_call_us = UINT32_MAX;
    state().detail = INT32_MIN;
    for (size_t i = 0; i < ReasonCount; ++i) {
      state().blocked_ms[i] = UINT32_MAX; state().transitions[i] = UINT16_MAX;
    }
    for (unsigned page = 0; page <= 8; ++page) {
      char large[512], bounded[157];
      formatPage(page, large, sizeof(large), UINT32_MAX);
      formatPage(page, bounded, sizeof(bounded), UINT32_MAX);
      assert(strlen(large) < sizeof(bounded)); assert(!strcmp(large, bounded));
    }
    for (size_t capacity = 1; capacity < 160; ++capacity) {
      char guarded[162]; memset(guarded, '!', sizeof(guarded));
      assert(handleCommand("get powersaving.diag time", guarded + 1, capacity, 0));
      assert(guarded[0] == '!' && guarded[capacity + 1] == '!');
      assert(memchr(guarded + 1, 0, capacity));
    }
    char untouched = '!';
    assert(!handleCommand("get powersaving.diag", &untouched, 0, 0));
    assert(untouched == '!');
    assert(!handleCommand(nullptr, &untouched, 1, 0));
    assert(!handleCommand("get powersaving.diag", nullptr, 1, 0));
  } else if (!strcmp(test, "shared")) {
    setEnabled(true, 0); powerDiagnosticsOtherTranslationUnit();
    assert(state().current == mask(Reason::UsbRecovery));
    assert(state().transitions[(int)Reason::UsbRecovery] == 1);
  } else if (!strcmp(test, "unknown_mask")) {
    setEnabled(true, 0); decision(UINT32_MAX, 0);
    assert(state().current == ValidMask && state().seen == ValidMask);
    assert(mask(Reason::Count) == 0 && !strcmp(reasonName(Reason::Count), "unknown"));
  } else assert(false);
}
