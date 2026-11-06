"""Retain Mini Companion size and dead-section policy in final LTO codegen."""

Import("env")

if env["PIOENV"] == "wio-e5-mini_companion_radio_usb":
    # The shared STM32 final link preserves function/data sections. Keep only
    # the Mini's qualified branch/loop choices in this target-specific hook.
    env.Append(LINKFLAGS=["-fno-tree-loop-im", "-fno-tree-pre",
                          "-fno-tree-slsr", "-fno-code-hoisting"])
