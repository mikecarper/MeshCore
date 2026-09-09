# Home-screen text spacing

On non-Indicator companions, a visible Bluetooth PIN or connection status owns
a separate bottom-aligned block. It replaces the ordinary inbox instruction,
clock and WiFi rows while displayed. The PIN stays below the inbox title; the
label and value use measured font heights, including the MeshPocket's larger
e-paper font. An unusually short viewport may omit the label, never overlap
the title or clip the PIN. Indicator's dedicated pairing layout is unchanged.

The repeater home page also stacks measured font-height rows instead of fixed
ten-pixel offsets. Long names and status lines are ellipsized horizontally.

Run `python3 -m unittest discover -s test -p test_home_text_spacing.py -v` on a
host with a C++ compiler. This renders the actual companion and repeater home
branches, recording every text rectangle and rejecting overlaps or clipping.
It covers 128x64 OLED, 160x80 T096, 250x122 MeshPocket and larger viewports,
PIN/connected/disabled/prompt states, WiFi variants, long strings and a sweep
of font heights. It also checks the static reader hint. The separate native
display and T096 tests exercise the driver drawing/measurement methods.

Hardware acceptance: check that the full six-digit PIN is near the bottom,
with no text over it; connect/disconnect Bluetooth and verify no stale digits;
then check the repeater's five rows and the V4's non-blinking reader footer.
