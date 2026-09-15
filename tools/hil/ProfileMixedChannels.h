#pragma once
// Bounded HIL-only four-channel experiment. All symbols are exactly 8192 us.
struct MixedChannelModulation { unsigned sf; float bwKhz; };
static constexpr MixedChannelModulation mixedChannelProfiles[4] = {
  {9,62.5f}, {10,125.0f}, {11,250.0f}, {12,500.0f}
};
static constexpr const char* mixedChannelInfo =
  "{\"mixed_profiles\":1,\"symbol_us\":8192,\"profiles\":[{\"sf\":9,\"bw_khz\":62.5},{\"sf\":10,\"bw_khz\":125},{\"sf\":11,\"bw_khz\":250},{\"sf\":12,\"bw_khz\":500}]}";
