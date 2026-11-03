#pragma once

namespace mesh { namespace tracker {

// A home signal is Present only after the caller validates a fresh,
// authenticated beacon. Missing, stale, unconfigured, or unverifiable home
// signals are Unknown. This function never changes the underlying causes.
enum class HomePresence { Unknown, Present };

struct TrackerEscalation {
  bool manual_lost;
  bool gps_outside;
  bool phone_disconnected;
  HomePresence home;

  bool automaticSuppressed() const { return home == HomePresence::Present; }
  bool lost() const {
    return manual_lost || (!automaticSuppressed() &&
                           (gps_outside || phone_disconnected));
  }
};

}} // namespace mesh::tracker
