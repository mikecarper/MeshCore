#include <cassert>
#include <helpers/tracker/TrackerEscalation.h>

using mesh::tracker::HomePresence;
using mesh::tracker::TrackerEscalation;

int main() {
  // Exhaust all cause combinations. A home beacon suppresses automatic
  // escalation only; the source flags remain intact for later evaluation.
  for (int manual = 0; manual < 2; ++manual) {
    for (int gps = 0; gps < 2; ++gps) {
      for (int phone = 0; phone < 2; ++phone) {
        TrackerEscalation x = {bool(manual), bool(gps), bool(phone), HomePresence::Unknown};
        assert(!x.automaticSuppressed());
        assert(x.lost() == bool(manual || gps || phone));
        x.home = HomePresence::Present;
        assert(x.automaticSuppressed());
        assert(x.lost() == bool(manual));
        assert(x.gps_outside == bool(gps));
        assert(x.phone_disconnected == bool(phone));
        x.home = HomePresence::Unknown;
        assert(x.lost() == bool(manual || gps || phone));
      }
    }
  }
}
