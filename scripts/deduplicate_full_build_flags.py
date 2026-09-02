"""Collapse repeated external build-flag overlays in ESP32 Full builds.

PlatformIO appends ``PLATFORMIO_BUILD_FLAGS`` while resolving every
``extends`` layer.  A deep Full environment can therefore put the same large
release overlay on the compiler command line several times.  On Windows that
can exceed the command-line limit used when GCC starts ``cc1plus``.

Only exact logical ``-D``/``-U`` flags from the external overlay are
collapsed. The final overlay's multiplicity and order win, matching
preprocessor handling of ordered overrides, while search paths and arbitrary
compiler/linker inputs remain untouched. On Windows, existing include
directories are also rewritten to shorter 8.3 aliases without changing their
order. This keeps GCC's expanded ``cc1plus`` command below the CreateProcess
limit.
"""

Import("env")  # noqa: F821

import os
import shlex
from collections import Counter
from functools import lru_cache


# Keep GNU-style option/argument pairs indivisible so de-duplication can never
# strand (for example) a bare ``-D`` or detach it from its value.
OPTIONS_WITH_ARGUMENT = {
    "-D",
    "-U",
    "-I",
    "-L",
    "-l",
    "-F",
    "-arch",
    "-dylib_file",
    "-framework",
    "-idirafter",
    "-imacros",
    "-include",
    "-iquote",
    "-isystem",
    "-isysroot",
    "--param",
}


# An object marker keeps a raw ``!command`` distinct from an ordinary quoted
# token whose value merely starts with ``!``.
COMMAND_FLAG = object()
SHORTENING_REPORTED = False


def safely_deduplicated(flag):
    """Return whether removing exact repeats cannot alter input ordering."""
    if flag[0] is COMMAND_FLAG:
        return False
    if len(flag) == 2:
        return flag[0] in {"-D", "-U"}
    return flag[0].startswith(("-D", "-U"))


def logical_flags(values):
    """Return shell tokens, grouping options that consume one argument."""
    if not isinstance(values, (list, tuple)):
        values = [values]

    result = []
    for raw in values:
        text = str(raw)
        if not text.strip():
            continue
        if text.startswith("!"):
            # ParseFlags executes a leading-! item and its output is not known
            # yet. Keep it opaque rather than changing command semantics.
            result.append((COMMAND_FLAG, text))
            continue

        tokens = shlex.split(text)
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token in OPTIONS_WITH_ARGUMENT and index + 1 < len(tokens):
                result.append((token, tokens[index + 1]))
                index += 2
            else:
                result.append((token,))
                index += 1
    return result


def serialize_flags(flags):
    """Serialize logical flags for a second, equivalent ParseFlags pass."""
    return [
        flag[1] if flag[0] is COMMAND_FLAG
        else shlex.join(flag)
        for flag in flags
    ]


def deduplicate_overlay(build_flags, overlay):
    """Retain the final overlay's exact multiplicity of safe macro flags."""
    try:
        flags = logical_flags(build_flags)
        overlay_counts = Counter(
            flag
            for flag in logical_flags(overlay)
            if safely_deduplicated(flag)
        )
    except ValueError:
        # Preserve PlatformIO's original input and error behavior when shell
        # quoting is malformed; this compactor must not reinterpret it.
        return build_flags, 0
    if not overlay_counts:
        return serialize_flags(flags), 0

    seen_from_right = Counter()
    retained_reversed = []
    for flag in reversed(flags):
        if flag in overlay_counts:
            seen_from_right[flag] += 1
            if seen_from_right[flag] > overlay_counts[flag]:
                continue
        retained_reversed.append(flag)
    unique = list(reversed(retained_reversed))
    return serialize_flags(unique), len(flags) - len(unique)


def resolved_path(value, project_dir, expander=None):
    """Resolve a SCons path node/string without requiring SCons in tests."""
    if hasattr(value, "get_abspath"):
        return value.get_abspath()
    text = str(value)
    if expander is not None:
        text = expander(text)
    if not os.path.isabs(text):
        text = os.path.join(project_dir, text)
    return os.path.abspath(text)


def shorten_cpppaths(values, project_dir, shortener, expander=None):
    """Use a shorter alias for each path when one exists; retain list order."""
    result = []
    characters_saved = 0
    for value in values:
        original = str(value)
        candidate = shortener(
            resolved_path(value, project_dir, expander=expander)
        )
        if candidate:
            candidate = candidate.replace("\\", "/")
        if candidate and len(candidate) < len(original):
            result.append(candidate)
            characters_saved += len(original) - len(candidate)
        else:
            result.append(value)
    return result, characters_saved


@lru_cache(maxsize=None)
def windows_short_path(path):
    """Return an existing path's Win32 8.3 alias, or None when unavailable."""
    if not os.path.exists(path):
        return None

    import ctypes
    from ctypes import wintypes

    get_short_path = ctypes.WinDLL(
        "kernel32", use_last_error=True
    ).GetShortPathNameW
    get_short_path.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    )
    get_short_path.restype = wintypes.DWORD

    required = get_short_path(path, None, 0)
    if not required:
        return None
    buffer = ctypes.create_unicode_buffer(required)
    written = get_short_path(path, buffer, required)
    if not written or written >= required:
        return None
    return buffer.value


def shorten_full_build_cpppaths(build_env, node):
    """PlatformIO build middleware applied after all include paths exist."""
    global SHORTENING_REPORTED
    if build_env.get("_MESHCORE_FULL_CPPPATHS_SHORTENED", False):
        return node
    cpppaths = build_env.get("CPPPATH", [])
    if not cpppaths:
        return node
    build_env.Replace(_MESHCORE_FULL_CPPPATHS_SHORTENED=True)
    project_dir = build_env.subst("$PROJECT_DIR")
    shortened, saved = shorten_cpppaths(
        cpppaths,
        project_dir,
        windows_short_path,
        expander=build_env.subst,
    )
    if saved:
        build_env.Replace(CPPPATH=shortened)
        if not SHORTENING_REPORTED:
            print(
                "ESP32 FULL build: shortened Windows include paths by %d chars"
                % saved
            )
            SHORTENING_REPORTED = True
    return node


if os.environ.get("MESHCORE_ESP32_FULL_BUILD") == "1":
    original = env.get("BUILD_FLAGS", [])  # noqa: F821
    cleaned, removed = deduplicate_overlay(
        original, os.environ.get("PLATFORMIO_BUILD_FLAGS", "")
    )
    if removed:
        env.Replace(BUILD_FLAGS=cleaned)  # noqa: F821
        print(
            "ESP32 FULL build: removed %d repeated external build flags"
            % removed
        )
    if (
        os.name == "nt"
        and os.environ.get("MESHCORE_COMPANION_RADIO_FULL") == "1"
        and not env.get(  # noqa: F821
            "_MESHCORE_FULL_CPPPATH_SHORTENER_INSTALLED", False
        )
    ):
        env.Replace(  # noqa: F821
            _MESHCORE_FULL_CPPPATH_SHORTENER_INSTALLED=True
        )
        # Library builders inherit middleware from the root environment. Wait
        # until PlatformIO starts the final Companion project sources, after
        # dependency discovery has finished appending include directories.
        env.AddBuildMiddleware(  # noqa: F821
            shorten_full_build_cpppaths,
            "*examples*companion_radio*.cpp",
        )
