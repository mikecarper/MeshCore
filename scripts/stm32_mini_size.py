"""Retain the measured Mini Repeater size policy in the final LTO link."""

Import("env")

# PlatformIO's build_flags apply these to compilation, but do not pass them
# to the LTO driver automatically. Keep this list matched by the profile test.
# No compiler/ABI, flash boundary or capacity check is overridden here.
if env["PIOENV"] == "wio-e5-mini_repeater":
    env.Append(LINKFLAGS=[
        "-fno-caller-saves",
        "-fno-tree-dominator-opts",
        "-fno-reorder-blocks",
        "-fno-reorder-functions",
        "-fno-tree-switch-conversion",
        "-flto-partition=one",
    ])
