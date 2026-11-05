#pragma once
typedef void* TaskHandle_t;
int xTaskCreatePinnedToCore(void (*task)(void*), const char*, int, void*, int, void*, int);
unsigned ulTaskNotifyTake(int, unsigned);
void xTaskNotifyGive(TaskHandle_t);
void vTaskDelete(void*);
void vTaskDelay(int);
