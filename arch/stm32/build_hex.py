Import("env")

# Run the size-oriented interprocedural passes during the final STM32 LTO link.
# PlatformIO's ordinary build flags do not forward these options to this link.
env.Append(LINKFLAGS=[
    "-flto-partition=one",
    "-fipa-pta",
    "-fno-semantic-interposition",
])

# Make custom HEX from ELF
env.AddPostAction(
    "$BUILD_DIR/${PROGNAME}.elf",
    env.VerboseAction(" ".join([
        "$OBJCOPY", "-O", "ihex", "-R", ".eeprom",
        '"$BUILD_DIR/${PROGNAME}.elf"', '"$BUILD_DIR/${PROGNAME}.hex"'
    ]), "Building $BUILD_DIR/${PROGNAME}.hex")
)
