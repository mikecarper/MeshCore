#pragma once
#include <stddef.h>
#include <helpers/WirelessControl.h>

enum class CompanionWirelessSource { Other, Usb, Network, Framed };
bool handleCompanionWirelessCommand(const char* command, char* reply, size_t size,
                                   CompanionWirelessSource source = CompanionWirelessSource::Other);
