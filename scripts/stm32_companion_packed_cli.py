"""Pack immutable CLI literals in four reviewed small STM32 Companions.

Only private build copies change. Commands, text selection, format arguments,
reply bytes and capacities remain intact. Reviewed source/output hashes and
call counts reject source drift instead of guessing at new C++ constructs.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import re

SCRIPT_DIR = (Path(__file__).resolve().parent if '__file__' in globals()
              else Path('scripts').resolve())
ROOT = SCRIPT_DIR.parent
FLAG = 'MESH_STM32_COMPANION_CLI_PACKING'
TARGETS = {
    'RAK_3x72_companion_radio_usb': 'stm32wle5ccu',
    'wio-e5-mini_companion_radio_usb': 'stm32wle5jc',
    'wio-e5_companion_radio_usb': 'stm32wle5jc',
    'Tiny_Relay_companion_radio_usb': 'stm32wle5ccu',
}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CODEC_MODULE = _load('mesh_stm32_output_bpe', SCRIPT_DIR / 'cli_output_bpe.py')
MANIFEST = json.loads((SCRIPT_DIR / 'stm32_companion_packed_cli.json').read_text(encoding='utf-8'))
SOURCES = MANIFEST['sources']
TRAINING_TEXTS = tuple(MANIFEST['training'])
CODEC = CODEC_MODULE.BytePairCodec(MANIFEST['pairs'])
if CODEC.max_depth != MANIFEST['max_depth'] or CODEC.max_depth > 6:
    raise ValueError('unreviewed CLI dictionary depth')

CALLS = re.compile(r'(?<![A-Za-z0-9_:])(?P<name>(?:(?:mesh::)?cli::)?formatError|snprintf|sprintf|strcpy|appendReply)\s*\(')
ORDINAL = {'strcpy': 1, 'sprintf': 1, 'snprintf': 2, 'formatError': 2, 'appendReply': 2}
FUNCTIONS = {'strcpy': 'copyPacked', 'sprintf': 'printfPacked',
             'snprintf': 'snprintfPacked', 'formatError': 'errorPacked',
             'appendReply': 'appendPacked'}
NAMESPACE = '::mesh::cli::outputpack::'


def digest(source):
    return hashlib.sha256(source.replace('\r\n', '\n').encode('utf-8')).hexdigest()


def _mask(source):
    """Keep code offsets, masking comments, strings, chars and raw strings."""
    result = list(source)
    index = 0
    while index < len(source):
        start = index
        if source.startswith('//', index):
            end = source.find('\n', index + 2)
            index = len(source) if end < 0 else end
        elif source.startswith('/*', index):
            end = source.find('*/', index + 2)
            if end < 0:
                raise ValueError('unterminated C++ comment')
            index = end + 2
        elif source.startswith('R"', index):
            opening = source.find('(', index + 2, index + 19)
            if opening < 0:
                raise ValueError('unsupported C++ raw string')
            delimiter = source[index + 2:opening]
            if any(c.isspace() or c in '\\()' for c in delimiter):
                raise ValueError('unsupported C++ raw delimiter')
            ending = ')' + delimiter + '"'
            end = source.find(ending, opening + 1)
            if end < 0:
                raise ValueError('unterminated C++ raw string')
            index = end + len(ending)
        elif source[index] in ('"', "'"):
            quote = source[index]
            index += 1
            while index < len(source):
                if source[index] == '\\':
                    if index + 1 == len(source):
                        raise ValueError('truncated C++ literal')
                    index += 2
                elif source[index] == quote:
                    index += 1
                    break
                else:
                    index += 1
            else:
                raise ValueError('unterminated C++ literal')
        else:
            index += 1
            continue
        for position in range(start, index):
            if result[position] not in '\r\n':
                result[position] = ' '
    return ''.join(result)


def _arguments(source, opening, masked=None):
    masked = _mask(source) if masked is None else masked
    depth = 1
    start = opening + 1
    result = []
    for index in range(start, len(source)):
        char = masked[index]
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if not depth:
                result.append(source[start:index].strip())
                return index + 1, result
        elif char == ',' and depth == 1:
            result.append(source[start:index].strip())
            start = index + 1
    raise ValueError('unbalanced C++ call')


class UnsupportedLiteral(ValueError):
    pass


def _trivia(text, index):
    while index < len(text):
        if text[index].isspace():
            index += 1
        elif text.startswith('/*', index):
            end = text.find('*/', index + 2)
            if end < 0:
                raise UnsupportedLiteral('unterminated literal comment')
            index = end + 2
        elif text.startswith('//', index):
            end = text.find('\n', index + 2)
            index = len(text) if end < 0 else end
        else:
            break
    return index


def _string(text, start):
    index = start + 1
    output = []
    escapes = {'a': 7, 'b': 8, 'f': 12, 'n': 10, 'r': 13, 't': 9,
               'v': 11, '\\': 92, "'": 39, '"': 34, '?': 63}
    while index < len(text):
        char = text[index]
        index += 1
        if char == '"':
            return ''.join(output), index
        if char == '\\':
            if index == len(text):
                raise UnsupportedLiteral('truncated C escape')
            char = text[index]
            index += 1
            if char in escapes:
                number = escapes[char]
            elif char in '01234567':
                digits = char
                while index < len(text) and len(digits) < 3 and text[index] in '01234567':
                    digits += text[index]
                    index += 1
                number = int(digits, 8)
            elif char == 'x':
                begin = index
                while index < len(text) and text[index] in '0123456789abcdefABCDEF':
                    index += 1
                if begin == index:
                    raise UnsupportedLiteral('empty C hex escape')
                number = int(text[begin:index], 16)
            elif char == '\n':
                continue
            elif char == '\r' and index < len(text) and text[index] == '\n':
                index += 1
                continue
            else:
                raise UnsupportedLiteral('unreviewed C escape')
        else:
            if char in '\r\n':
                raise UnsupportedLiteral('newline in C literal')
            number = ord(char)
        if not 0 < number < 128:
            raise UnsupportedLiteral('packed literals require non-NUL ASCII')
        output.append(chr(number))
    raise UnsupportedLiteral('unterminated C literal')


def parse_literal_expression(expression):
    expression = expression.strip()
    if expression.startswith('MESH_CLI_REPLY_TEXT('):
        end, parts = _arguments(expression, len('MESH_CLI_REPLY_TEXT'))
        if end != len(expression) or len(parts) != 2:
            return None
        values = [parse_literal_expression(part) for part in parts]
        if not all(value and len(value) == 1 for value in values):
            raise UnsupportedLiteral('nested reply text selection is not reviewed')
        return values[0] + values[1]
    index = _trivia(expression, 0)
    output = []
    while index < len(expression) and expression[index] == '"':
        value, index = _string(expression, index)
        output.append(value)
        index = _trivia(expression, index)
    return (''.join(output),) if output and index == len(expression) else None


def source_records(source, eligible=None):
    eligible = set(TRAINING_TEXTS) if eligible is None else set(eligible)
    masked = _mask(source)
    records = []
    for match in CALLS.finditer(masked):
        previous = masked[:match.start()].rstrip()
        if previous and previous[-1] in '.>:':
            continue
        end, args = _arguments(source, match.end() - 1, masked)
        function = match['name'].split('::')[-1]
        ordinal = ORDINAL[function]
        if len(args) <= ordinal:
            continue
        expression = args[ordinal]
        try:
            texts = parse_literal_expression(expression)
        except UnsupportedLiteral:
            continue
        if not texts or texts[-1] not in eligible:
            continue
        records.append({'function': function, 'expression': expression,
                        'texts': texts, 'args': args, 'begin': match.start(), 'end': end})
    return records


def literal_name(text):
    return 'literal_' + hashlib.sha256(text.encode('ascii')).hexdigest()[:16]


def _raw_patched_source(source):
    records = source_records(source)
    occupied = len(source) + 1
    for record in reversed(records):
        if record['end'] > occupied:
            raise ValueError('overlapping packed CLI calls')
        args = list(record['args'])
        packed = [NAMESPACE + literal_name(text) for text in record['texts']]
        args[ORDINAL[record['function']]] = packed[0] if len(packed) == 1 else 'MESH_CLI_REPLY_TEXT(' + ', '.join(packed) + ')'
        replacement = NAMESPACE + FUNCTIONS[record['function']] + '(' + ', '.join(args) + ')'
        source = source[:record['begin']] + replacement + source[record['end']:]
        occupied = record['begin']
    return '#include <helpers/Stm32PackedCliReplies.h>\n' + source


def patched_source(relative, source):
    source = source.replace('\r\n', '\n')
    expected = SOURCES.get(relative)
    if not expected or digest(source) != expected['input_sha']:
        raise ValueError('unreviewed packed CLI source: ' + relative)
    records = source_records(source)
    counts = {name: sum(record['function'] == name for record in records) for name in FUNCTIONS}
    if len(records) != expected['call_count'] or counts != expected['function_counts']:
        raise ValueError('unreviewed packed CLI call counts: ' + relative)
    result = _raw_patched_source(source)
    if digest(result) != expected['output_sha']:
        raise ValueError('unreviewed packed CLI output: ' + relative)
    return result


def generated_header():
    arrays = {}
    formats = []
    for relative in SOURCES:
        source = (ROOT / relative).read_text(encoding='utf-8')
        patched_source(relative, source)
        for record in source_records(source):
            for text in record['texts']:
                arrays[literal_name(text)] = text
                if record['function'] in ('sprintf', 'snprintf', 'appendReply'):
                    formats.append(len(text))
    if len(arrays) != MANIFEST['array_count'] or max(formats) + 1 != MANIFEST['format_capacity']:
        raise ValueError('unreviewed CLI array/format limits')
    declarations = '\n'.join('extern const uint8_t ' + name + '[];' for name in sorted(arrays))
    definitions = '\n'.join('const uint8_t ' + name + '[] = {' + ','.join(str(byte) for byte in CODEC.encode(text)) + '};'
                            for name, text in sorted(arrays.items()))
    return ('#pragma once\n#include <stddef.h>\n#include <stdint.h>\n'
            'namespace mesh { namespace cli { namespace outputpack {\n'
            'constexpr size_t FormatCapacity = ' + str(MANIFEST['format_capacity']) + ';\n' + declarations
            + '\n#if defined(MESH_STM32_PACKED_CLI_IMPLEMENTATION) && MESH_STM32_PACKED_CLI_IMPLEMENTATION\n'
            + definitions + '\n' + CODEC.cpp_decoder() + '\n#endif\n}}}\n')


def generated_implementation():
    return (ROOT / 'src/helpers/Stm32PackedCliReplies.cpp').read_text(encoding='utf-8')


def install(build_env):
    role = _load('mesh_stm32_packed_role', SCRIPT_DIR / 'nrf52_flash_trim.py')
    flags = role.flag_defines(build_env.get('BUILD_FLAGS', ()), build_env.get('CPPDEFINES', ()))
    if not role._enabled(flags.get(FLAG)):
        return False
    expected_mcu = TARGETS.get(build_env.get('PIOENV'))
    sources = build_env.get('SRC_FILTER')
    if sources is None:
        sources = build_env.GetProjectOption('build_src_filter', '')
    companion = ('../examples/companion_radio/main.cpp', role.COMPANION_SOURCES[0])
    if (not expected_mcu or build_env.PioPlatform().name != 'ststm32'
            or str(build_env.BoardConfig().get('build.mcu', '')).lower() != expected_mcu
            or not role._enabled(flags.get('STM32_PLATFORM'))
            or not role._enabled(flags.get('STM32WL_COMPANION_COMPACT_UNSUPPORTED_AGC'))
            or not role._enabled(flags.get('MESH_CLI_SHARE_REPLY_COPIES'))
            or not all(role.source_selected(sources, source) for source in companion)
            or any(role.source_selected(sources, source) for source in role.ROLE_SOURCES + role.COMPANION_SOURCES[1:])):
        raise ValueError('packed CLI replies require an exact reviewed STM32 Companion')
    root = Path(build_env.subst('$PROJECT_DIR')).resolve()
    if root != ROOT.resolve():
        raise ValueError('packed CLI sources must come from the qualified project')
    directory = Path(build_env.subst('$BUILD_DIR')) / 'stm32-packed-cli'
    directory.mkdir(parents=True, exist_ok=True)
    header = directory / 'Stm32PackedCliRepliesGenerated.h'
    content = generated_header()
    if not header.exists() or header.read_text(encoding='utf-8') != content:
        header.write_text(content, encoding='utf-8')
    build_env.Append(CPPPATH=[str(directory)])

    def replace(env, node):
        original = Path(node.srcnode().get_abspath()).resolve()
        try:
            relative = original.relative_to(root).as_posix()
        except ValueError:
            return node
        if relative not in SOURCES:
            return node
        content = patched_source(relative, original.read_text(encoding='utf-8'))
        destination = directory / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or destination.read_text(encoding='utf-8') != content:
            destination.write_text(content, encoding='utf-8')
        includes = [str(original.parent), *env.get('CPPPATH', ())]
        local = env.Clone(CPPPATH=includes)
        return local.Object(local.File(str(destination)), CPPPATH=includes)[0]

    for name in ('MyMesh.cpp', 'RadioProfileCLI.cpp', 'CommonRadioPrefs.cpp'):
        build_env.AddBuildMiddleware(replace, '*' + name)
    print('STM32 Companion CLI: reviewed byte-identical packed literals')
    return True


if 'Import' in globals():
    Import('env')
    role = _load('mesh_stm32_lifetime_role', SCRIPT_DIR / 'nrf52_flash_trim.py')
    lifetime = _load('mesh_stm32_lifetime', SCRIPT_DIR / 'stm32_mini_program_lifetime.py')
    lifetime.install(env, role)
    install(env)
