#include "CardputerLayout.h"
#include <new>
#include <string.h>

using namespace Keyboard::Layout;

// The built-in map is immutable; keep its 450 bytes in flash instead of DRAM.
static const LayoutLayer default_layout_layers[2] = {
  { (Keyboard::Layout::LAYER_MASK_BASE),
    { "`", "",  "",  "",  "1", "q", "",  "",  "2", "w", "a", "",  "3", "e", "s", "z",  "4", "r", "d",
      "x", "5", "t", "f", "c", "6", "y", "g", "v", "7", "u", "h", "b", "8", "i", "j",  "n", "9", "o",
      "k", "m", "0", "p", "l", ",", "-", "[", ";", ".", "=", "]", "'", "/", "",  "\\", "",  "" } },
  { (Keyboard::Layout::LAYER_MASK_SHIFT),
    { "~", "",  "",  "",  "!", "Q", "",  "",  "@", "W", "A", "",  "#",  "E", "S", "Z", "$", "R", "D",
      "X", "%", "T", "F", "C", "^", "Y", "G", "V", "&", "U", "H", "B",  "*", "I", "J", "N", "(", "O",
      "K", "M", ")", "P", "L", "<", "_", "{", ":", ">", "+", "}", "\"", "?", "",  "|", "",  "" } }
};

static LayoutData default_layout = { .language_code = "en",
                                     .layers_count = 2,
                                     .layers = default_layout_layers };

uint8_t CardputerLayout::getLayerMask(Keyboard::Modifiers modifiers) const {
  return (modifiers.fn ? LAYER_MASK_FN : 0) | (modifiers.ctrl ? LAYER_MASK_CTRL : 0) |
         (modifiers.shift ? LAYER_MASK_SHIFT : 0) | (modifiers.opt ? LAYER_MASK_OPT : 0) |
         (modifiers.alt ? LAYER_MASK_ALT : 0);
}

Keyboard::Layout::CardputerLayout::CardputerLayout()
    : main_layout(&default_layout), current_layout(&default_layout) {}

CardputerLayout::~CardputerLayout() {
  if (main_layout != &default_layout) {
    delete[] main_layout->layers;
    delete main_layout;
  }
  if (alternate_layout) {
    delete[] alternate_layout->layers;
    delete alternate_layout;
  }
}


const char *CardputerLayout::lookup(Keyboard::Event &event, bool force_default) {
  if (event.key == Keyboard::KEY_NONE || event.key > TOTAL_KEYS || !current_layout) {
    return "";
  }

  const LayoutData *tgt = force_default ? &default_layout : current_layout;
  uint8_t mask = getLayerMask(event.modifiers);
  uint8_t key = event.key - 1;

  // Try to find an exact match in the current modifier layer
  for (uint8_t i = 0; i < tgt->layers_count; i++) {
    if (mask == tgt->layers[i].modifiers_mask) {
      const char *data = tgt->layers[i].entries[key].data;
      if (data && data[0]) {
        return data;
      }
    }
  }

  // If Shift is pressed and nothing was found, try the dedicated SHIFT layer
  if (event.modifiers.shift && mask != LAYER_MASK_SHIFT) {
    for (uint8_t i = 0; i < tgt->layers_count; i++) {
      if (LAYER_MASK_SHIFT == tgt->layers[i].modifiers_mask) {
        const char *data = tgt->layers[i].entries[key].data;
        if (data && data[0]) {
          return data;
        }
      }
    }
  }

  // Fallback to the BASE layer if we haven't returned anything yet
  if (mask != LAYER_MASK_BASE) {
    for (uint8_t i = 0; i < tgt->layers_count; i++) {
      if (LAYER_MASK_BASE == tgt->layers[i].modifiers_mask) {
        const char *data = tgt->layers[i].entries[key].data;
        if (data && data[0]) {
          return data;
        }
      }
    }
  }

  // Handle space if it not overriden
  if (event.key == Keyboard::KEY_SPACE && !event.modifiers.ctrl) {
    return " ";
  }

  return "";
}

void CardputerLayout::begin(FS &fs) {
  parseLayout(fs, LAYOUT_TYPE_MAIN);
  parseLayout(fs, LAYOUT_TYPE_ALT);
}

