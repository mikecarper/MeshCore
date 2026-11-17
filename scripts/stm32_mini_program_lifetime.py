"""Omit unreachable global process-exit cleanup in the exact qualified Mini."""
import hashlib
from pathlib import Path

MAIN_SHA256 = '09ca39e44837f99e091328f5a1775f5fdb7597d6915fd17d530f471e20ebace8'
FLAG = 'MESH_STM32_MINI_NO_PROGRAM_EXIT'


def supported_program_lifetime(framework):
    if not framework:
        return False
    main = Path(framework) / 'cores/arduino/main.cpp'
    if not main.is_file():
        return False
    # Git/archive transports can change only line endings on Windows.
    source = main.read_text(encoding='utf-8').replace('\r\n', '\n')
    return hashlib.sha256(source.encode('utf-8')).hexdigest() == MAIN_SHA256


def install(env, role_policy):
    if env.get('PIOENV') != 'wio-e5-mini_repeater':
        return False
    flags = role_policy.flag_defines(env.get('BUILD_FLAGS', ()), env.get('CPPDEFINES', ()))
    override = flags.get(FLAG)
    if FLAG in flags and not role_policy._enabled(override):
        return False
    if not role_policy._enabled(flags.get('MESH_STM32_MINI_LIFETIME_POLICY')) and not role_policy._enabled(override):
        return False
    # The exact board recipe and its pinned core are both required. No user
    # capacity, stack, flash or filesystem limit is changed by this policy.
    sources = env.get('SRC_FILTER', ())
    if (str(env.BoardConfig().get('build.mcu', '')).lower() != 'stm32wle5jc'
            or not role_policy._enabled(flags.get('STM32_PLATFORM'))
            or not role_policy.source_selected(sources, role_policy.ROLE_SOURCES[0])
            or any(role_policy.source_selected(sources, source)
                   for source in role_policy.ROLE_SOURCES[1:] + role_policy.COMPANION_SOURCES)):
        raise ValueError('Mini program lifetime requires its exact STM32WL repeater recipe')
    framework = env.PioPlatform().get_package_dir('framework-arduinoststm32')
    supported = bool(framework) and supported_program_lifetime(framework)
    if FLAG in flags:
        enabled = role_policy._enabled(override)
        if enabled and not supported:
            raise ValueError('unqualified STM32 process-exit override')
        return supported and enabled
    if supported:
        env.AppendUnique(CPPDEFINES=[(FLAG, 1)])
        print('Mini process lifetime: verified non-returning Arduino main')
    return supported
