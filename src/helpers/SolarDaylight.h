#pragma once

#include <stdint.h>

namespace mesh { namespace solar {

enum class State : uint8_t { Unknown, Day, Night };

struct Window {
  State state;
  // Next sunrise/sunset, or a six-hour reevaluation during polar day/night.
  // Saturates at the end of the RTC range instead of wrapping into 1970.
  uint32_t nextChangeUtc;
};

namespace daylight_detail {

constexpr int32_t Scale = 32768;
constexpr uint32_t PlausibleEpoch = 1577836800UL;
constexpr uint32_t PolarRecheckSeconds = 6UL * 3600UL;

// Five-degree samples and linear interpolation keep the daily NOAA Fourier
// calculation integer-only. Angles are millidegrees, results are Q15.
inline int32_t sine(int32_t angle) {
  static constexpr uint16_t quarter[] = {
      0, 2856, 5690, 8481, 11207, 13848, 16384, 18795, 21063, 23170,
      25102, 26842, 28378, 29698, 30792, 31651, 32270, 32643, 32768};
  angle %= 360000;
  if (angle < 0) angle += 360000;
  const bool negative = angle > 180000;
  if (negative) angle -= 180000;
  if (angle > 90000) angle = 180000 - angle;
  const unsigned index = unsigned(angle / 5000);
  int32_t value = quarter[index];
  if (index < 18)
    value += ((int32_t(quarter[index + 1]) - value) * (angle % 5000) + 2500) / 5000;
  return negative ? -value : value;
}

inline int32_t roundedDivide(int64_t value, int32_t denominator) {
  return int32_t((value + (value < 0 ? -denominator / 2 : denominator / 2)) / denominator);
}

inline bool leapYear(unsigned year) {
  return year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
}

inline uint32_t clampUtc(int64_t value) {
  if (value <= 0) return 0;
  if (value >= int64_t(UINT32_MAX)) return UINT32_MAX;
  return uint32_t(value);
}

struct Events {
  int64_t sunrise;
  int64_t sunset;
  State polar;
  bool crosses;
};

inline Events events(int64_t unix_day, int32_t latitude_e6, int32_t longitude_e6) {
  // The caller only supplies dates close to a plausible uint32 RTC epoch.
  uint32_t day = uint32_t(unix_day - 10957); // days since 2000-01-01
  unsigned year = 2000;
  for (;;) {
    const unsigned length = 365 + unsigned(leapYear(year));
    if (day < length) break;
    day -= length;
    ++year;
  }
  const unsigned year_days = 365 + unsigned(leapYear(year));
  const int32_t gamma = int32_t((day * 360000UL + year_days / 2) / year_days);
  const int32_t s1 = sine(gamma), c1 = sine(gamma + 90000);
  const int32_t s2 = sine(gamma * 2), c2 = sine(gamma * 2 + 90000);
  const int32_t s3 = sine(gamma * 3), c3 = sine(gamma * 3 + 90000);

  // NOAA fractional-year coefficients, scaled to millidegrees and seconds.
  const int32_t declination = 396 + roundedDivide(
      -int64_t(22913) * c1 + int64_t(4025) * s1 - int64_t(387) * c2
      + int64_t(52) * s2 - int64_t(155) * c3 + int64_t(85) * s3, Scale);
  const int32_t equation_seconds = 1 + roundedDivide(
      int64_t(26) * c1 - int64_t(441) * s1 - int64_t(201) * c2
      - int64_t(562) * s2, Scale);
  const int32_t latitude = roundedDivide(latitude_e6, 1000);
  const int32_t sin_latitude = sine(latitude), cos_latitude = sine(latitude + 90000);
  const int32_t sin_declination = sine(declination), cos_declination = sine(declination + 90000);
  // Conventional sunrise is solar altitude -0.833 degrees (refraction/limb).
  const int64_t numerator = -int64_t(476) * Scale - int64_t(sin_latitude) * sin_declination;
  const int64_t denominator = int64_t(cos_latitude) * cos_declination;
  if (numerator <= -denominator) return {0, 0, State::Day, false};
  if (numerator >= denominator) return {0, 0, State::Night, false};

  int32_t low = 0, high = 180000;
  while (low < high) {
    const int32_t middle = (low + high) / 2;
    if (int64_t(sine(middle + 90000)) * denominator > numerator * Scale)
      low = middle + 1;
    else
      high = middle;
  }
  const int32_t half_day_seconds = roundedDivide(int64_t(low) * 240, 1000);
  const int32_t longitude_seconds = roundedDivide(int64_t(longitude_e6) * 240, 1000000);
  const int64_t noon = unix_day * 86400 + 43200 - longitude_seconds - equation_seconds;
  return {noon - half_day_seconds, noon + half_day_seconds, State::Unknown, true};
}

} // namespace daylight_detail

// The caller supplies the node's configured/cached location and can cache this
// result by UTC minute. No GPS reads, timezone, daylight-saving rules or floats.
inline Window evaluate(uint32_t utc, int32_t latitude_e6, int32_t longitude_e6) {
  using namespace daylight_detail;
  if (utc < PlausibleEpoch || latitude_e6 < -90000000 || latitude_e6 > 90000000
      || longitude_e6 < -180000000 || longitude_e6 > 180000000)
    return {State::Unknown, 0};

  const int32_t longitude_seconds = roundedDivide(int64_t(longitude_e6) * 240, 1000000);
  const int64_t solar_day = (int64_t(utc) + longitude_seconds) / 86400;
  Events days[3];
  days[1] = events(solar_day, latitude_e6, longitude_e6);
  if (!days[1].crosses)
    return {days[1].polar, clampUtc(int64_t(utc) + PolarRecheckSeconds)};
  days[0] = events(solar_day - 1, latitude_e6, longitude_e6);
  days[2] = events(solar_day + 1, latitude_e6, longitude_e6);

  int64_t daylight_end = 0;
  int64_t next_sunrise = -1;
  for (const Events& day : days) {
    if (!day.crosses) continue;
    if (int64_t(utc) >= day.sunrise && int64_t(utc) < day.sunset
        && day.sunset > daylight_end) daylight_end = day.sunset;
    if (day.sunrise > int64_t(utc) && (next_sunrise < 0 || day.sunrise < next_sunrise))
      next_sunrise = day.sunrise;
  }
  if (daylight_end) {
    // Adjacent very long days can overlap across apparent solar midnight.
    for (unsigned pass = 0; pass < 2; ++pass)
      for (const Events& day : days)
        if (day.crosses && day.sunrise <= daylight_end && day.sunset > daylight_end)
          daylight_end = day.sunset;
    return {State::Day, clampUtc(daylight_end)};
  }
  return {State::Night, clampUtc(next_sunrise < 0
      ? int64_t(utc) + PolarRecheckSeconds : next_sunrise)};
}

}} // namespace mesh::solar
