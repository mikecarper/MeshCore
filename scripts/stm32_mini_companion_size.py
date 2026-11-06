"""Apply the Mini Companion loop size policy during the final LTO link."""

Import("env")

if env["PIOENV"] == "wio-e5-mini_companion_radio_usb":
    env.Append(LINKFLAGS=["-fno-tree-loop-im"])
