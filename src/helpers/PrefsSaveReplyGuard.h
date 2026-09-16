#pragma once

#include <stdint.h>
#include <string.h>

// Legacy setters can apply live settings before saving. Never let their final
// OK overwrite a persistence error, including through nested CLI dispatch.
class PrefsSaveReplyGuard {
  const uint32_t& _failures;
  const uint32_t _start;
  char* _reply;

public:
  PrefsSaveReplyGuard(const uint32_t& failures, char* reply)
      : _failures(failures), _start(failures), _reply(reply) {}
  ~PrefsSaveReplyGuard() {
    if (_failures != _start) {
      strcpy(_reply, "Error: settings not saved; unsaved changes may remain active until reboot");
    }
  }
};
