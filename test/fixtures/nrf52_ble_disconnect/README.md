The dispatcher in `bluefruit_dispatch.cpp` is copied without changes from
`libraries/Bluefruit52Lib/src/bluefruit.cpp` at the framework revision pinned
by `platformio.ini`:

https://github.com/meshcore-dev/Adafruit_nRF52_Arduino/blob/d541301665b40959682252911e57b11df3ee651a/libraries/Bluefruit52Lib/src/bluefruit.cpp

Its original BSD license is retained. `bluefruit.h` supplies host-side test
doubles; `test.cpp` exercises the complete dispatcher after the production
middleware transforms it. The unpatched dispatcher must fail under ASan with
a heap-use-after-free on disconnect. The fixed dispatcher must retain role
filtering, callback deferral, handle reuse, deletion order, and generic-event
routing for peripheral and central connections.
