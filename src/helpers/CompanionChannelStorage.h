#pragma once

#include "ChannelDetails.h"

#ifndef COMPANION_CHANNELS_RTC_STORAGE
#define COMPANION_CHANNELS_RTC_STORAGE 0
#endif
#if COMPANION_CHANNELS_RTC_STORAGE != 0 && COMPANION_CHANNELS_RTC_STORAGE != 1
#error "COMPANION_CHANNELS_RTC_STORAGE must be 0 or 1"
#endif
#if COMPANION_CHANNELS_RTC_STORAGE
#if !defined(ESP32_PLATFORM) || !defined(TBEAM_SX1262) || !defined(MESH_RADIO_AUTO_FAMILY) || !MESH_RADIO_AUTO_FAMILY
#error "RTC channels require the universal ESP32 T-Beam"
#endif
#include <sdkconfig.h>
#include <type_traits>
#if !defined(CONFIG_IDF_TARGET_ESP32) || !CONFIG_IDF_TARGET_ESP32
#error "RTC channels require the classic ESP32"
#endif
#if !defined(MAX_GROUP_CHANNELS) || MAX_GROUP_CHANNELS < 1
#error "RTC channels require a channel table"
#endif
static_assert(std::is_trivially_copyable<ChannelDetails>::value,
              "RTC channels must be plain data");
static_assert(std::is_trivially_destructible<ChannelDetails>::value,
              "RTC channels must have no resources");
namespace mesh { namespace companion {
using ChannelArray = ChannelDetails[MAX_GROUP_CHANNELS];
ChannelArray& rtcChannels();
} }
#endif
