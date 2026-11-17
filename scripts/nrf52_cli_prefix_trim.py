"""Share reviewed CLI reply prefixes in opt-in RAK and exact Mini builds.

The emitted reply bytes and command behavior are unchanged. Only private build
copies are edited; the project and downloaded libraries remain untouched.
Unknown sources or builds fail closed instead of guessing at replacements.
"""

import hashlib
import importlib.util
from pathlib import Path
import re


SOURCES = {
    'examples/simple_repeater/MyMesh.cpp': (
        '573bff72a760942a55c2e57abb658f5bd2be3f04519ae31db333ffaf669a4e6f',
        '951e34e0a8490a2ee9de1d84ed73bfbef77f173a93c8c67ca485b97c753d544c'),
    'src/helpers/CommonCLI.cpp': (
        '4e834c41455d1dca4f4f6beb406a49ed475d0a8a8f14bb0edb31ab6aceec7d85',
        'b7f98051122effe3a59e545edec1f776b53eb874007669c9a280867389863c5d'),
    'src/helpers/ota/OtaCli.cpp': (
        'dbe8f7e6f282942f3edd6be542d8e02acfff47ef86652f9b800e00da074dfcc5',
        '562017187e8ae475c3401beb544aabfd9d5b1abec29f597dedd5cf719670ebb3'),
    'src/helpers/CLIReplyCopy.cpp': (
        '6225345ea6a331fbff855a384508919b6a1bd870a69acc89dbab33f4418ceb18',
        '78d0b4caa9f90bd2df1b28937040b1f2f0a0f4325ed45eb4d615e69c10dcddc2'),
}

ENABLED = ('(defined(MESH_CLI_SHARE_ERROR_PREFIXES) && MESH_CLI_SHARE_ERROR_PREFIXES && '
           'defined(MESH_CLI_SHARE_REPLY_COPIES) && MESH_CLI_SHARE_REPLY_COPIES)')
CASES = {
    'examples/simple_repeater/MyMesh.cpp': (
        '/* ------------------------------ Config -------------------------------- */',
        (('Err - ', 'MESH_SHARED_ERR', 'copyErrorDash', 140),
         ('OK - ', 'MESH_SHARED_OK', 'copyOkDash', 18),
         ('Error: ', 'MESH_SHARED_ERROR_COLON', 'copyErrorColon', 10),
         ('Error, ', 'MESH_SHARED_ERROR_COMMA', 'copyErrorComma', 3))),
    'src/helpers/CommonCLI.cpp': (
        '// Believe it or not',
        (('Err - ', 'MESH_SHARED_ERR', 'copyErrorDash', 11),
         ('OK - ', 'MESH_SHARED_OK', 'copyOkDash', 11),
         ('Error: ', 'MESH_SHARED_ERROR_COLON', 'copyErrorColon', 3),
         ('Error, ', 'MESH_SHARED_ERROR_COMMA', 'copyErrorComma', 6),
         ('ERR: ', 'MESH_SHARED_UPPER_ERROR_COLON', 'copyUpperErrorColon', 9))),
    'src/helpers/ota/OtaCli.cpp': (
        'namespace mesh {', (('ERR ', 'MESH_SHARED_OTA_ERR', 'copyOtaError', 76),)),
}

# Only pairs whose full and compact forms have the same reviewed prefix are
# eligible. The suffix keeps the original MESH_CLI_REPLY_TEXT selection.
PAIRED_COUNTS = {
    'examples/simple_repeater/MyMesh.cpp': {'Err - ': 60, 'OK - ': 1, 'Error: ': 8},
    'src/helpers/CommonCLI.cpp': {'OK - ': 4, 'Error, ': 44, 'ERR: ': 2},
}


def digest(source):
    return hashlib.sha256(source.encode('utf-8')).hexdigest()


