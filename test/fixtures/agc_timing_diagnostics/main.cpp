#include <helpers/radiolib/AGCTimingDiagnostics.h>

#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

using Diagnostics = AGCTimingDiagnostics;
using Skip = Diagnostics::Skip;
using Step = Diagnostics::Step;

static void earlyBusyReturn(Diagnostics& d) {
  Diagnostics::PeriodicRequest request(d);
  // The caller can reject BUSY before the common sequence/owner guard.
  return;
}

static void periodic() {
  Diagnostics d;
  earlyBusyReturn(d);
  assert(d.periodic_requests == 1 && d.periodic_skipped == 1);
  assert(d.periodic_admitted == 0 && d.last_periodic);
  assert(d.last_skip == Skip::Busy && !d.pending);
  assert(d.shared_requests == 0 && d.shared_skipped == 0);

  const Skip reasons[] = {Skip::Busy, Skip::Beacon, Skip::Carrier, Skip::Packet};
  for (unsigned i = 0; i < sizeof(reasons) / sizeof(reasons[0]); ++i) {
    {
      Diagnostics::PeriodicRequest request(d);
      d.sequenceRequest();
      d.reject(reasons[i]);
      assert(d.last_skip == reasons[i] && d.last_periodic);
    }
    // An explicit rejection resolves the scope; destruction adds no second skip.
    assert(d.periodic_requests == i + 2 && d.periodic_skipped == i + 2);
    assert(d.periodic_admitted == 0 && d.shared_requests == 0);
  }
  {
    Diagnostics::PeriodicRequest request(d);
    d.sequenceRequest();
    d.admit(100);
    d.helperFinished(130);
  }
  assert(d.periodic_requests == 6 && d.periodic_admitted == 1);
  assert(d.periodic_skipped == 5 && d.shared_requests == 0);
  assert(d.last_skip == Skip::None && d.last_periodic && d.pending);
  assert(d.helper_us.count == 1 && d.helper_us.last == 30);
}

static void shared() {
  Diagnostics d;
  const Skip reasons[] = {Skip::Busy, Skip::Beacon, Skip::Carrier, Skip::Packet};
  for (unsigned i = 0; i < sizeof(reasons) / sizeof(reasons[0]); ++i) {
    d.sequenceRequest();
    d.reject(reasons[i]);
    assert(d.shared_requests == i + 1 && d.shared_skipped == i + 1);
    assert(d.shared_admitted == 0 && !d.last_periodic);
    assert(d.last_skip == reasons[i]);
  }
  d.sequenceRequest();
  d.admit(100);
  d.helperFinished(120);
  d.receiveEntry(0, 150);
  assert(d.shared_requests == 5 && d.shared_admitted == 1);
  assert(d.shared_skipped == 4 && d.last_skip == Skip::None);
  assert(!d.last_periodic && !d.pending);
  {
    Diagnostics::PeriodicRequest request(d);
    d.sequenceRequest();
    d.reject(Skip::Carrier);
  }
  assert(d.shared_requests == 5 && d.shared_skipped == 4);
  assert(d.periodic_requests == 1 && d.periodic_skipped == 1);
  assert(d.last_periodic);
  // The RAII scope restores shared attribution for subsequent callers.
  d.sequenceRequest();
  d.admit(200);
  assert(d.shared_requests == 6 && d.shared_admitted == 2);
  assert(d.periodic_admitted == 0 && !d.last_periodic);
}

static void errors() {
  Diagnostics d;
  d.sequenceRequest();
  d.admit(100);
  const Step steps[] = {Step::Sleep, Step::Standby, Step::Calibrate, Step::Image,
                       Step::Dio2, Step::Gain, Step::ReadPatch, Step::WritePatch};
  for (Step step : steps) d.commandResult(step, 0);
  d.helperFinished(140);
  d.receiveEntry(0, 150);
  assert(d.shared_admitted == 1 && d.command_failures == 0);
  assert(d.failed_sequences == 0 && d.last_error == 0);
  assert(d.last_error_step == Step::None);

  {
    Diagnostics::PeriodicRequest request(d);
    d.sequenceRequest();
    d.admit(200);
    d.commandResult(Step::Sleep, -12);
    d.commandResult(Step::Standby, -22);
    d.commandResult(Step::Gain, 0);
    d.pollTimeout();
    d.helperFinished(260);
  }
  // Admission records ownership, even when the command sequence then fails.
  assert(d.periodic_admitted == 1 && d.periodic_skipped == 0);
  assert(d.command_failures == 2 && d.failed_sequences == 1);
  assert(d.poll_timeouts == 1 && d.last_error == -12);
  assert(d.last_error_step == Step::Sleep);
  d.receiveEntry(0, 280);
  assert(d.rx_gap_us.count == 2 && d.rx_gap_us.last == 80);

  d.sequenceRequest();
  d.admit(300);
  assert(d.last_error == 0 && d.last_error_step == Step::None);
  d.pollTimeout();
  d.pollTimeout();
  d.commandResult(Step::Gain, -7);
  d.helperFinished(350);
  assert(d.command_failures == 3 && d.failed_sequences == 2);
  assert(d.poll_timeouts == 3 && d.last_error_step == Step::Poll);
  assert(d.last_error == 0); // The first failure was the poll timeout.
  d.receiveEntry(0, 360);

  d.sequenceRequest();
  d.admit(400);
  d.commandResult(Step::Sleep, 0);
  d.helperFinished(410);
  assert(d.shared_admitted == 3 && d.failed_sequences == 2);
  assert(d.command_failures == 3 && d.last_error == 0);
  assert(d.last_error_step == Step::None);
}

