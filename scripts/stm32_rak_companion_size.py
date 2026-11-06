"""Keep the RAK STM32WL Companion's loop size policy in the final LTO link."""

Import("env")

if env["PIOENV"] == "RAK_3x72_companion_radio_usb":
    env.Append(LINKFLAGS=["-fno-tree-loop-im"])
