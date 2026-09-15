// Arduino builds every source in its variant directory; this adapter selects
// only pin/startup definitions, not target.cpp's Mesh radio/identity objects.
#include "../../../variants/xiao_nrf52/variant.cpp"
