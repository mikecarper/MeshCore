#pragma once

namespace mesh {
#ifdef ARDUINO
bool wasColdBoot();
#else
inline bool wasColdBoot() { return false; }
#endif
}
