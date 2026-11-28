#pragma once

#include <cstdio>
#include <esp_bt.h>
#include <esp_now.h>
#include <esp_wifi.h>

namespace v4rf24 {

void shutDown();
void printStatus(char* reply, size_t size);

}  // namespace v4rf24
