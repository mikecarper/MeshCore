#pragma once

#include <stdint.h>

namespace mesh { namespace ui {

// Keep each configured radio profile visible long enough to read, while still
// fitting both into the normal short display wake interval.
constexpr uint32_t RADIO_PROFILE_DISPLAY_PAGE_MILLIS = 7000;

// The ordinary repeater home page needs two RF pages when radio2 is active.
// Compact panels use the following page(s) for the settings that do not fit
// beside F/B/S/C.  The caller supplies the number of status pages required by
// its measured font height and display height.
inline uint8_t radioProfileDisplayPageCount(bool dual_radio_enabled,
                                            uint8_t status_page_count) {
  return (dual_radio_enabled ? 2 : 1) + status_page_count;
}

inline uint8_t radioProfileDisplayPageIndex(bool dual_radio_enabled,
                                            uint8_t status_page_count,
                                            uint32_t elapsed_millis) {
  const uint8_t page_count = radioProfileDisplayPageCount(
      dual_radio_enabled, status_page_count);
  return page_count == 0 ? 0
      : (elapsed_millis / RADIO_PROFILE_DISPLAY_PAGE_MILLIS) % page_count;
}

// Repeater screens advance these pages with a physical button.  Keeping the
// manual helpers alongside the timed compatibility helpers makes it explicit
// that a button press, rather than a timeout, owns the selected profile.
inline uint8_t radioProfileManualPageIndex(bool dual_radio_enabled,
                                           uint8_t status_page_count,
                                           uint8_t selected_page) {
  const uint8_t page_count = radioProfileDisplayPageCount(
      dual_radio_enabled, status_page_count);
  return page_count == 0 ? 0 : selected_page % page_count;
}

inline bool showRadioProfileSystemStatusPage(bool dual_radio_enabled,
                                             uint8_t status_page_count,
                                             uint32_t elapsed_millis,
                                             uint8_t* status_page_index = nullptr) {
  const uint8_t radio_page_count = dual_radio_enabled ? 2 : 1;
  const uint8_t page_index = radioProfileDisplayPageIndex(
      dual_radio_enabled, status_page_count, elapsed_millis);
  const bool is_status_page = status_page_count != 0
      && page_index >= radio_page_count;
  if (status_page_index != nullptr) {
    *status_page_index = is_status_page ? page_index - radio_page_count : 0;
  }
  return is_status_page;
}

inline bool showManualRadioProfileSystemStatusPage(bool dual_radio_enabled,
                                                   uint8_t status_page_count,
                                                   uint8_t selected_page,
                                                   uint8_t* status_page_index = nullptr) {
  const uint8_t radio_page_count = dual_radio_enabled ? 2 : 1;
  const uint8_t page_index = radioProfileManualPageIndex(
      dual_radio_enabled, status_page_count, selected_page);
  const bool is_status_page = status_page_count != 0
      && page_index >= radio_page_count;
  if (status_page_index != nullptr) {
    *status_page_index = is_status_page ? page_index - radio_page_count : 0;
  }
  return is_status_page;
}

inline bool showSecondaryRadioProfilePage(bool dual_radio_enabled,
                                          uint8_t status_page_count,
                                          uint32_t elapsed_millis) {
  return dual_radio_enabled && radioProfileDisplayPageIndex(
      dual_radio_enabled, status_page_count, elapsed_millis) == 1;
}

inline bool showManualSecondaryRadioProfilePage(bool dual_radio_enabled,
                                                uint8_t selected_page) {
  return dual_radio_enabled && selected_page == 1;
}

inline bool showSecondaryRadioProfilePage(bool dual_radio_enabled,
                                          uint32_t elapsed_millis) {
  return dual_radio_enabled
      && ((elapsed_millis / RADIO_PROFILE_DISPLAY_PAGE_MILLIS) & 1U) != 0;
}

inline const char* radioProfileDisplayTag(bool secondary, bool temporary) {
  if (secondary) return temporary ? "T2" : "R2";
  return temporary ? "T1" : "R1";
}

} }  // namespace mesh::ui