static void timing() {
  Diagnostics d;
  d.sequenceRequest();
  d.admit(100);
  d.helperFinished(140);
  assert(d.helper_us.count == 1 && d.helper_us.last == 40);
  assert(d.helper_us.minimum == 40 && d.helper_us.maximum == 40);
  assert(d.pending && d.rx_gap_us.count == 0);
  d.receiveEntry(0, 190);
  assert(d.rx_gap_us.count == 1 && d.rx_gap_us.last == 90);
  assert(d.rx_gap_us.minimum == 90 && d.rx_gap_us.maximum == 90);
  assert(!d.pending);

  d.sequenceRequest();
  d.admit(1000);
  d.helperFinished(1010);
  d.receiveEntry(0, 1110);
  d.sequenceRequest();
  d.admit(2000);
  d.helperFinished(2070);
  d.receiveEntry(0, 2100);
  assert(d.helper_us.count == 3 && d.helper_us.last == 70);
  assert(d.helper_us.minimum == 10 && d.helper_us.maximum == 70);
  assert(d.rx_gap_us.count == 3 && d.rx_gap_us.last == 100);
  assert(d.rx_gap_us.minimum == 90 && d.rx_gap_us.maximum == 110);
  // Receive calls without an admitted reset do not create samples or failures.
  d.receiveEntry(-9, 2200);
  d.receiveEntry(0, 2300);
  assert(d.rearm_failures == 0 && d.rx_gap_us.count == 3);
  assert(d.superseded_pending == 0);
}

static void pending() {
  Diagnostics d;
  d.sequenceRequest();
  d.admit(100);
  d.helperFinished(120);
  d.receiveEntry(-7, 150);
  d.receiveEntry(-8, 180);
  assert(d.pending && d.rearm_failures == 2 && d.rx_gap_us.count == 0);
  // Guarded requests must not disturb the pending reset-start timestamp.
  d.sequenceRequest();
  d.reject(Skip::Packet);
  earlyBusyReturn(d);
  assert(d.pending && d.superseded_pending == 0);
  d.receiveEntry(0, 230);
  assert(!d.pending && d.rx_gap_us.count == 1 && d.rx_gap_us.last == 130);
  assert(d.helper_us.count == 1 && d.helper_us.last == 20);
  d.receiveEntry(-8, 240);
  d.receiveEntry(0, 250);
  assert(d.rearm_failures == 2 && d.rx_gap_us.count == 1);
  assert(d.command_failures == 0 && d.failed_sequences == 0);
}

static void superseded() {
  Diagnostics d;
  d.sequenceRequest();
  d.admit(100);
  d.helperFinished(110);
  d.receiveEntry(-1, 150);
  {
    Diagnostics::PeriodicRequest request(d);
    d.sequenceRequest();
    d.admit(200);
    d.helperFinished(220);
  }
  assert(d.pending && d.superseded_pending == 1);
  d.receiveEntry(0, 260);
  assert(d.rx_gap_us.count == 1 && d.rx_gap_us.last == 60);
  assert(d.helper_us.count == 2 && d.helper_us.last == 20);
  assert(d.rearm_failures == 1 && !d.pending);
  d.sequenceRequest();
  d.admit(300);
  d.helperFinished(310);
  assert(d.superseded_pending == 1); // A completed RX interval is not overlap.
  d.sequenceRequest();
  d.admit(400);
  d.helperFinished(415);
  assert(d.superseded_pending == 2 && d.pending);
  d.receiveEntry(0, 430);
  assert(d.rx_gap_us.count == 2 && d.rx_gap_us.last == 30 && !d.pending);
  assert(d.helper_us.count == 4 && d.shared_admitted == 3);
  assert(d.shared_requests == 3 && d.periodic_requests == 1);
  assert(d.periodic_admitted == 1);
}

static void wrap() {
  Diagnostics d;
  d.sequenceRequest();
  d.admit(UINT32_MAX - 15U);
  d.helperFinished(4);
  d.receiveEntry(0, 24);
  assert(d.helper_us.last == 20 && d.rx_gap_us.last == 40);
  d.sequenceRequest();
  d.admit(UINT32_MAX - 1U);
  d.helperFinished(UINT32_MAX);
  d.receiveEntry(0, 1);
  assert(d.helper_us.last == 1 && d.rx_gap_us.last == 3);
  d.sequenceRequest();
  d.admit(100);
  d.helperFinished(100);
  d.receiveEntry(0, 1000);
  assert(d.helper_us.count == 3 && d.helper_us.last == 0);
  assert(d.helper_us.minimum == 0 && d.helper_us.maximum == 20);
  assert(d.rx_gap_us.count == 3 && d.rx_gap_us.last == 900);
  assert(d.rx_gap_us.minimum == 3 && d.rx_gap_us.maximum == 900);

  Diagnostics::Samples samples;
  assert(samples.count == 0 && samples.last == 0);
  assert(samples.minimum == 0 && samples.maximum == 0);
  samples.add(UINT32_MAX);
  assert(samples.count == 1 && samples.minimum == UINT32_MAX);
  assert(samples.maximum == UINT32_MAX && samples.last == UINT32_MAX);
  samples.add(0);
  samples.add(42);
  assert(samples.count == 3 && samples.last == 42);
  assert(samples.minimum == 0 && samples.maximum == UINT32_MAX);
}

