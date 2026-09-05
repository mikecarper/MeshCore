#pragma once

#include <atomic>

#define __DMB() std::atomic_signal_fence(std::memory_order_seq_cst)
#define __DSB() std::atomic_signal_fence(std::memory_order_seq_cst)
