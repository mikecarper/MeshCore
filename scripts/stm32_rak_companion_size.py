"""Keep the RAK STM32WL Companion's compact policy in the final LTO link."""

Import("env")

if env["PIOENV"] == "RAK_3x72_companion_radio_usb":
    env.Append(LINKFLAGS=["-fno-tree-loop-im", "-fno-tree-pre",
                          "-fno-tree-slsr", "-fno-code-hoisting"])
