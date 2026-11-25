"""Apply MeshCore's loop-stack and diagnostic-output policy to the nRF52 core.

GNU --wrap only redirects undefined references. The pinned core and FreeRTOS
share an LTO graph, so its xTaskCreate call can bind directly to the real
function and silently retain the framework's 4 KiB stack. Explicitly route
only the core loop-task call through MeshCore's existing stack policy in a
private build copy. Retarget the default framework printf logger through
MeshCore's bounded, runtime-selected diagnostic stream: raw Serial writes
otherwise corrupt the Companion protocol, including on dual-CDC hardware.
Explicit UART/RTT loggers retain their framework behavior. Never edit the
shared framework package.
"""

from pathlib import Path


OLD_LOOP_CREATE = '  xTaskCreate(loop_task, "loop", LOOP_STACK_SZ, NULL, TASK_PRIO_LOW, &_loopHandle);'
LOOP_CREATE = OLD_LOOP_CREATE.replace("xTaskCreate(", "__wrap_xTaskCreate(", 1)
DECLARATION = """extern "C" BaseType_t __wrap_xTaskCreate(
    TaskFunction_t task_code, const char* const task_name,
    const configSTACK_DEPTH_TYPE stack_depth, void* const parameters,
    UBaseType_t priority, TaskHandle_t* const created_task);

"""
LOOP_STACK = "#define LOOP_STACK_SZ       (256*4)"
LOGGER_ENTRY = "// nanolib printf() retarget\n"
LOGGER_DECLARATION = """// MeshCore routes default framework diagnostics without borrowing CDC0.
size_t meshNrf52FrameworkDebugWrite(const void* data, size_t size)
    __attribute__((weak));

"""
OLD_LOGGER = """#else
  if ( Serial )
  {
    ret = Serial.write((const uint8_t *) buf, count);
  }
#endif

  return (int) ret;
"""
LOGGER = """#else
  if ( meshNrf52FrameworkDebugWrite )
  {
    ret = meshNrf52FrameworkDebugWrite(buf, count);
  }
  else if ( Serial )
  {
    ret = Serial.write((const uint8_t *) buf, count);
  }
#endif

  return (int) ret;
"""


def patched_source(source):
    source = source.replace("\r\n", "\n")
    if (source.count(LOOP_CREATE) == 1 and source.count(DECLARATION) == 1
            and OLD_LOOP_CREATE not in source and source.count(LOOP_STACK) == 1):
        return source
    if (source.count(OLD_LOOP_CREATE) != 1 or source.count(LOOP_STACK) != 1
            or LOOP_CREATE in source or DECLARATION in source):
        raise RuntimeError(
            "nRF52 loop stack fix: unrecognized core task creation; review the "
            "framework update before building (shared SDK not modified)"
        )
    return source.replace(LOOP_STACK, DECLARATION + LOOP_STACK).replace(
        OLD_LOOP_CREATE, LOOP_CREATE)


def patched_logging_source(source):
    source = source.replace("\r\n", "\n")
    if (source.count(LOGGER) == 1 and source.count(LOGGER_DECLARATION) == 1
            and OLD_LOGGER not in source and source.count(LOGGER_ENTRY) == 1):
        return source
    if (source.count(OLD_LOGGER) != 1 or source.count(LOGGER_ENTRY) != 1
            or LOGGER in source or LOGGER_DECLARATION in source):
        raise RuntimeError(
            "nRF52 framework logging fix: unrecognized printf retarget; review "
            "the framework update before building (shared SDK not modified)"
        )
    return source.replace(LOGGER_ENTRY, LOGGER_DECLARATION + LOGGER_ENTRY).replace(
        OLD_LOGGER, LOGGER)


def replace_core(build_env, node):
    source = Path(node.srcnode().get_abspath())
    patched = patched_logging_source(patched_source(source.read_text(encoding="utf-8")))
    # Core calls bind inside one LTO graph. Gate startup in that same source
    # before TinyUSB can enumerate, including the optional debug UART fallback.
    entry = "  TinyUSB_Device_Init(0);"
    debug = "  if(!Serial) Serial.begin(115200);"
    if patched.count(entry) != 1 or patched.count(debug) != 1:
        raise RuntimeError("Unrecognized nRF52 core USB startup")
    patched = ('namespace mesh { bool usbDataLocked(); void applyUsbDataLockAtBoot(); }\n' + patched
               .replace(entry, "  mesh::applyUsbDataLockAtBoot();\n  if (!mesh::usbDataLocked()) TinyUSB_Device_Init(0);")
               .replace(debug, "  if (!mesh::usbDataLocked() && !Serial) Serial.begin(115200);"))
    destination = Path(build_env.subst("$BUILD_DIR")) / "patched-nrf52-core" / "main.cpp"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_text(encoding="utf-8") != patched:
        destination.write_text(patched, encoding="utf-8")
    print("nRF52 core: LTO-safe loop stack and routed framework diagnostics enabled")
    return build_env.File(str(destination))


def install(build_env):
    build_env.AddBuildMiddleware(replace_core, "*cores*nRF5*main.cpp")


if "Import" in globals():
    Import("env")
    install(env)
