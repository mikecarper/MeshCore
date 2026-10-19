#!/usr/bin/env python3
"""Execute production contact indexing at compact/wide and PSRAM boundaries."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <algorithm>
#include <memory>
#include <new>
#include <set>
#include <type_traits>
#include <utility>
#include <vector>
#include <helpers/ContactListOrder.h>
#define MESH_DEBUG_PRINTLN(...) ((void)0)
@INDEX_TYPES@
struct ContactInfo {
  struct Identity {
    uint8_t prefix = 0;
    uint32_t ordinal = 0;
    bool isHashMatch(const uint8_t* hash) const { return prefix == *hash; }
  } id;
  uint8_t flags = 0;
  uint32_t last_advert_timestamp = 0;
};
@VISITOR@
static unsigned allocations = 0, fail_allocation = 0;
static std::set<void*> outstanding;
static std::vector<std::pair<size_t, size_t>> allocation_sizes;
static constexpr int MALLOC_CAP_SPIRAM = 1, MALLOC_CAP_8BIT = 2;
static void* heap_caps_calloc(size_t count, size_t size, int) {
  allocation_sizes.push_back({count, size});
  if (++allocations == fail_allocation) return nullptr;
  void* pointer = calloc(count, size);
  assert(pointer != nullptr);
  outstanding.insert(pointer);
  return pointer;
}
static void heap_caps_free(void* pointer) {
  assert(outstanding.erase(pointer) == 1);
  free(pointer);
}
class BaseChatMesh {
public:
@STORAGE@
  unsigned relocations = 0;
  BaseChatMesh() {
#if defined(ESP32_PLATFORM) && defined(BOARD_HAS_PSRAM)
    contacts = contacts_fallback;
    sort_array = sort_array_fallback;
    contact_capacity = CONTACT_PSRAM_FALLBACK_TOTAL_SLOTS;
#endif
  }
  ~BaseChatMesh() {
#if defined(ESP32_PLATFORM) && defined(BOARD_HAS_PSRAM)
    if (contacts != contacts_fallback) {
      heap_caps_free(contacts);
      heap_caps_free(sort_array);
    }
#endif
  }
  static void resetContactValue(ContactInfo& contact) { contact = ContactInfo(); }
  void onContactReferenceChanged(const ContactInfo*, ContactInfo*) { ++relocations; }
  bool initializeContactStorage();
  int searchPeersByHash(const uint8_t* hash);
  void scanRecentContacts(int last_n, ContactVisitor* visitor);
};
@METHODS@
struct Visitor : ContactVisitor {
  std::vector<ContactInfo> seen;
  void onContactVisit(const ContactInfo& contact) override { seen.push_back(contact); }
};
static void checkSort(BaseChatMesh& mesh, int requested, size_t expected) {
  Visitor visitor;
  mesh.scanRecentContacts(requested, &visitor);
  assert(visitor.seen.size() == expected);
  std::set<uint32_t> ordinals;
  for (size_t i = 0; i < visitor.seen.size(); ++i) {
    const auto& contact = visitor.seen[i];
    assert(contact.id.ordinal < static_cast<uint32_t>(mesh.num_contacts));
    assert(ordinals.insert(contact.id.ordinal).second);
    if (i > 0) {
      const auto& previous = visitor.seen[i - 1];
      const bool previous_favorite = (previous.flags & 1) != 0;
      const bool favorite = (contact.flags & 1) != 0;
      assert(previous_favorite || !favorite);
      if (previous_favorite == favorite)
        assert(previous.last_advert_timestamp >= contact.last_advert_timestamp);
    }
  }
}
static void checkFullTable() {
  auto mesh = std::make_unique<BaseChatMesh>();
  assert(mesh->initializeContactStorage());
  mesh->num_contacts = MAX_CONTACTS + MAX_ANON_CONTACTS;
  for (int i = 0; i < mesh->num_contacts; ++i) {
    mesh->contacts[i].id.ordinal = i;
    mesh->contacts[i].flags = (i % 7 == 0 ? 1 : 0) | 0x20;
    mesh->contacts[i].last_advert_timestamp = (static_cast<uint32_t>(i) * 7919) % 997;
  }
  checkSort(*mesh, 0, mesh->num_contacts);
  checkSort(*mesh, 11, 11);
  checkSort(*mesh, mesh->num_contacts + 1, mesh->num_contacts);

  // Twenty late persistent matches precede the eight transient matches. The
  // 24-result limit must preserve the highest index and then four transients.
  const uint8_t hash = 0x72;
  for (int i = 0; i < MAX_ANON_CONTACTS; ++i) mesh->contacts[i].id.prefix = hash;
  for (int i = mesh->num_contacts - 20; i < mesh->num_contacts; ++i)
    mesh->contacts[i].id.prefix = hash;
  assert(mesh->searchPeersByHash(&hash) == MAX_SEARCH_RESULTS);
  for (int i = 0; i < 20; ++i)
    assert(mesh->matching_peer_indexes[i] == mesh->num_contacts - 20 + i);
  for (int i = 20; i < MAX_SEARCH_RESULTS; ++i)
    assert(mesh->matching_peer_indexes[i] == i - 20);
  for (int i = mesh->num_contacts - 24; i < mesh->num_contacts - 20; ++i)
    mesh->contacts[i].id.prefix = hash;
  assert(mesh->searchPeersByHash(&hash) == MAX_SEARCH_RESULTS);
  for (int i = 0; i < MAX_SEARCH_RESULTS; ++i)
    assert(mesh->matching_peer_indexes[i] == mesh->num_contacts - 24 + i);
  const uint8_t missing = 0x91;
  assert(mesh->searchPeersByHash(&missing) == 0);
}
#if defined(ESP32_PLATFORM) && defined(BOARD_HAS_PSRAM)
static void checkPsram() {
  for (unsigned fail : {1U, 2U, 0U}) {
    allocations = 0;
    fail_allocation = fail;
    allocation_sizes.clear();
    {
      BaseChatMesh mesh;
      mesh.num_contacts = MAX_ANON_CONTACTS + 16;
      for (int i = 0; i < mesh.num_contacts; ++i) mesh.contacts[i].id.ordinal = i + 100;
      assert(mesh.initializeContactStorage() == (fail == 0));
      assert(allocations == 2);
      assert(allocation_sizes[1].first == MAX_CONTACTS + MAX_ANON_CONTACTS);
      assert(allocation_sizes[1].second == sizeof(ContactIndex));
      assert(mesh.contact_capacity == (fail ? MAX_ANON_CONTACTS + 16
                                            : MAX_CONTACTS + MAX_ANON_CONTACTS));
      assert((mesh.contacts == mesh.contacts_fallback) == (fail != 0));
      assert((mesh.sort_array == mesh.sort_array_fallback) == (fail != 0));
      for (int i = 0; i < mesh.num_contacts; ++i) assert(mesh.contacts[i].id.ordinal == i + 100);
      if (!fail) {
        const unsigned before = allocations;
        assert(mesh.initializeContactStorage());
        assert(allocations == before);
      }
    }
    assert(outstanding.empty());
  }
  fail_allocation = 0;
}
#endif
int main() {
#if MAX_CONTACTS + MAX_ANON_CONTACTS <= 65535
  static_assert(std::is_same<ContactIndex, uint16_t>::value, "compact contact indices");
#else
  static_assert(std::is_same<ContactIndex, int>::value, "retain wide contact indices");
#endif
  static_assert(sizeof(((BaseChatMesh*)nullptr)->matching_peer_indexes)
                == MAX_SEARCH_RESULTS * sizeof(ContactIndex), "matching index storage");
#if defined(ESP32_PLATFORM) && defined(BOARD_HAS_PSRAM)
  static_assert(std::is_same<decltype(BaseChatMesh::sort_array), ContactIndex*>::value,
                "PSRAM sort allocation type");
  static_assert(sizeof(((BaseChatMesh*)nullptr)->sort_array_fallback)
                == (MAX_ANON_CONTACTS + 16) * sizeof(ContactIndex), "fallback index storage");
  checkPsram();
#else
  static_assert(sizeof(((BaseChatMesh*)nullptr)->sort_array)
                == (MAX_CONTACTS + MAX_ANON_CONTACTS) * sizeof(ContactIndex), "sort index storage");
#endif
  checkFullTable();
  assert(outstanding.empty());
  puts("PASS: contact sorting, peer collisions, widths and allocation ownership");
}
'''


class ContactIndexTests(unittest.TestCase):
    def test_contact_layout_on_all_platforms(self):
        # Compile the real record rather than the indexing harness's small
        # stand-in. Only cached nRF52 records have the extra storage-slot field
        # whose placement can cost four bytes per contact.
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++17 compiler is required")
        for platform in ("NRF52_PLATFORM", "ESP32_PLATFORM", "RP2040_PLATFORM", "STM32_PLATFORM"):
            for cache in (0, 1):
                expected = 92 if cache else (188 if platform == "NRF52_PLATFORM" else 184)
                with self.subTest(platform=platform, cache=cache):
                    source = ("#include <helpers/ContactInfo.h>\n"
                              f'static_assert(sizeof(ContactInfo) == {expected}, "contact RAM budget");\n')
                    compiled = subprocess.run([
                        compiler, "-std=c++17", "-fsyntax-only", "-x", "c++", "-",
                        f"-D{platform}=1", f"-DMESH_CONTACT_CACHE={cache}",
                        f"-I{ROOT / 'test/fixtures/contact_cache/mocks'}",
                        f"-I{ROOT / 'lib/ed25519'}", f"-I{ROOT / 'src'}",
                    ], input=source, capture_output=True, text=True, timeout=30)
                    self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)

    def test_production_indices(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++17 compiler is required")
        header = (ROOT / "src/helpers/BaseChatMesh.h").read_text()
        source = (ROOT / "src/helpers/BaseChatMesh.cpp").read_text()
        index_types = "\n".join(re.findall(
            r"^#define MAX_(?:ANON_CONTACTS|SEARCH_RESULTS)\s+.+$", header, re.MULTILINE))
        index_types += "\n" + header[header.index("#if MAX_CONTACTS + MAX_ANON_CONTACTS <= 65535"):
                                     header.index("#ifndef MAX_CONNECTIONS")]
        storage_start = header.index("#if defined(ESP32_PLATFORM)",
                                     header.index("friend class ContactsIterator;"))
        storage = header[storage_start:header.index("  unsigned long txt_send_timeout;", storage_start)]
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "bool BaseChatMesh::initializeContactStorage(",
            "int BaseChatMesh::searchPeersByHash(",
            "static int cmp_contact_list_order(",
            "void BaseChatMesh::scanRecentContacts("))
        methods = "static ContactInfo* table;\n" + methods
        harness = (HARNESS.replace("@INDEX_TYPES@", index_types)
                   .replace("@VISITOR@", extract_braced(header, "class ContactVisitor") + ";")
                   .replace("@STORAGE@", storage).replace("@METHODS@", methods))
        variants = ((350, None, False, False),
                    (350, "NRF52_PLATFORM", False, True),
                    (350, "ESP32_PLATFORM", False, True),
                    (350, "ESP32_PLATFORM", True, False),
                    (350, "ESP32_PLATFORM", True, True),
                    (350, "RP2040_PLATFORM", False, False),
                    (32, "STM32_PLATFORM", False, False),
                    (65527, None, False, False), (65528, None, False, False),
                    (65529, None, False, False), (65529, "ESP32_PLATFORM", True, True))
        with tempfile.TemporaryDirectory(prefix="meshcore-contact-indices-") as directory:
            work = Path(directory)
            test_source = work / "test.cpp"
            test_source.write_text(harness, encoding="ascii")
            for contacts, platform, psram, cache in variants:
                with self.subTest(contacts=contacts, platform=platform, psram=psram, cache=cache):
                    binary = work / f"indices-{contacts}-{platform}-{psram}-{cache}"
                    command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                               "-Wno-unused-function", "-Wno-sign-compare",
                               f"-DMAX_CONTACTS={contacts}", f"-DMESH_CONTACT_CACHE={int(cache)}",
                               f"-I{ROOT / 'src'}", str(test_source), "-o", str(binary)]
                    if platform:
                        command.append(f"-D{platform}=1")
                    if psram:
                        command.append("-DBOARD_HAS_PSRAM=1")
                    if sys.platform.startswith("linux"):
                        command += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                    "-fno-pie", "-no-pie"]
                    built = subprocess.run(command, capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("PASS: contact sorting", checked.stdout)


if __name__ == "__main__":
    unittest.main()