namespace {

// Read only the declared, bounded file bytes. A device which returns EOF or
// repeats bytes without advancing must not spin or silently publish a prefix.
bool readLayoutLine(File& file, size_t& remaining, char* line) {
  size_t length = 0;
  while (remaining) {
    const size_t position = file.position();
    const int value = file.read();
    if (value < 0 || file.position() != position + 1) return false;
    --remaining;
    if (value == '\n') break;
    if (value == 0 || length == CardputerLayout::MAX_LAYOUT_LINE_BYTES) return false;
    line[length++] = static_cast<char>(value);
  }
  if (length && line[length - 1] == '\r') --length;
  line[length] = '\0';
  return true;
}

bool readLayerMask(const char* line, uint8_t& mask) {
  if (line[0] != 'L' || strlen(line) < 3) return false;
  mask = 0;
  for (size_t i = 2; line[i]; ++i) {
    switch (line[i]) {
      case 'B': mask = LAYER_MASK_BASE; break;
      case 'F': mask |= LAYER_MASK_FN; break;
      case 'C': mask |= LAYER_MASK_CTRL; break;
      case 'S': mask |= LAYER_MASK_SHIFT; break;
      case 'O': mask |= LAYER_MASK_OPT; break;
      case 'A': mask |= LAYER_MASK_ALT; break;
    }
  }
  return true;
}

} // namespace

void CardputerLayout::parseLayout(FS &fs, LayoutType layoutType) {
  const char *filename = (layoutType == LAYOUT_TYPE_MAIN) ? KB_LAYOUT_MAIN_FILE : KB_LAYOUT_ALT_FILE;
  if (!fs.exists(filename)) {
    return;
  }

  File layout_file = fs.open(filename, FILE_READ, false);
  if (!layout_file) {
    return;
  }

  const size_t file_bytes = layout_file.size();
  if (file_bytes == 0 || file_bytes > MAX_LAYOUT_FILE_BYTES) {
    layout_file.close();
    return;
  }
  char line[MAX_LAYOUT_LINE_BYTES + 1];
  uint32_t masks = 0;
  uint8_t layer_count = 0;
  size_t remaining = file_bytes;
  bool valid = true;
  while (remaining && valid) {
    valid = readLayoutLine(layout_file, remaining, line);
    uint8_t mask;
    if (valid && readLayerMask(line, mask) && !(masks & (uint32_t(1) << mask))) {
      if (layer_count == MAX_LAYOUT_LAYERS) valid = false;
      else { masks |= uint32_t(1) << mask; ++layer_count; }
    }
  }
  if (!valid || !layer_count || !layout_file.seek(0)) {
    layout_file.close();
    return;
  }

  LayoutData* layout = new (std::nothrow) LayoutData();
  if (!layout) { layout_file.close(); return; }
  LayoutLayer* layers = new (std::nothrow) LayoutLayer[layer_count]();
  if (!layers) { delete layout; layout_file.close(); return; }
  layout->layers = layers;
  layout->layers_count = 0;
  int current_layer_index = -1;
  remaining = file_bytes;
  while (remaining && valid) {
    valid = readLayoutLine(layout_file, remaining, line);
    if (!valid) break;
    const size_t length = strlen(line);
    uint8_t mask;
    if (line[0] == 'N' && length >= 4) {
      memcpy(layout->language_code, line + 2, 2);
      layout->language_code[2] = '\0';
    } else if (readLayerMask(line, mask)) {
      current_layer_index = 0;
      while (current_layer_index < layout->layers_count
             && layers[current_layer_index].modifiers_mask != mask) ++current_layer_index;
      if (current_layer_index == layout->layers_count) {
        // The file may change or fail between passes. Never grow past the
        // counted allocation or publish a partial replacement.
        if (layout->layers_count == layer_count) { valid = false; break; }
        layers[current_layer_index].modifiers_mask = mask;
        ++layout->layers_count;
      }
    } else if (line[0] == 'K' && length >= 5 && line[4] == '='
               && current_layer_index >= 0) {
      const int key_id = (line[2] >= '0' && line[2] <= '9'
                          && line[3] >= '0' && line[3] <= '9')
          ? (line[2] - '0') * 10 + line[3] - '0' : 0;
      if (key_id >= 1 && key_id <= TOTAL_KEYS) {
        char* dest = layers[current_layer_index].entries[key_id - 1].data;
        memset(dest, 0, KB_LAYOUT_CHAR_MAX + 1);
        memcpy(dest, line + 5, length - 5 < KB_LAYOUT_CHAR_MAX ? length - 5 : KB_LAYOUT_CHAR_MAX);
      }
    }
  }
  layout_file.close();
  if (!valid || layout->layers_count != layer_count) {
    delete[] layout->layers;
    delete layout;
    return;
  }

  LayoutData*& previous = layoutType == LAYOUT_TYPE_MAIN ? main_layout : alternate_layout;
  if (previous && previous != &default_layout) {
    delete[] previous->layers;
    delete previous;
  }
  previous = layout;
  if (current_layout_type == layoutType) current_layout = layout;
}

void Keyboard::Layout::CardputerLayout::switchLayout() {
  LayoutType new_type = (current_layout_type == LAYOUT_TYPE_MAIN) ? LAYOUT_TYPE_ALT : LAYOUT_TYPE_MAIN;
  LayoutData *new_layout = (new_type == LAYOUT_TYPE_MAIN) ? main_layout : alternate_layout;

  if (new_layout) {
    current_layout_type = new_type;
    current_layout = new_layout;
  }
};
