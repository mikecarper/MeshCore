"""Omit process-exit cleanup only in exact, hash-qualified STM32 recipes."""
import hashlib
from pathlib import Path

MAIN_SHA256 = '09ca39e44837f99e091328f5a1775f5fdb7597d6915fd17d530f471e20ebace8'
FLAG = 'MESH_STM32_MINI_NO_PROGRAM_EXIT'
COMPANION_REQUEST = 'MESH_STM32_COMPANION_LIFETIME_POLICY'
COMPANION_TARGETS = {
    'RAK_3x72_companion_radio_usb': 'stm32wle5ccu',
    'wio-e5-mini_companion_radio_usb': 'stm32wle5jc',
    'wio-e5_companion_radio_usb': 'stm32wle5jc',
    'Tiny_Relay_companion_radio_usb': 'stm32wle5ccu',
}
COMPANION_SOURCES = ('../examples/companion_radio/main.cpp',
                     '../examples/companion_radio/MyMesh.cpp')


def supported_program_lifetime(framework):
    if not framework:
        return False
    main = Path(framework) / 'cores/arduino/main.cpp'
    if not main.is_file():
        return False
    # Git/archive transports can change only line endings on Windows.
    try:
        source = main.read_text(encoding='utf-8').replace('\r\n', '\n')
    except (OSError, UnicodeError):
        return False
    return hashlib.sha256(source.encode('utf-8')).hexdigest() == MAIN_SHA256


def install(env, role_policy):
    target = env.get('PIOENV')
    mini = target == 'wio-e5-mini_repeater'
    if not mini and target not in COMPANION_TARGETS:
        return False
    flags = role_policy.flag_defines(env.get('BUILD_FLAGS', ()), env.get('CPPDEFINES', ()))
    override = flags.get(FLAG)
    if FLAG in flags and not role_policy._enabled(override):
        return False
    request = 'MESH_STM32_MINI_LIFETIME_POLICY' if mini else COMPANION_REQUEST
    if not role_policy._enabled(flags.get(request)) and not role_policy._enabled(override):
        return False
    # The exact board recipe and its pinned core are both required. No user
    # capacity, stack, flash or filesystem limit is changed by this policy.
    sources = env.get('SRC_FILTER')
    if sources is None:
        sources = env.GetProjectOption('build_src_filter', '') if hasattr(env, 'GetProjectOption') else ()
    if mini:
        required = (role_policy.ROLE_SOURCES[0],)
        forbidden = role_policy.ROLE_SOURCES[1:] + role_policy.COMPANION_SOURCES
        expected_mcu = 'stm32wle5jc'
    else:
        required = COMPANION_SOURCES
        forbidden = role_policy.ROLE_SOURCES + role_policy.COMPANION_SOURCES[1:]
        expected_mcu = COMPANION_TARGETS[target]
        if (getattr(env.PioPlatform(), 'name', None) != 'ststm32'
                or not role_policy._enabled(flags.get('STM32WL_COMPANION_COMPACT_UNSUPPORTED_AGC'))):
            raise ValueError('Companion program lifetime requires its exact STM32WL USB recipe')
    if (str(env.BoardConfig().get('build.mcu', '')).lower() != expected_mcu
            or not role_policy._enabled(flags.get('STM32_PLATFORM'))
            or not all(role_policy.source_selected(sources, source) for source in required)
            or any(role_policy.source_selected(sources, source)
                   for source in forbidden)):
        raise ValueError('STM32 program lifetime requires its exact qualified role and MCU')
    framework = env.PioPlatform().get_package_dir('framework-arduinoststm32')
    supported = bool(framework) and supported_program_lifetime(framework)
    if FLAG in flags:
        enabled = role_policy._enabled(override)
        if enabled and not supported:
            raise ValueError('unqualified STM32 process-exit override')
        return supported and enabled
    if supported:
        env.AppendUnique(CPPDEFINES=[(FLAG, 1)])
        print(('Mini' if mini else 'Companion') + ' process lifetime: verified non-returning Arduino main')
    return supported
