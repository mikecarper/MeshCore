Import("env")

# This script is loaded as a ``pre:`` extra script.  The Nordic platform's
# builder selects binutils ar/ranlib later while it creates the library build
# actions.  Those tools do not generate the plugin index required by GNU LTO,
# which leaves the final link with hundreds of false "undefined reference"
# errors.  Intercept that later replacement so every nRF52 archive is built
# with GCC's plugin-aware wrappers.
_replace = env.Replace


def _replace_with_lto_archivers(*args, **kwargs):
    if kwargs.get("AR") == "arm-none-eabi-ar":
        kwargs["AR"] = "arm-none-eabi-gcc-ar"
    if kwargs.get("RANLIB") == "arm-none-eabi-ranlib":
        kwargs["RANLIB"] = "arm-none-eabi-gcc-ranlib"
    return _replace(*args, **kwargs)


env.Replace = _replace_with_lto_archivers

# GCC runs the final whole-program passes while linking.  PlatformIO supplies
# the project flags to compilation only, so forward the size policy explicitly
# to that final LTO invocation as well.  One partition also avoids GCC 14
# generating out-of-range Thumb literal loads in large nRF52 images.
env.Append(LINKFLAGS=[
    "-Oz",
    "-flto",
    "-flto-partition=one",
    "-fno-inline-small-functions",
    "-fipa-pta",
    "-fmerge-all-constants",
    "-fno-ipa-cp-clone",
    "-fno-partial-inlining",
    "-fno-jump-tables",
    "-fno-tree-switch-conversion",
    "-fno-semantic-interposition",
    "-fno-unwind-tables",
    "-fno-asynchronous-unwind-tables",
    "-fno-schedule-insns2",
])
