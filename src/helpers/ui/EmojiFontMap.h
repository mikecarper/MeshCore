#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {
namespace ui {

class EmojiFontMap {
  static constexpr size_t MAP_HEADER_SIZE = 12;
  static constexpr size_t NODE_SIZE = 6;
  static constexpr size_t EDGE_SIZE = 3;
  static constexpr size_t FOOTER_V1_SIZE = 24;
  static constexpr size_t FOOTER_V2_SIZE = 40;

  const uint8_t* _nodes = nullptr;
  const uint8_t* _edges = nullptr;
  uint16_t _node_count = 0;
  uint16_t _edge_count = 0;

  static uint16_t read16(const uint8_t* p) {
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
  }

  static uint32_t read32(const uint8_t* p) {
    return (uint32_t)p[0]
        | ((uint32_t)p[1] << 8)
        | ((uint32_t)p[2] << 16)
        | ((uint32_t)p[3] << 24);
  }

  static uint32_t crc32(const uint8_t* data, size_t size) {
    uint32_t crc = 0xFFFFFFFFUL;
    for (size_t i = 0; i < size; ++i) {
      crc ^= data[i];
      for (uint8_t bit = 0; bit < 8; ++bit) {
        crc = (crc >> 1) ^ (0xEDB88320UL & (0U - (crc & 1U)));
      }
    }
    return ~crc;
  }

  const uint8_t* node(uint16_t index) const {
    return _nodes + (size_t)index * NODE_SIZE;
  }

  const uint8_t* edge(uint16_t index) const {
    return _edges + (size_t)index * EDGE_SIZE;
  }

  bool findChild(uint16_t node_index, uint8_t value,
                 uint16_t& child) const {
    const uint8_t* current = node(node_index);
    uint16_t first = read16(current);
    uint16_t count = read16(current + 2);
    uint16_t low = 0;
    uint16_t high = count;
    while (low < high) {
      uint16_t middle = low + (high - low) / 2;
      const uint8_t* candidate = edge(first + middle);
      if (candidate[0] < value) {
        low = middle + 1;
      } else {
        high = middle;
      }
    }
    if (low >= count) return false;
    const uint8_t* match = edge(first + low);
    if (match[0] != value) return false;
    child = read16(match + 1);
    return child < _node_count;
  }

public:
  void reset() {
    _nodes = nullptr;
    _edges = nullptr;
    _node_count = 0;
    _edge_count = 0;
  }

  bool begin(const uint8_t* font_data, size_t font_size) {
    reset();
    if (font_data == nullptr || font_size < FOOTER_V1_SIZE) return false;

    static const uint8_t FOOTER_V1_MAGIC[8] = {
      'M', 'C', 'E', 'M', 'A', 'P', '1', 0
    };
    static const uint8_t FOOTER_V2_MAGIC[8] = {
      'M', 'C', 'E', 'M', 'A', 'P', '2', 0
    };
    size_t footer_size = FOOTER_V1_SIZE;
    const uint8_t* footer = font_data + font_size - footer_size;
    if (font_size >= FOOTER_V2_SIZE
        && memcmp(font_data + font_size - FOOTER_V2_SIZE,
                  FOOTER_V2_MAGIC, sizeof(FOOTER_V2_MAGIC)) == 0) {
      footer_size = FOOTER_V2_SIZE;
      footer = font_data + font_size - footer_size;
    } else if (memcmp(footer, FOOTER_V1_MAGIC,
                      sizeof(FOOTER_V1_MAGIC)) != 0) {
      return false;
    }

    uint32_t map_offset = read32(footer + 8);
    uint32_t map_size = read32(footer + 12);
    uint32_t map_crc = read32(footer + 16);
    if (map_offset > font_size - footer_size
        || map_size > font_size - footer_size - map_offset
        || map_size < MAP_HEADER_SIZE) {
      return false;
    }

    const uint8_t* map = font_data + map_offset;
    if (crc32(map, map_size) != map_crc
        || memcmp(map, "EM01", 4) != 0) {
      return false;
    }

    uint16_t node_count = read16(map + 4);
    uint16_t edge_count = read16(map + 6);
    size_t expected_size = MAP_HEADER_SIZE
        + (size_t)node_count * NODE_SIZE
        + (size_t)edge_count * EDGE_SIZE;
    if (node_count == 0 || expected_size != map_size) return false;

    const uint8_t* nodes = map + MAP_HEADER_SIZE;
    const uint8_t* edges = nodes + (size_t)node_count * NODE_SIZE;
    for (uint16_t i = 0; i < node_count; ++i) {
      const uint8_t* current = nodes + (size_t)i * NODE_SIZE;
      uint16_t first = read16(current);
      uint16_t count = read16(current + 2);
      uint16_t output = read16(current + 4);
      if (first > edge_count || count > edge_count - first) return false;
      if (output != 0 && (output < 0xE000 || output > 0xF8FF)) return false;
      int previous = -1;
      for (uint16_t j = 0; j < count; ++j) {
        const uint8_t* current_edge = edges + (size_t)(first + j) * EDGE_SIZE;
        if (current_edge[0] <= previous
            || read16(current_edge + 1) >= node_count) {
          return false;
        }
        previous = current_edge[0];
      }
    }

    _nodes = nodes;
    _edges = edges;
    _node_count = node_count;
    _edge_count = edge_count;
    return true;
  }

  bool isReady() const { return _nodes != nullptr; }

  size_t longestMatch(const uint8_t* text, size_t length,
                      uint16_t& mapped_codepoint) const {
    mapped_codepoint = 0;
    if (!isReady() || text == nullptr || length == 0) return 0;

    uint16_t node_index = 0;
    size_t best_length = 0;
    for (size_t i = 0; i < length; ++i) {
      uint16_t child;
      if (!findChild(node_index, text[i], child)) break;
      node_index = child;
      uint16_t output = read16(node(node_index) + 4);
      if (output != 0) {
        mapped_codepoint = output;
        best_length = i + 1;
      }
    }
    return best_length;
  }
};

}  // namespace ui
}  // namespace mesh
