#pragma once
#include <stddef.h>
typedef void* QueueHandle_t;
QueueHandle_t xQueueCreate(unsigned count, size_t size);
int xQueueSend(QueueHandle_t queue, const void* item, int wait);
int xQueueReceive(QueueHandle_t queue, void* item, int wait);
void xQueueReset(QueueHandle_t queue);
void vQueueDelete(QueueHandle_t queue);
