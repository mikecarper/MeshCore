"""Compile the real USB watchdog policy and exercise its bounded timelines."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <initializer_list>
#include <limits>
#include <helpers/UsbLoggingWatchdogPolicy.h>

using Policy = mesh::UsbLoggingWatchdogPolicy;
using Action = Policy::Action;
using Observation = mesh::UsbLoggingObservation;
using AutoArm = mesh::UsbLoggingAutoArmPolicy;

struct Clock {
  uint64_t elapsed = 0;
  uint32_t initial = 0;
  explicit Clock(uint32_t first = 0) : initial(first) {}
  uint32_t now() const { return uint32_t(uint64_t(initial) + elapsed); }
  void advance(uint64_t duration) { elapsed += duration; }
};

Observation missing() { return {true, false, false, false, 0}; }
Observation idle() { return {true, true, true, false, 0}; }
Observation pending() { return {true, true, true, true, 0}; }

static void check_intervals_and_corruption() {
  const unsigned hours[] = {1, 2, 4, 8, 16, 32, 64, 128, 168};
  for (uint8_t step = 0; step <= Policy::MAX_STEP; ++step) {
    assert(Policy::intervalMs(step) == hours[step] * Policy::BASE_MS);
    assert(Policy::nextStep(step) == (step == 8 ? 8 : step + 1));
    Policy policy;
    policy.begin(0, step);
    assert(policy.update(0, true, missing()) == Action::None);
    const auto timeout = Policy::intervalMs(step);
    assert(policy.update(Policy::EARLY_RECOVERY_MS - 1, true, missing()) == Action::None);
    assert(policy.update(Policy::EARLY_RECOVERY_MS, true, missing()) == Action::SoftRecovery);
    policy.actionAttempted(Action::SoftRecovery);
    assert(policy.update(Policy::EARLY_RECOVERY_MS + Policy::RECOVERY_GRACE_MS - 1,
                         true, missing()) == Action::None);
    assert(policy.update(Policy::EARLY_RECOVERY_MS + Policy::RECOVERY_GRACE_MS,
                         true, missing()) == Action::Reenumerate);
    policy.actionAttempted(Action::Reenumerate);
    assert(policy.update(timeout - 1, true, missing()) == Action::None);
    assert(policy.update(timeout, true, missing()) == Action::Reboot);
    assert(policy.backoffStep() == step); // An action never persists a tier.
    assert(policy.inactiveSeconds() == timeout / 1000);
  }
  for (uint8_t corrupt : {uint8_t(9), uint8_t(127), uint8_t(255)}) {
    Policy policy;
    policy.begin(0, corrupt);
    assert(policy.backoffStep() == 8);
    assert(Policy::intervalMs(corrupt) == Policy::MAX_MS);
    assert(Policy::nextStep(corrupt) == 8);
    assert(policy.update(0, true, missing()) == Action::None);
    assert(policy.update(Policy::EARLY_RECOVERY_MS - 1, true, missing()) == Action::None);
    assert(policy.update(Policy::MAX_MS - 1, true, missing()) == Action::SoftRecovery);
    assert(policy.update(Policy::MAX_MS, true, missing()) == Action::SoftRecovery);
    policy.backoffCommitted(corrupt);
    assert(policy.backoffStep() == 8);
  }
}

static void check_missing_unopened_and_unplugged() {
  for (const auto observation : {missing(), Observation{true, true, false, false, 0},
                                Observation{true, false, true, false, 0}}) {
    Policy policy;
    policy.begin(500, 0);
    assert(policy.update(500, true, observation) == Action::None);
    for (uint32_t elapsed = 100; elapsed < Policy::BASE_MS; elapsed += 100) {
      assert(policy.update(500 + elapsed, true, observation)
             == (elapsed < Policy::EARLY_RECOVERY_MS ? Action::None : Action::SoftRecovery));
    }
    assert(policy.update(500 + Policy::BASE_MS, true, observation) == Action::SoftRecovery);
    assert(policy.inactiveSeconds() == 3600);
  }
  Policy policy;
  policy.begin(0, 0);
  assert(policy.update(0, true, idle()) == Action::None);
  auto unplugged = missing();
  assert(policy.update(1000, true, unplugged) == Action::None);
  assert(policy.update(1000 + Policy::EARLY_RECOVERY_MS - 1, true, unplugged) == Action::None);
  assert(policy.update(1000 + Policy::BASE_MS, true, unplugged) == Action::SoftRecovery);
  policy.actionAttempted(Action::SoftRecovery);
  assert(policy.update(1001 + Policy::BASE_MS, true, idle()) == Action::None);
  assert(policy.stage() == 0 && policy.inactiveSeconds() == 0);
}

static void check_idle_and_pending_stall() {
  Policy policy;
  policy.begin(0, 0);
  for (uint32_t day = 0; day <= 80; ++day) {
    assert(policy.update(day * 86400000U, true, idle()) == Action::None);
    assert(!policy.stalled() && policy.inactiveSeconds() == 0);
  }
  Clock clock;
  Policy stalled;
  stalled.begin(clock.now(), 0);
  auto observation = pending();
  assert(stalled.update(clock.now(), true, observation) == Action::None);
  clock.advance(Policy::STALL_OBSERVE_MS - 1);
  assert(stalled.update(clock.now(), true, observation) == Action::None);
  assert(!stalled.stalled());
  clock.advance(1);
  assert(stalled.update(clock.now(), true, observation) == Action::None);
  assert(stalled.stalled() && stalled.inactiveSeconds() == 30);
  clock.advance(Policy::EARLY_RECOVERY_MS - Policy::STALL_OBSERVE_MS - 1);
  assert(stalled.update(clock.now(), true, observation) == Action::None);
  clock.advance(1);
  assert(stalled.update(clock.now(), true, observation) == Action::SoftRecovery);
  stalled.actionAttempted(Action::SoftRecovery);
  assert(stalled.stage() == 1 && stalled.stalled());

  // Destructive purge can remove demand, but cannot fake completion evidence.
  observation.pending = false;
  clock.advance(1);
  assert(stalled.update(clock.now(), true, observation) == Action::None);
  assert(stalled.stalled() && stalled.stage() == 1);
  clock.advance(Policy::RECOVERY_GRACE_MS - 1);
  assert(stalled.update(clock.now(), true, observation) == Action::Reenumerate);
  stalled.actionAttempted(Action::Reenumerate);
  clock.advance(Policy::RECOVERY_GRACE_MS - 1);
  assert(stalled.update(clock.now(), true, observation) == Action::None);
  clock.advance(1);
  assert(stalled.update(clock.now(), true, observation) == Action::None);
  clock.advance(Policy::BASE_MS - clock.elapsed - 1);
  assert(stalled.update(clock.now(), true, observation) == Action::None);
  clock.advance(1);
  assert(stalled.update(clock.now(), true, observation) == Action::Reboot);
  assert(stalled.backoffStep() == 0); // Caller still must persist nextStep().
}

static void check_stages_and_deferred_retries() {
  Policy policy;
  Clock clock;
  auto observation = missing();
  policy.begin(clock.now(), 0);
  assert(policy.update(clock.now(), true, observation) == Action::None);
  clock.advance(Policy::EARLY_RECOVERY_MS);
  assert(policy.update(clock.now(), true, observation, false) == Action::None);
  assert(policy.deferred() && policy.stage() == 0);
  clock.advance(1000);
  assert(policy.update(clock.now(), true, observation, false) == Action::None);
  assert(policy.update(clock.now(), true, observation, true) == Action::SoftRecovery);
  // Backend Deferred: leave action unnoted and retry it without timer extension.
  clock.advance(1);
  assert(policy.update(clock.now(), true, observation) == Action::SoftRecovery);
  policy.actionAttempted(Action::SoftRecovery);
  assert(policy.stage() == 1);
  clock.advance(Policy::RECOVERY_GRACE_MS - 1);
  assert(policy.update(clock.now(), true, observation) == Action::None);
  clock.advance(1);
  assert(policy.update(clock.now(), true, observation, false) == Action::None);
  assert(policy.deferred() && policy.stage() == 1);
  assert(policy.update(clock.now(), true, observation) == Action::Reenumerate);
  clock.advance(1);
  assert(policy.update(clock.now(), true, observation) == Action::Reenumerate);
  policy.actionAttempted(Action::Reenumerate);
  assert(policy.stage() == 2);
  clock.advance(Policy::RECOVERY_GRACE_MS - 1);
  assert(policy.update(clock.now(), true, observation) == Action::None);
  clock.advance(1);
  assert(policy.update(clock.now(), true, observation, false) == Action::None);
  assert(policy.deferred() && policy.backoffStep() == 0);
  assert(policy.update(clock.now(), true, observation) == Action::None);
  clock.advance(Policy::BASE_MS - clock.elapsed);
  assert(policy.update(clock.now(), true, observation) == Action::Reboot);
  clock.advance(1);
  assert(policy.update(clock.now(), true, observation) == Action::Reboot);
  policy.backoffCommitted(Policy::nextStep(policy.backoffStep()));
  assert(policy.backoffStep() == 1);
  policy.actionAttempted(Action::Reboot);
  assert(policy.stage() == 3);
}

static void check_progress_aborts_and_counter_wrap() {
  for (unsigned stage = 1; stage <= 2; ++stage) {
    Policy policy;
    Clock clock;
    auto observation = pending();
    policy.begin(clock.now(), 0);
    assert(policy.update(clock.now(), true, observation) == Action::None);
    clock.advance(Policy::BASE_MS);
    assert(policy.update(clock.now(), true, observation) == Action::SoftRecovery);
    policy.actionAttempted(Action::SoftRecovery);
    if (stage == 2) {
      clock.advance(Policy::RECOVERY_GRACE_MS);
      assert(policy.update(clock.now(), true, observation) == Action::Reenumerate);
      policy.actionAttempted(Action::Reenumerate);
    }
    observation.pending = false;
    clock.advance(1);
    assert(policy.update(clock.now(), true, observation) == Action::None);
    assert(policy.stage() == stage && policy.stalled());
    ++observation.tx_progress; // Only the backend's actual ACK advances this.
    assert(policy.update(clock.now(), true, observation, false) == Action::None);
    assert(policy.stage() == 0 && !policy.stalled() && policy.inactiveSeconds() == 0);
  }
  Policy policy;
  auto observation = pending();
  observation.tx_progress = UINT32_MAX;
  policy.begin(0, 0);
  policy.update(0, true, observation);
  policy.update(Policy::STALL_OBSERVE_MS, true, observation);
  assert(policy.stalled());
  observation.tx_progress = 0;
  assert(policy.update(Policy::STALL_OBSERVE_MS + 1, true, observation) == Action::None);
  assert(!policy.stalled() && policy.inactiveSeconds() == 0);
  assert(policy.update(Policy::STALL_OBSERVE_MS * 2, true, observation) == Action::None);
  assert(!policy.stalled());
}

static void check_healthy_reset_requires_continuity_and_commit() {
  Policy policy;
  policy.begin(0, 4);
  assert(policy.update(0, true, idle()) == Action::None);
  assert(policy.update(Policy::HEALTHY_RESET_MS - 1, true, idle()) == Action::None);
  assert(policy.update(Policy::HEALTHY_RESET_MS, true, idle(), false) == Action::None);
  assert(policy.deferred() && policy.backoffStep() == 4);
  assert(policy.update(Policy::HEALTHY_RESET_MS, true, idle()) == Action::ResetBackoff);
  assert(policy.backoffStep() == 4); // Persistence not acknowledged yet.
  assert(policy.update(Policy::HEALTHY_RESET_MS + 1, true, idle()) == Action::ResetBackoff);
  policy.backoffCommitted(0);
  assert(policy.update(Policy::HEALTHY_RESET_MS + 2, true, idle()) == Action::None);

  policy.begin(0, 7);
  assert(policy.update(0, true, idle()) == Action::None);
  assert(policy.update(300000, true, missing()) == Action::None);
  assert(policy.update(400000, true, idle()) == Action::None);
  assert(policy.update(400000 + Policy::HEALTHY_RESET_MS - 1, true, idle()) == Action::None);
  assert(policy.update(400000 + Policy::HEALTHY_RESET_MS, true, idle()) == Action::ResetBackoff);
}

static void check_disabled_unsupported_and_reenable() {
  Policy policy;
  policy.begin(0, 2);
  assert(policy.update(0, true, missing()) == Action::None);
  assert(policy.update(Policy::BASE_MS * 2, false, missing()) == Action::None);
  assert(policy.inactiveSeconds() == 0 && policy.stage() == 0);
  const auto start = Policy::BASE_MS * 2 + 1;
  assert(policy.update(start, true, missing()) == Action::None);
  assert(policy.update(start + Policy::EARLY_RECOVERY_MS - 1, true, missing()) == Action::None);
  assert(policy.update(start + Policy::EARLY_RECOVERY_MS, true, missing()) == Action::SoftRecovery);
  assert(policy.backoffStep() == 2);
  policy.actionAttempted(Action::SoftRecovery);
  assert(policy.update(start + Policy::BASE_MS * 4 + 1, false, missing()) == Action::None);
  assert(policy.stage() == 0 && !policy.stalled() && !policy.deferred());

  Observation unsupported;
  policy.begin(0, 0);
  assert(policy.update(0, true, unsupported) == Action::None);
  assert(policy.update(Policy::MAX_MS, true, unsupported) == Action::None);
  assert(policy.inactiveSeconds() == 0);
  assert(policy.update(Policy::MAX_MS + 1, true, missing()) == Action::None);
  assert(policy.update(Policy::MAX_MS + Policy::EARLY_RECOVERY_MS, true, missing()) == Action::None);
  assert(policy.update(Policy::MAX_MS + Policy::EARLY_RECOVERY_MS + 1, true, missing()) == Action::SoftRecovery);
}

static void check_millis_wrap_long_run_and_old_samples() {
  Policy policy;
  Clock clock(UINT32_MAX - 1500);
  policy.begin(clock.now(), 8);
  assert(policy.update(clock.now(), true, missing()) == Action::None);
  for (unsigned day = 1; day <= 150; ++day) {
    clock.advance(86400000UL);
    const auto action = policy.update(clock.now(), true, missing());
    assert(action == Action::SoftRecovery);
    assert(policy.inactiveSeconds() == day * 86400U);
  }
  // Status saturates instead of rolling over after 136 years of elapsed time.
  for (unsigned day = 151; day <= 49712; ++day) {
    clock.advance(86400000UL);
    assert(policy.update(clock.now(), true, missing()) == Action::SoftRecovery);
  }
  assert(policy.inactiveSeconds() == UINT32_MAX);

  policy.begin(1000, 0);
  policy.update(1000, true, missing());
  policy.update(1100, true, missing());
  policy.update(1090, true, missing()); // Brief out-of-order sample is ignored.
  assert(policy.inactiveSeconds() == 0);
  assert(policy.update(1000 + Policy::EARLY_RECOVERY_MS - 1, true, missing()) == Action::None);
  assert(policy.update(1000 + Policy::EARLY_RECOVERY_MS, true, missing()) == Action::SoftRecovery);
}

static void check_pending_resets_after_real_progress() {
  Policy policy;
  auto observation = pending();
  policy.begin(0, 0);
  policy.update(0, true, observation);
  assert(policy.update(Policy::EARLY_RECOVERY_MS - 1, true, observation) == Action::None);
  assert(policy.stalled());
  observation.tx_progress = 1;
  assert(policy.update(Policy::EARLY_RECOVERY_MS - 1, true, observation) == Action::None);
  assert(!policy.stalled());
  assert(policy.update(Policy::EARLY_RECOVERY_MS * 2 - 2, true, observation) == Action::None);
  assert(policy.update(Policy::EARLY_RECOVERY_MS * 2 - 1, true, observation) == Action::SoftRecovery);
}

static void check_pre_recovery_purge_cannot_erase_a_stall() {
  Policy policy;
  auto observation = pending();
  policy.begin(0, 0);
  assert(policy.update(0, true, observation) == Action::None);
  assert(policy.update(Policy::STALL_OBSERVE_MS, true, observation) == Action::None);
  assert(policy.stalled());
  observation.pending = false; // An external queue/session cleanup, not ACK.
  assert(policy.update(Policy::STALL_OBSERVE_MS + 1, true, observation) == Action::None);
  assert(policy.stalled() && policy.inactiveSeconds() == 30);
  assert(policy.update(Policy::BASE_MS, true, observation) == Action::SoftRecovery);
  policy.actionAttempted(Action::SoftRecovery);
  assert(policy.update(Policy::BASE_MS + 1, true, observation) == Action::None);
  assert(policy.stalled() && policy.stage() == 1);
  ++observation.tx_progress;
  assert(policy.update(Policy::BASE_MS + 2, true, observation) == Action::None);
  assert(!policy.stalled() && policy.stage() == 0);
}

static void check_restarted_interval_preserves_completion_requirement() {
  for (uint8_t step : {uint8_t(0), uint8_t(1), uint8_t(8)})
  for (uint32_t origin : {0U, UINT32_MAX - 360100U})
  for (bool purged : {false, true}) {
    Policy policy;
    Clock clock(origin);
    auto observation = pending();
    observation.tx_progress = UINT32_MAX;
    policy.begin(clock.now(), step);
    assert(policy.update(clock.now(), true, observation) == Action::None);
    clock.advance(Policy::EARLY_RECOVERY_MS);
    assert(policy.update(clock.now(), true, observation) == Action::SoftRecovery);
    policy.actionAttempted(Action::SoftRecovery);
    clock.advance(Policy::RECOVERY_GRACE_MS);
    assert(policy.update(clock.now(), true, observation) == Action::Reenumerate);
    policy.actionAttempted(Action::Reenumerate);
    observation.pending = !purged;
    clock.advance(Policy::intervalMs(step) - clock.elapsed);
    assert(policy.update(clock.now(), true, observation) == Action::Reboot);
    const uint8_t next = Policy::nextStep(step);
    policy.backoffCommitted(next);
    policy.restartInterval(); // Durable request was cancelled by its owner.
    assert(policy.stalled() && "restart must retain unresolved USB stall");
    assert(policy.stage() == 0 && policy.inactiveSeconds() == 0);
    const uint64_t restart = clock.elapsed;
    clock.advance(Policy::EARLY_RECOVERY_MS - 1);
    assert(policy.update(clock.now(), true, observation) == Action::None);
    assert(policy.stalled() && policy.inactiveSeconds() == 299);
    clock.advance(1);
    assert(policy.update(clock.now(), true, observation) == Action::SoftRecovery);
    policy.actionAttempted(Action::SoftRecovery);
    clock.advance(Policy::RECOVERY_GRACE_MS - 1);
    assert(policy.update(clock.now(), true, observation) == Action::None);
    clock.advance(1);
    assert(policy.update(clock.now(), true, observation) == Action::Reenumerate);
    policy.actionAttempted(Action::Reenumerate);
    clock.advance(Policy::HEALTHY_RESET_MS - (clock.elapsed - restart));
    assert(policy.update(clock.now(), true, observation) == Action::None);
    assert(policy.stalled() && policy.backoffStep() == next && policy.stage() == 2);
    clock.advance(Policy::intervalMs(next) - (clock.elapsed - restart) - 1);
    assert(policy.update(clock.now(), true, observation) == Action::None);
    clock.advance(1);
    assert(policy.update(clock.now(), true, observation) == Action::Reboot);
    assert(policy.inactiveSeconds() == Policy::intervalMs(next) / 1000);

    // Real completion, including counter wrap, still restores health and can
    // reset the durable tier after the normal continuous healthy interval.
    ++observation.tx_progress;
    observation.pending = false;
    assert(policy.update(clock.now(), true, observation) == Action::None);
    policy.restartInterval();
    assert(!policy.stalled() && policy.stage() == 0 && policy.inactiveSeconds() == 0);
    clock.advance(Policy::HEALTHY_RESET_MS - 1);
    assert(policy.update(clock.now(), true, observation) == Action::None);
    clock.advance(1);
    assert(policy.update(clock.now(), true, observation) == Action::ResetBackoff);
  }
}

static void check_auto_qualification_is_continuous() {
  AutoArm policy;
  policy.begin(100);
  assert(!policy.update(100, true, true));
  assert(policy.connectedSeconds() == 0);
  assert(!policy.update(100 + AutoArm::QUALIFICATION_MS - 1, true, true));
  assert(policy.connectedSeconds() == AutoArm::QUALIFICATION_MS / 1000 - 1);
  assert(policy.update(100 + AutoArm::QUALIFICATION_MS, true, true));
  assert(policy.connectedSeconds() == 14U * 86400U);

  // Disconnect, DTR closure, observed stall, disabled master, unsupported
  // transport and non-durable storage all map to qualifying=false. None can
  // accumulate separate cable sessions into the required continuous period.
  for (unsigned reason = 0; reason != 6; ++reason) {
    policy.begin(0);
    assert(!policy.update(0, true, true));
    assert(!policy.update(AutoArm::QUALIFICATION_MS - 1, true, true));
    assert(!policy.update(AutoArm::QUALIFICATION_MS, false, true));
    assert(policy.connectedSeconds() == 0);
    const uint32_t restarted = AutoArm::QUALIFICATION_MS + 1;
    assert(!policy.update(restarted, true, true));
    assert(!policy.update(restarted + AutoArm::QUALIFICATION_MS - 1, true, true));
    assert(policy.update(restarted + AutoArm::QUALIFICATION_MS, true, true));
  }
  policy.begin(0);
  assert(!policy.update(0, true, true));
  assert(!policy.update(AutoArm::QUALIFICATION_MS - 1, true, true));
  assert(!policy.update(AutoArm::QUALIFICATION_MS, true, false));
  assert(policy.connectedSeconds() == 0);
  assert(!policy.update(AutoArm::QUALIFICATION_MS + 1, true, true));
  assert(!policy.update(AutoArm::QUALIFICATION_MS * 2, true, true));
  assert(policy.update(AutoArm::QUALIFICATION_MS * 2 + 1, true, true));
  // A reboot always loses the unprovable connected interval.
  policy.begin(AutoArm::QUALIFICATION_MS * 2 + 1);
  assert(!policy.update(AutoArm::QUALIFICATION_MS * 2 + 1, true, true));
  assert(policy.connectedSeconds() == 0);
}

static void check_auto_clock_wrap_and_saturation() {
  AutoArm policy;
  Clock clock(UINT32_MAX - 1000);
  policy.begin(clock.now());
  assert(!policy.update(clock.now(), true, true));
  for (unsigned day = 1; day <= 150; ++day) {
    clock.advance(86400000UL);
    assert(policy.update(clock.now(), true, true) == (day >= 14));
    assert(policy.connectedSeconds() == day * 86400U);
  }
  for (unsigned day = 151; day <= 49712; ++day) {
    clock.advance(86400000UL);
    assert(policy.update(clock.now(), true, true));
  }
  assert(policy.connectedSeconds() == UINT32_MAX);
  assert(!policy.update(clock.now(), false, true));
  assert(policy.connectedSeconds() == 0);

  policy.begin(1000);
  assert(!policy.update(1000, true, true));
  assert(!policy.update(1100, true, true));
  assert(!policy.update(1090, true, true));
  assert(policy.connectedSeconds() == 0);
  assert(!policy.update(1000 + AutoArm::QUALIFICATION_MS - 1, true, true));
  assert(policy.update(1000 + AutoArm::QUALIFICATION_MS, true, true));
}

static void check_auto_stall_observation_does_not_arm_recovery() {
  Policy policy;
  auto observation = pending();
  policy.begin(0, 0);
  assert(policy.update(0, false, observation) == Action::None);
  assert(policy.update(Policy::STALL_OBSERVE_MS, false, observation) == Action::None);
  assert(policy.stalled() && policy.inactiveSeconds() == 0);
  assert(policy.update(Policy::BASE_MS * 20, false, observation) == Action::None);
  assert(policy.stalled() && policy.stage() == 0);
  // Promotion to On starts a new complete interval, never inherits Auto age.
  const uint32_t activation = Policy::BASE_MS * 20 + 1;
  assert(policy.update(activation, true, observation) == Action::None);
  assert(policy.update(activation + Policy::EARLY_RECOVERY_MS - 1, true, observation) == Action::None);
  assert(policy.update(activation + Policy::EARLY_RECOVERY_MS, true, observation) == Action::SoftRecovery);
}

static void check_auto_usb_only_recovery_vetoes_board_reset() {
  Policy policy;
  policy.begin(0, 0);
  auto observation = missing();
  assert(policy.update(0, true, observation, true, false) == Action::None);
  assert(policy.update(Policy::EARLY_RECOVERY_MS, true, observation, false, false) == Action::None);
  assert(policy.deferred());
  assert(policy.update(Policy::EARLY_RECOVERY_MS, true, observation, true, false) == Action::SoftRecovery);
  policy.actionAttempted(Action::SoftRecovery);
  const uint32_t reenum = Policy::EARLY_RECOVERY_MS + Policy::RECOVERY_GRACE_MS;
  assert(policy.update(reenum, true, observation, true, false) == Action::Reenumerate);
  policy.actionAttempted(Action::Reenumerate);
  for (uint32_t day = 1; day <= 80; ++day) {
    assert(policy.update(day * 86400000U, true, observation, true, false) == Action::None);
    assert(policy.stage() == 2 && policy.backoffStep() == 0);
  }
  assert(policy.update(80U * 86400000U, true, observation, false, true) == Action::None);
  assert(policy.deferred());
  assert(policy.update(80U * 86400000U, true, observation, true, true) == Action::Reboot);
}

static void check_ack_without_effective_reader_cannot_extend_outage() {
  for (auto observation : {Observation{true, true, false, false, 0},
                           Observation{true, false, true, false, 0}}) {
    Policy policy;
    policy.begin(0, 0);
    assert(policy.update(0, true, observation) == Action::None);
    for (uint32_t elapsed = 10000; elapsed < Policy::EARLY_RECOVERY_MS; elapsed += 10000) {
      ++observation.tx_progress;
      assert(policy.update(elapsed, true, observation) == Action::None);
    }
    ++observation.tx_progress;
    assert(policy.update(Policy::EARLY_RECOVERY_MS, true, observation) == Action::SoftRecovery);
    assert(policy.inactiveSeconds() == 300);
    policy.actionAttempted(Action::SoftRecovery);
    ++observation.tx_progress;
    const auto reenum = Policy::EARLY_RECOVERY_MS + Policy::RECOVERY_GRACE_MS;
    assert(policy.update(reenum, true, observation) == Action::Reenumerate);
    policy.actionAttempted(Action::Reenumerate);
    ++observation.tx_progress;
    assert(policy.update(Policy::BASE_MS - 1, true, observation) == Action::None);
    ++observation.tx_progress;
    assert(policy.update(Policy::BASE_MS, true, observation) == Action::Reboot);
  }
}

int main() {
  check_intervals_and_corruption();
  check_missing_unopened_and_unplugged();
  check_idle_and_pending_stall();
  check_stages_and_deferred_retries();
  check_progress_aborts_and_counter_wrap();
  check_healthy_reset_requires_continuity_and_commit();
  check_disabled_unsupported_and_reenable();
  check_millis_wrap_long_run_and_old_samples();
  check_pending_resets_after_real_progress();
  check_pre_recovery_purge_cannot_erase_a_stall();
  check_restarted_interval_preserves_completion_requirement();
  check_auto_qualification_is_continuous();
  check_auto_clock_wrap_and_saturation();
  check_auto_stall_observation_does_not_arm_recovery();
  check_auto_usb_only_recovery_vetoes_board_reset();
  check_ack_without_effective_reader_cannot_extend_outage();
  puts("USB watchdog policy timelines passed");
}
'''


class UsbLoggingWatchdogTest(unittest.TestCase):
    def test_production_policy_timelines(self):
        compiler = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        if compiler is None:
            self.skipTest('a host C++17 compiler is required')
        sanitizer_flags = [] if os.name == 'nt' else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-fno-pie', '-no-pie']
        with tempfile.TemporaryDirectory(prefix='usb-watchdog-policy-') as directory:
            work = Path(directory)
            cpp = work / 'test.cpp'
            binary = work / ('test.exe' if os.name == 'nt' else 'test')
            cpp.write_text(HARNESS, encoding='utf-8')
            built = subprocess.run([
                compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                *sanitizer_flags, '-I' + str(ROOT / 'src'),
                str(cpp), '-o', str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            tested = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(tested.returncode, 0, tested.stderr)
            self.assertIn('USB watchdog policy timelines passed', tested.stdout)

    def test_old_restart_clear_has_intended_semantic_failure(self):
        compiler = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        if compiler is None:
            self.skipTest('a host C++17 compiler is required')
        header = (ROOT / 'src/helpers/UsbLoggingWatchdogPolicy.h').read_text(encoding='ascii')
        restart = '    _stage = 0;\n    _fault_since = _now;\n'
        self.assertEqual(header.count(restart), 1)
        legacy = header.replace(restart, restart + '    _need_progress = false;\n', 1)
        sanitizer_flags = [] if os.name == 'nt' else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-fno-pie', '-no-pie']
        with tempfile.TemporaryDirectory(prefix='usb-watchdog-restart-negative-') as directory:
            work = Path(directory)
            (work / 'helpers').mkdir()
            (work / 'helpers/UsbLoggingWatchdogPolicy.h').write_text(legacy, encoding='ascii')
            cpp = work / 'test.cpp'
            binary = work / ('test.exe' if os.name == 'nt' else 'test')
            cpp.write_text(HARNESS, encoding='ascii')
            built = subprocess.run([
                compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                *sanitizer_flags, '-I' + str(work), '-I' + str(ROOT / 'src'),
                '-I' + str(ROOT / 'src/helpers'), str(cpp), '-o', str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            tested = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertNotEqual(tested.returncode, 0)
            self.assertIn('restart must retain unresolved USB stall', tested.stderr)
            self.assertNotIn('runtime error:', tested.stderr)
            self.assertNotIn('AddressSanitizer:', tested.stderr)


if __name__ == '__main__':
    unittest.main()
