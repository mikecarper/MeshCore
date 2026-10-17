#include <helpers/PowerSavingDiagnostics.h>

void powerDiagnosticsOtherTranslationUnit() {
  mesh::powerdiag::decision(mesh::powerdiag::mask(mesh::powerdiag::Reason::UsbRecovery), 1);
}
