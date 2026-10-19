"""Extract radio regulator declarations for native production-method harnesses."""
from test_radio_receive_contract import method


def regulator_members(source):
    start = source.index('{', source.index('class Custom')) + 1
    end = source.index('  uint32_t _preambleMillis', start)
    fields = source[start:end]
    methods = '\n'.join(method(source, signature) for signature in (
        'uint8_t regulatorModes(', 'mesh::power::RegulatorMode defaultRegulatorMode(',
        'bool configureBootRegulator(', 'mesh::power::RegulatorMode activeRegulatorMode('))
    return fields + '\n public:\n' + methods
