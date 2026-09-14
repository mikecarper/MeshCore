#pragma once
#include <stdarg.h>

struct BenchResult {
  uint32_t sequence = 0;
  char text[2048] = {};
};
BenchResult lastListenResult, lastTxResult, lastRxResult, lastRunResult;

void appendBenchResult(BenchResult& result, const char* format, ...) {
  const size_t used=strlen(result.text);
  va_list args;
  va_start(args,format);
  const int n=vsnprintf(result.text+used,sizeof(result.text)-used,format,args);
  va_end(args);
  if(n<0 || size_t(n)>=sizeof(result.text)-used) result.sequence=0;
}

void emitBenchResult(const BenchResult& result) {
  Serial.print(result.text);
  Serial.flush();
}

void recordBenchResult(BenchResult& result, uint32_t sequence, const char* format, ...) {
  va_list args;
  va_start(args, format);
  const int n = vsnprintf(result.text, sizeof(result.text), format, args);
  va_end(args);
  if (n < 0 || size_t(n) >= sizeof(result.text)) {
    result.sequence = 0;
    Serial.println("{\"error\":\"result overflow\"}");
    return;
  }
  result.sequence = sequence;  // publish before USB: retrieval cannot repeat RF
  emitBenchResult(result);
}

void replayBenchResult(const char* kind, uint32_t sequence) {
  const BenchResult* result = !strcmp(kind,"sent") ? &lastTxResult
      : !strcmp(kind,"received") ? &lastRxResult
      : !strcmp(kind,"listening") ? &lastListenResult
      : !strcmp(kind,"result") ? &lastRunResult : nullptr;
  if (!result || !sequence || result->sequence != sequence) {
    Serial.println("{\"error\":\"result unavailable\"}");
    return;
  }
  emitBenchResult(*result);
}