def patched_source(relative, source):
    source = source.replace('\r\n', '\n')
    expected = SOURCES.get(relative)
    if not expected or digest(source) != expected[0]:
        raise ValueError('unreviewed CLI prefix source: ' + relative)
    if relative == 'src/helpers/CLIReplyCopy.cpp':
        marker = '}}\n#endif'
        if source.count(marker) != 1:
            raise ValueError('unexpected shared reply implementation')
        block = '#if defined(MESH_CLI_SHARE_ERROR_PREFIXES) && MESH_CLI_SHARE_ERROR_PREFIXES\n'
        for prefix, func in (('Err - ', 'copyErrorDash'), ('OK - ', 'copyOkDash'),
                             ('ERR ', 'copyOtaError'), ('Error: ', 'copyErrorColon'),
                             ('Error, ', 'copyErrorComma'), ('ERR: ', 'copyUpperErrorColon')):
            block += (f'char* {func}(char* reply, const char* message) {{\n'
                      f'  memcpy(reply, "{prefix}", {len(prefix)});\n'
                      f'  strcpy(reply + {len(prefix)}, message);\n'
                      '  return reply;\n}\n')
        source = source.replace(marker, block + '#endif\n' + marker, 1)
    else:
        marker, groups = CASES[relative]
        if source.count(marker) != 1:
            raise ValueError('unexpected CLI prefix insertion marker: ' + relative)
        block = f'#if {ENABLED}\nnamespace mesh {{ namespace cli {{\n'
        for _, _, function, _ in groups:
            block += f'__attribute__((noipa)) char* {function}(char*, const char*);\n'
        block += '} }\n'
        for _, macro, function, _ in groups:
            block += f'#define {macro}(reply, message) ::mesh::cli::{function}(reply, message)\n'
        block += '#else\n'
        for prefix, macro, _, _ in groups:
            block += f'#define {macro}(reply, message) strcpy(reply, "{prefix}" message)\n'
        block += '#endif\n\n'
        for prefix, macro, _, expected_count in groups:
            pattern = re.compile(r'strcpy\s*\(\s*reply\s*,\s*"' + re.escape(prefix)
                                 + r'((?:\\.|[^"\\])*)"\s*\)')
            source, count = pattern.subn(lambda match: macro + '(reply, "' + match.group(1) + '")', source)
            if count != expected_count:
                raise ValueError('unexpected CLI prefix count: ' + relative)
            if relative in PAIRED_COUNTS:
                quoted = r'((?:\\.|[^"\\])*)'
                paired = re.compile(r'strcpy\s*\(\s*reply\s*,\s*MESH_CLI_REPLY_TEXT\(\s*"'
                    + re.escape(prefix) + quoted + r'"\s*,\s*"' + re.escape(prefix)
                    + quoted + r'"\s*\)\s*\)')
                source, paired_count = paired.subn(lambda match: macro
                    + '(reply, MESH_CLI_REPLY_TEXT("' + match.group(1) + '", "'
                    + match.group(2) + '"))', source)
                if paired_count != PAIRED_COUNTS[relative].get(prefix, 0):
                    raise ValueError('unexpected paired CLI prefix count: ' + relative)
        source = source.replace(marker, block + marker, 1)
    if digest(source) != expected[1]:
        raise ValueError('CLI prefix transformation differs from reviewed build: ' + relative)
    return source


def install(build_env):
    root = Path(build_env.subst('$PROJECT_DIR')).resolve()
    trim = root / 'scripts/nrf52_flash_trim.py'
    spec = importlib.util.spec_from_file_location('mesh_prefix_trim_role', trim)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    flags = module.flag_defines(build_env.get('BUILD_FLAGS', ()), build_env.get('CPPDEFINES', ()))
    if not module._enabled(flags.get('MESH_CLI_SHARE_ERROR_PREFIXES')):
        return
    mcu = str(build_env.BoardConfig().get('build.mcu', '')).lower()
    sources = build_env.get('SRC_FILTER', ())
    rak = module.is_rak_repeater(mcu, sources, flags)
    mini = (build_env.get('PIOENV') == 'wio-e5-mini_repeater'
            and mcu == 'stm32wle5jc'
            and module._enabled(flags.get('STM32_PLATFORM'))
            and module.source_selected(sources, module.ROLE_SOURCES[0])
            and not any(module.source_selected(sources, source)
                        for source in module.ROLE_SOURCES[1:] + module.COMPANION_SOURCES))
    if not module._enabled(flags.get('MESH_CLI_SHARE_REPLY_COPIES')) or not (rak or mini):
        raise ValueError('CLI prefix trim requires a qualified RAK or exact Mini repeater and shared reply copies')

    def replace(env, node):
        original = Path(node.srcnode().get_abspath()).resolve()
        try:
            relative = original.relative_to(root).as_posix()
        except ValueError:
            return node
        if relative not in SOURCES:
            return node
        content = patched_source(relative, original.read_text(encoding='utf-8'))
        destination = Path(env.subst('$BUILD_DIR')) / 'nrf52-cli-prefix' / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or destination.read_text(encoding='utf-8') != content:
            destination.write_text(content, encoding='utf-8')
        includes = [str(original.parent), *env.get('CPPPATH', ())]
        local = env.Clone(CPPPATH=includes)
        if relative == 'src/helpers/CommonCLI.cpp':
            flags = list(local.get('CCFLAGS', ()))
            if '-fno-builtin-strcpy' not in flags:
                flags.append('-fno-builtin-strcpy')
            return local.Object(local.File(str(destination)), CCFLAGS=flags, CPPPATH=includes)[0]
        return local.Object(local.File(str(destination)), CPPPATH=includes)[0]

    for name in ('MyMesh.cpp', 'CommonCLI.cpp', 'OtaCli.cpp', 'CLIReplyCopy.cpp'):
        build_env.AddBuildMiddleware(replace, '*' + name)
    print('CLI prefix trim: byte-identical replies in private build sources')


if 'Import' in globals():
    Import('env')
    install(env)
