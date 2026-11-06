"""Keep Tiny Relay Companion's compact policy in the final LTO link."""

Import("env")

if env["PIOENV"] == "Tiny_Relay_companion_radio_usb":
    env.Append(LINKFLAGS=["-fno-tree-loop-im", "-fno-tree-pre",
                          "-fno-tree-slsr", "-fno-code-hoisting"])
