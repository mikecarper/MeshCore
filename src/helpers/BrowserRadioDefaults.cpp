#include "BrowserRadioDefaults.h"

namespace mesh {
namespace browserRadioDefaults {

// Const storage keeps the record in genuine read-only flash on both GNU ARM
// and Xtensa. Volatile *access* below prevents LTO from folding patched bytes;
// qualifying the object itself as volatile incorrectly makes Xtensa rodata WA.
// A live reference from read() retains this single image with linker GC/LTO.
extern const uint8_t image[ImageSize]
    __attribute__((used, aligned(4), section(".rodata.mesh_radio_defaults"))) = {
  'M', 'C', 'R', 'D', 'E', 'F', 'A', 'U', 'L', 'T', 'S', '0', '0', '0', '1', 0,
  1, 0, 64, 0, 0, 0, 0, 0,
  0, 0, 0, 0, 0, 0, 0, 0,
  0, 0, 0, 0, 0, 0, 0, 0,
  0, 0, 0, 0, 0, 0, 0, 0,
  0, 0, 0, 0, 0, 0, 0, 0,
  0, 0, 0, 0, 0xa6, 0x73, 0x37, 0x89
};

bool read(Settings& result) {
  const volatile uint8_t* flash = image;
  uint8_t bytes[ImageSize];
  for (size_t n = 0; n < ImageSize; ++n) bytes[n] = flash[n];
  return decode(bytes, sizeof(bytes), result);
}

}  // namespace browserRadioDefaults
}  // namespace mesh
