#pragma once
#include <stdint.h>

struct TagFix {
  int32_t lat, lon;
  uint32_t utc;
  int64_t received_ms;
};

bool tag_gps_available();
bool tag_gps_begin();
void tag_gps_end();
void tag_gps_process();
bool tag_gps_latest(TagFix& result);
