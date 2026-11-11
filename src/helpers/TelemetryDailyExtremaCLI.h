#pragma once

#include <stdio.h>
#include <string.h>
#include "TelemetryDailyExtrema.h"
#include "TelemetryHistory.h"

namespace mesh {
namespace telemetry_daily_cli {

inline const char* spaces(const char* p) { while (*p == ' ') ++p; return p; }

struct Reader {
  const TelemetryHistory& history;
  TelemetryHistory::Series series;
  uint16_t operator()(uint16_t offset) const {
    uint16_t code = 0;
    history.historyCodeAtOffset(series, offset, code);
    return code;
  }
};

// Handle only extrema requests; normal telemetry pages keep their existing
// handlers and correlated replies retain their existing command prefix.
inline bool handle(const char* command, const TelemetryHistory& history,
                   char* reply, size_t capacity, bool include_prompt,
                   bool permitted) {
  if (reply == NULL || capacity == 0 || command == NULL) return false;
  static const char combined[] = "get telemetry.minmax";
  static const char temperature[] = "get telemetry.temp";
  static const char voltage[] = "get telemetry.volt";
  const size_t combined_len = sizeof(combined) - 1U;
  bool both = false;
  TelemetryDailyExtrema::Series series = TelemetryDailyExtrema::TEMPERATURE;
  const char* args;
  if (strncmp(command, combined, combined_len) == 0
      && (command[combined_len] == 0 || command[combined_len] == ' ')) {
    both = true;
    args = command + combined_len;
  } else {
    const size_t tlen = sizeof(temperature) - 1U;
    const size_t vlen = sizeof(voltage) - 1U;
    if (strncmp(command, temperature, tlen) == 0
        && (command[tlen] == 0 || command[tlen] == ' ')) {
      args = command + tlen;
    } else if (strncmp(command, voltage, vlen) == 0
               && (command[vlen] == 0 || command[vlen] == ' ')) {
      series = TelemetryDailyExtrema::BATTERY_VOLTAGE;
      args = command + vlen;
    } else return false;
    args = spaces(args);
    static const char token[] = "minmax";
    if (strncmp(args, token, sizeof(token) - 1U) != 0
        || (args[sizeof(token) - 1U] != 0 && args[sizeof(token) - 1U] != ' '))
      return false;
    args += sizeof(token) - 1U;
  }
  if (!permitted) {
    snprintf(reply, capacity, "Err - not permitted");
    return true;
  }
  if (*spaces(args) != 0) {
    snprintf(reply, capacity, both ? "Err - use: get telemetry.minmax"
        : series == TelemetryDailyExtrema::TEMPERATURE
            ? "Err - use: get telemetry.temp minmax"
            : "Err - use: get telemetry.volt minmax");
    return true;
  }
  uint32_t bucket;
  if (!history.latestHistoryBucket(bucket)) {
    snprintf(reply, capacity, "Err - telemetry history is empty");
    return true;
  }
  const Reader t{history, TelemetryHistory::SERIES_TEMPERATURE};
  const Reader v{history, TelemetryHistory::SERIES_VOLTAGE};
  const bool ok = both
      ? TelemetryDailyExtrema::formatTemperatureVoltage(
            bucket, t, v, reply, capacity, include_prompt)
      : TelemetryDailyExtrema::format(series, 0, bucket, 1800,
            series == TelemetryDailyExtrema::TEMPERATURE ? t : v,
            reply, capacity, include_prompt);
  if (!ok) snprintf(reply, capacity, "Err - telemetry minmax reply unavailable");
  return true;
}

}  // namespace telemetry_daily_cli
}  // namespace mesh
