"""Keep the Wio-E5 USB Companion's compact policy in final LTO codegen."""

Import("env")

if env["PIOENV"] == "wio-e5_companion_radio_usb":
    env.Append(LINKFLAGS=["-fno-tree-loop-im", "-fno-tree-pre",
                          "-fno-tree-slsr", "-fno-code-hoisting"])