static void expectPage(const Diagnostics& d, uint8_t page, const char* expected) {
  char full[512];
  d.format(page, full, sizeof(full));
  assert(strcmp(full, expected) == 0);
  assert(strlen(full) <= 157);

  struct Guarded {
    char before;
    char reply[158];
    char after;
  } guarded;
  memset(&guarded, '#', sizeof(guarded));
  d.format(page, guarded.reply, sizeof(guarded.reply));
  assert(guarded.before == '#' && guarded.after == '#');
  // Equality also verifies every key and all ten digits survived the CLI cap.
  assert(strcmp(guarded.reply, expected) == 0);

  memset(&guarded, '#', sizeof(guarded));
  d.format(page, guarded.reply, 8);
  assert(strncmp(guarded.reply, expected, 7) == 0);
  assert(guarded.reply[7] == '\0' && guarded.reply[8] == '#');
  assert(guarded.before == '#' && guarded.after == '#');
  d.format(page, guarded.reply, 1);
  assert(guarded.reply[0] == '\0');
  guarded.reply[0] = '#';
  d.format(page, guarded.reply, 0);
  assert(guarded.reply[0] == '#');
}

static void format() {
  Diagnostics d;
  d.periodic_requests = d.periodic_admitted = d.periodic_skipped = UINT32_MAX;
  d.shared_requests = d.shared_admitted = d.shared_skipped = UINT32_MAX;
  d.rearm_failures = d.command_failures = d.failed_sequences = UINT32_MAX;
  d.poll_timeouts = d.superseded_pending = UINT32_MAX;
  d.helper_us.count = d.helper_us.last = UINT32_MAX;
  d.helper_us.minimum = d.helper_us.maximum = UINT32_MAX;
  d.rx_gap_us = d.helper_us;
  d.pending = d.last_periodic = true;
  d.last_error = INT16_MIN;
  d.last_error_step = Step::WritePatch;
  d.last_skip = Skip::Carrier;
  const char* page0 = "> v=1,pr=4294967295,pa=4294967295,ps=4294967295,"
      "sr=4294967295,sa=4294967295,ss=4294967295,pending=1,origin=periodic,rf=4294967295";
  const char* page1 = "> v=1,h_n=4294967295,h_us=4294967295,h_min=4294967295,"
      "h_max=4294967295,g_n=4294967295,g_us=4294967295,g_min=4294967295,g_max=4294967295";
  const char* page2 = "> v=1,cmdfail=4294967295,seqfail=4294967295,rc=-32768,"
      "step=writepatch,poll=4294967295,overlap=4294967295,skip=carrier";
  expectPage(d, 0, page0);
  expectPage(d, 1, page1);
  expectPage(d, 2, page2);
  expectPage(d, 255, page0);

  const Skip skips[] = {Skip::None, Skip::Busy, Skip::Beacon, Skip::Carrier, Skip::Packet};
  const char* skip_names[] = {"none", "busy", "beacon", "carrier", "packet"};
  for (unsigned i = 0; i < sizeof(skips) / sizeof(skips[0]); ++i)
    assert(strcmp(Diagnostics::skipName(skips[i]), skip_names[i]) == 0);
  const Step steps[] = {Step::None, Step::Sleep, Step::Standby, Step::Calibrate,
                       Step::Image, Step::Dio2, Step::Gain, Step::ReadPatch,
                       Step::WritePatch, Step::Poll};
  const char* step_names[] = {"none", "sleep", "standby", "calibrate", "image",
                            "dio2", "gain", "readpatch", "writepatch", "poll"};
  for (unsigned i = 0; i < sizeof(steps) / sizeof(steps[0]); ++i)
    assert(strcmp(Diagnostics::stepName(steps[i]), step_names[i]) == 0);
}

int main(int argc, char** argv) {
  assert(argc == 2);
  const char* scenario = argv[1];
  if (!strcmp(scenario, "periodic")) periodic();
  else if (!strcmp(scenario, "shared")) shared();
  else if (!strcmp(scenario, "errors")) errors();
  else if (!strcmp(scenario, "timing")) timing();
  else if (!strcmp(scenario, "pending")) pending();
  else if (!strcmp(scenario, "superseded")) superseded();
  else if (!strcmp(scenario, "wrap")) wrap();
  else if (!strcmp(scenario, "format")) format();
  else assert(false);
  printf("PASS: %s\n", scenario);
}
