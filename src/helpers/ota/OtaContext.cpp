#include "OtaContext.h"
#include <assert.h>
#if OTA_DYNAMIC_CONTEXT && defined(OTA_HEAP_CONTEXT)
  #include <new>
#endif

namespace mesh {
namespace ota {

#if OTA_DYNAMIC_CONTEXT
namespace {
OtaContext* active_context = nullptr;
void* storage_owner = nullptr;
OtaContext* (*acquire_storage)(void*) = nullptr;
void (*release_storage)(void*) = nullptr;
OtaSend saved_send = nullptr;
void* saved_send_ctx = nullptr;
uint32_t saved_target = 0;
char saved_hw[33] = {0};
uint8_t saved_seeder_id[4] = {0};
// Keep user policy across workspace reuse; discovered catalogs and transfers
// belong to the temporary mOTA session. No allocation is needed while idle.
SignerAllowlist saved_allow;
uint8_t saved_autofetch = OtaManager::AUTOFETCH_OFF;
uint8_t saved_autoinstall = OtaContext::AUTOINSTALL_OFF;
uint8_t saved_hops = OTA_HOP_LIMIT_DEFAULT;
uint16_t saved_checkpoint = OTA_CHECKPOINT_BLOCKS;
uint16_t saved_advert = OTA_ADVERT_INTERVAL_MINS;

#if defined(OTA_HEAP_CONTEXT)
// Default storage for roles with no borrowable workspace (repeaters, room
// servers). The context is several kilobytes; keeping it off .bss matters most
// on classic ESP32, whose static DRAM window is far smaller than its heap.
OtaContext* heap_context = nullptr;

OtaContext* acquireHeapContext(void*) {
  if (!heap_context) heap_context = new (std::nothrow) OtaContext();
  return heap_context;   // nullptr on exhaustion; the caller reports and bails
}

void releaseHeapContext(void*) {
  delete heap_context;
  heap_context = nullptr;
}
#endif
}

void ota_set_context_storage(void* owner, OtaContext* (*acquire)(void*),
                             void (*release)(void*)) {
  assert(!active_context);
  storage_owner = owner;
  acquire_storage = acquire;
  release_storage = release;
}

OtaContext& ota_ctx() {
  assert(active_context);  // Only explicit OTA entry points acquire storage.
  return *active_context;
}

OtaContext* ota_context_if_active() { return active_context; }

void ota_begin_context(uint32_t target, OtaSend send, void* ctx,
                       const char* hw, const uint8_t* seeder_id) {
  saved_target = target;
  saved_send = send;
  saved_send_ctx = ctx;
  strncpy(saved_hw, hw ? hw : "", sizeof(saved_hw) - 1);
  saved_hw[sizeof(saved_hw) - 1] = 0;
  if (seeder_id) memcpy(saved_seeder_id, seeder_id, sizeof(saved_seeder_id));
}

bool ota_acquire_context(char* reply, size_t cap) {
  if (active_context) return true;
#if defined(OTA_HEAP_CONTEXT)
  if (!acquire_storage) {   // no owner registered one: fall back to the heap
    acquire_storage = acquireHeapContext;
    release_storage = releaseHeapContext;
  }
#endif
  if (!acquire_storage || !release_storage || !saved_send) {
    if (reply && cap) snprintf(reply, cap, "ERR mOTA storage is not ready");
    return false;
  }
  active_context = acquire_storage(storage_owner);
  if (!active_context) {
    if (reply && cap) snprintf(reply, cap,
#if defined(OTA_HEAP_CONTEXT)
        "ERR mOTA is out of memory; retry when the node is less busy");
#else
        "ERR mOTA needs 128 free queue slots; sync unread messages with an app first");
#endif
    return false;
  }
  OtaContext& c = *active_context;
  c.begin(saved_target, saved_send, saved_send_ctx, saved_hw);
  c.manager.set_seeder_id(saved_seeder_id);
  c.manager.set_autofetch(saved_autofetch);
  c.manager.set_checkpoint_blocks(saved_checkpoint);
  c.manager.set_advert_mins(saved_advert);
  c.manager.set_max_hops(saved_hops);
  c.autoinstall = saved_autoinstall;
  c.allow = saved_allow;
  return true;
}

uint8_t ota_hop_limit() {
  return active_context ? active_context->manager.max_hops() : saved_hops;
}

void ota_release_context_if_idle(bool temporary_radio_active) {
  if (!active_context) return;
  OtaContext& c = *active_context;
  if (c.folder_active || c.folder_dest || c.apply_pending) return;
#if defined(OTA_HEAP_CONTEXT)
  // Self-serving ends with the temporary radio window. It must not keep the
  // heap workspace forever after the first announcement. Manual staging is
  // different: preserve its bytes across separate CLI commands until reset.
  if (c.serve_expected != 0) return;
#else
  if (c.serving) return;
#endif
  if (temporary_radio_active && !c.release_when_idle) return;
  // No host source/destination remains. Discard pending transfer work before
  // the queue reuses these bytes, including dynamic discovery/diff buffers.
  saved_autofetch = c.manager.autofetch();
  saved_checkpoint = c.manager.checkpoint_blocks();
  saved_advert = c.manager.advert_mins();
  saved_hops = c.manager.max_hops();
  saved_autoinstall = c.autoinstall;
  saved_allow = c.allow;
  c.manager.clearPendingEgress();
  c.manager.reset_session();
  active_context = nullptr;
  release_storage(storage_owner);
}
#else
OtaContext& ota_ctx() {
  static OtaContext ctx;
  return ctx;
}

OtaContext* ota_context_if_active() { return &ota_ctx(); }
bool ota_acquire_context(char*, size_t) { return true; }
void ota_begin_context(uint32_t target, OtaSend send, void* ctx,
                       const char* hw, const uint8_t* seeder_id) {
  ota_ctx().begin(target, send, ctx, hw);
  ota_ctx().manager.set_seeder_id(seeder_id);
}
uint8_t ota_hop_limit() { return ota_ctx().manager.max_hops(); }
#endif

} // namespace ota
} // namespace mesh
