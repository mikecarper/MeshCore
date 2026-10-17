#include <helpers/nrf52/SleepCycleState.h>

void invalidateSleepCycleFromOtherTranslationUnit() {
  mesh_nrf52::sleepCycleState().invalidate();
}
