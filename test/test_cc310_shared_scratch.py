"""Run the production CC310 session and callers with typed SDK/RTOS fakes.

The fake algorithms test storage, serialization and existing fallback behavior;
they are not a substitute for CryptoCell hardware known-vector qualification.
"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]

ARDUINO = r'''
#pragma once
#include <atomic>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <mutex>
struct StaticSemaphore_t { std::mutex mutex; };
using SemaphoreHandle_t = StaticSemaphore_t*;
extern thread_local bool mock_isr;
extern std::mutex creation_mutex;
extern std::atomic<unsigned> creates, takes, gives, lock_depth;
inline bool isInISR() { return mock_isr; }
inline SemaphoreHandle_t xSemaphoreCreateMutexStatic(StaticSemaphore_t* value) {
  ++creates; return value;
}
inline int xSemaphoreTake(SemaphoreHandle_t value, unsigned) {
  value->mutex.lock(); ++takes; assert(++lock_depth == 1); return 1;
}
inline void xSemaphoreGive(SemaphoreHandle_t value) {
  assert(--lock_depth == 0); ++gives; value->mutex.unlock();
}
#define pdTRUE 1
#define portMAX_DELAY 0xffffffffU
#define taskENTER_CRITICAL() creation_mutex.lock()
#define taskEXIT_CRITICAL() creation_mutex.unlock()
'''

SDK = r'''
#pragma once
#include <cstddef>
#include <cstdint>
struct CRYS_ECEDW_TempBuff_t { uint32_t buff[140]; };
struct SaSiAesUserContext_t { uint32_t buff[19]; };
using CRYS_HASH_Result_t = uint32_t[16];
using CRYSError_t = int;
using SaSiError_t = int;
struct SaSiAesUserKeyData_t { uint8_t* key; unsigned size; };
constexpr int CRYS_OK=0, SASI_OK=0, CRYS_ECEDW_SIGN_VERIFY_FAILED_ERROR=2;
constexpr unsigned CRYS_ECEDW_SIGNATURE_BYTES=64, CRYS_ECEDW_MOD_SIZE_IN_BYTES=32;
constexpr int SASI_AES_ENCRYPT=1, SASI_AES_DECRYPT=0, SASI_AES_MODE_ECB=0;
constexpr int SASI_AES_PADDING_NONE=0, SASI_AES_USER_KEY=0, CRYS_HASH_SHA256_mode=0;
'''

CRYPTO = r'''
#pragma once
#include <cstdint>
struct MockRandom { bool generate(uint8_t*, uint16_t); };
struct MockCrypto { bool begin(); MockRandom Random; };
extern MockCrypto nRFCrypto;
'''

HARNESS = r'''
#include <Arduino.h>
#include <Adafruit_nRFCrypto.h>
#include <helpers/NRF52CryptoScratch.h>
#include <array>
#include <cstring>
#include <string>
#include <thread>
#include <vector>
thread_local bool mock_isr=false;
std::mutex creation_mutex;
std::atomic<unsigned> creates{0}, takes{0}, gives{0}, lock_depth{0};
std::atomic<unsigned> begins{0}, sdk_calls{0}, software_verifies{0};
bool begin_ok=true, random_ok=true;
int verify_rc=CRYS_OK, aes_rc=SASI_OK, hmac_rc=CRYS_OK;
MockCrypto nRFCrypto;
bool MockCrypto::begin() { ++begins; return begin_ok; }
bool MockRandom::generate(uint8_t* out, uint16_t size) {
  if (!random_ok) return false;
  memset(out, 0x5a, size); return true;
}
template<typename T> bool zero(const T& value) {
  const auto* bytes=reinterpret_cast<const unsigned char*>(&value);
  for(size_t i=0;i<sizeof(T);i++) if(bytes[i]) return false;
  return true;
}
constexpr int CIPHER_KEY_SIZE=16, CIPHER_BLOCK_SIZE=16, PUB_KEY_SIZE=32;
constexpr int CIPHER_MAC_SIZE=2;
int SaSi_AesInit(SaSiAesUserContext_t* ctx, int, int, int) {
  assert(lock_depth==1 && zero(*ctx)); ++sdk_calls;
  memset(ctx, 0x4a, sizeof(*ctx)); return aes_rc;
}
int SaSi_AesSetKey(SaSiAesUserContext_t* ctx, int, SaSiAesUserKeyData_t* key, size_t) {
  ctx->buff[0]=key->key[0]; return SASI_OK;
}
int SaSi_AesBlock(SaSiAesUserContext_t* ctx, uint8_t* src, unsigned size, uint8_t* dest) {
  for(unsigned i=0;i<size;i++) dest[i]=src[i]^uint8_t(ctx->buff[0]);
  std::this_thread::yield(); return SASI_OK;
}
int SaSi_AesFinish(SaSiAesUserContext_t*, int, void*, int, void*, size_t*) { return SASI_OK; }
int SaSi_AesFree(SaSiAesUserContext_t* ctx) { memset(ctx,0,sizeof(*ctx)); return SASI_OK; }
int CRYS_HMAC(int, uint8_t*, unsigned, uint8_t*, size_t, uint32_t* result) {
  assert(lock_depth==1);
  for(unsigned i=0;i<16;i++) assert(result[i]==0);
  ++sdk_calls;
  if(hmac_rc) return hmac_rc;
  memset(result,0xa5,sizeof(CRYS_HASH_Result_t)); return CRYS_OK;
}
int CRYS_ECEDW_Verify(uint8_t*, unsigned, uint8_t*, unsigned, uint8_t*, size_t,
                      CRYS_ECEDW_TempBuff_t* scratch) {
  assert(lock_depth==1 && zero(*scratch)); ++sdk_calls;
  memset(scratch,0xc3,sizeof(*scratch)); return verify_rc;
}
struct Ed25519 {
  static bool verify(const uint8_t*, const uint8_t*, const uint8_t*, int) {
    ++software_verifies; return true;
  }
};
namespace mesh { struct Identity {
  uint8_t pub_key[32]{};
  bool verify(const uint8_t*,const uint8_t*,int) const;
}; }
@CALLERS@

static void no_scratch(mesh::CC310CryptoSession& session) {
  assert(!session && !mesh::cc310VerificationScratch(session) &&
         !mesh::cc310AesScratch(session) && !mesh::cc310HmacScratch(session));
}
static void operation_round() {
  std::array<uint8_t,32> key{}; key.fill(0x17);
  std::array<uint8_t,32> plain{}, encrypted{}, decoded{};
  for(unsigned i=0;i<plain.size();i++) plain[i]=uint8_t(i);
  assert(aesHardware(true,key.data(),encrypted.data(),plain.data(),plain.size())==32);
  assert(aesHardware(false,key.data(),decoded.data(),encrypted.data(),encrypted.size())==32);
  assert(decoded==plain);
  std::array<uint8_t,2> mac{};
  assert(hmacHardware(key.data(),mac.data(),plain.data(),plain.size()));
  assert(mac[0]==0xa5 && mac[1]==0xa5);
  mesh::Identity identity;
  std::array<uint8_t,64> signature{};
  assert(identity.verify(signature.data(),plain.data(),plain.size()));
}
int main(int argc,char**argv) {
  assert(argc==2);
  const std::string scenario=argv[1];
  if(scenario=="alternating") {
    static_assert(sizeof(CRYS_ECEDW_TempBuff_t)==560);
    static_assert(sizeof(SaSiAesUserContext_t)==76);
    static_assert(sizeof(CRYS_HASH_Result_t)==64);
    for(unsigned n=0;n<1000;n++) {
      mesh::CC310CryptoSession session;
      assert(session && lock_depth==1);
      auto* v=mesh::cc310VerificationScratch(session); assert(zero(*v));
      assert(reinterpret_cast<uintptr_t>(v)%alignof(CRYS_ECEDW_TempBuff_t)==0);
      memset(v,0xff,sizeof(*v));
      auto* a=mesh::cc310AesScratch(session); assert(zero(*a));
      assert(reinterpret_cast<void*>(a)==reinterpret_cast<void*>(v));
      assert(reinterpret_cast<uintptr_t>(a)%alignof(SaSiAesUserContext_t)==0);
      memset(a,0xff,sizeof(*a));
      auto* h=mesh::cc310HmacScratch(session); assert(zero(*h));
      assert(reinterpret_cast<void*>(h)==reinterpret_cast<void*>(v));
      assert(reinterpret_cast<uintptr_t>(h)%alignof(CRYS_HASH_Result_t)==0);
      memset(h,0xff,sizeof(*h));
    }
    assert(begins==1 && creates==1 && takes==gives);
  } else if(scenario=="callers") {
    for(unsigned n=0;n<1000;n++) operation_round();
    assert(begins==1 && software_verifies==0 && takes==gives && sdk_calls==4000);
    std::array<uint8_t,64> signature{}; std::array<uint8_t,32> message{};
    mesh::Identity identity;
    verify_rc=CRYS_ECEDW_SIGN_VERIFY_FAILED_ERROR;
    assert(!identity.verify(signature.data(),message.data(),message.size()) && software_verifies==0);
    verify_rc=7;
    assert(identity.verify(signature.data(),message.data(),message.size()) && software_verifies==1);
    std::array<uint8_t,32> key{}, destination{}; destination.fill(0x79);
    aes_rc=7; hmac_rc=7;
    assert(aesHardware(true,key.data(),destination.data(),message.data(),32)==-1);
    for(auto byte:destination) assert(byte==0x79);
    assert(!hmacHardware(key.data(),destination.data(),message.data(),32));
    for(auto byte:destination) assert(byte==0x79);
  } else if(scenario=="threads") {
    assert(mesh::initializeCC310Crypto());
    std::vector<std::thread> workers;
    for(unsigned n=0;n<4;n++) workers.emplace_back([]{for(unsigned i=0;i<250;i++) operation_round();});
    for(auto& worker:workers) worker.join();
    assert(lock_depth==0 && takes==gives && begins==1 && creates==1);
  } else if(scenario=="isr" || scenario=="init-failure") {
    mock_isr=scenario=="isr"; begin_ok=scenario!="init-failure";
    { mesh::CC310CryptoSession session; no_scratch(session); }
    std::array<uint8_t,32> key{}, input{}, output{}; output.fill(0x68);
    assert(aesHardware(true,key.data(),output.data(),input.data(),32)==-1);
    assert(!hmacHardware(key.data(),output.data(),input.data(),32));
    mesh::mixCC310Random(output.data(),output.size());
    for(auto byte:output) assert(byte==0x68);
    mesh::Identity identity; std::array<uint8_t,64> signature{};
    assert(identity.verify(signature.data(),input.data(),input.size()));
    assert(software_verifies==1 && sdk_calls==0 && takes==gives);
    if(mock_isr) assert(creates==0 && begins==0 && takes==0);
    else assert(creates==1 && begins==1);
  } else if(scenario=="random") {
    std::array<uint8_t,79> data{}; data.fill(0xa5);
    mesh::mixCC310Random(data.data(),data.size());
    for(auto byte:data) assert(byte==0xff);
    random_ok=false;
    mesh::mixCC310Random(data.data(),data.size());
    for(auto byte:data) assert(byte==0xff);
    assert(begins==1 && takes==gives);
  } else assert(false);
}
'''


class CC310SharedScratchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise RuntimeError("A native C++17 compiler is required")
        cls.temporary = tempfile.TemporaryDirectory(prefix="cc310-shared-scratch-")
        cls.addClassCleanup(cls.temporary.cleanup)
        work = Path(cls.temporary.name)
        (work / "nrf_cc310/include").mkdir(parents=True)
        (work / "Arduino.h").write_text(ARDUINO)
        (work / "Adafruit_nRFCrypto.h").write_text(CRYPTO)
        (work / "sdk_types.h").write_text(SDK)
        for name in ("crys_ec_edw_api.h", "crys_hash.h", "ssi_aes.h"):
            (work / "nrf_cc310/include" / name).write_text('#include "sdk_types.h"\n')
        utils = (ROOT / "src/Utils.cpp").read_text()
        identity = (ROOT / "src/Identity.cpp").read_text()
        callers = "\n".join((body(utils, "int aesHardware("), body(utils, "bool hmacHardware("),
                              body(identity, "bool Identity::verify(")))
        callers = callers.replace("bool Identity::verify(", "bool mesh::Identity::verify(")
        source = work / "main.cpp"
        source.write_text(HARNESS.replace("@CALLERS@", callers))
        cls.binary = work / "check"
        flags = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                 if sys.platform.startswith("linux") else [])
        result = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-pthread",
                                 *flags, "-DUSE_CC310_HW_CRYPTO=1", "-I" + str(work),
                                 "-I" + str(ROOT / "src"), str(source),
                                 str(ROOT / "src/helpers/NRF52Crypto.cpp"), "-o", str(cls.binary)],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def run_case(self, scenario):
        result = subprocess.run([str(self.binary), scenario], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_alternating_types_have_shared_aligned_zero_initialized_storage(self):
        self.run_case("alternating")

    def test_production_callers_repeat_roundtrips_and_preserve_error_fallbacks(self):
        self.run_case("callers")

    def test_sessions_serialize_shared_scratch_between_threads(self):
        self.run_case("threads")

    def test_isr_rejects_workspaces_and_hardware_without_mutating_output(self):
        self.run_case("isr")

    def test_cached_initialization_failure_preserves_output_and_software_verify(self):
        self.run_case("init-failure")

    def test_entropy_mixing_and_failed_random_output_are_unchanged(self):
        self.run_case("random")


if __name__ == "__main__":
    unittest.main()
