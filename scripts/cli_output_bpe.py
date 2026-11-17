"""Deterministic, bounded byte-pair coding of reviewed ASCII CLI literals.

Tokens 128..255 expand to two earlier non-NUL symbols. A six-level acyclic
dictionary permits a small fixed decoder stack without recursion or allocation.
The source-qualified STM32 build hook supplies immutable, terminated literals.
"""
from collections import Counter
from itertools import groupby

MAX_DEPTH = 6


def _ascii(text):
    data = text.encode('ascii') if isinstance(text, str) else bytes(text)
    if any(not 0 < value < 128 for value in data):
        raise ValueError('Packed literals require non-NUL ASCII')
    return data


class BytePairCodec:
    def __init__(self, pairs):
        self.pairs = tuple(tuple(pair) for pair in pairs)
        if len(self.pairs) > 128:
            raise ValueError('At most 128 dictionary entries')
        depths = [0] * 128
        for token, pair in enumerate(self.pairs, 128):
            if len(pair) != 2 or any(not isinstance(byte, int) or not 0 < byte < token for byte in pair):
                raise ValueError('Each pair must refer only to prior non-NUL symbols')
            depths.append(1 + max(depths[pair[0]], depths[pair[1]]))
        self.max_depth = max(depths)
        if self.max_depth > MAX_DEPTH:
            raise ValueError('Dictionary expansion exceeds the fixed depth bound')

    def encode(self, text, terminated=True):
        values = list(_ascii(text))
        for token, (first, second) in enumerate(self.pairs, 128):
            output = []
            index = 0
            while index < len(values):
                if index + 1 < len(values) and values[index:index + 2] == [first, second]:
                    output.append(token)
                    index += 2
                else:
                    output.append(values[index])
                    index += 1
            values = output
        return bytes(values) + (b'\0' if terminated else b'')

    def decode(self, packed):
        output = bytearray()
        for token in packed:
            if not token:
                break
            pending = [token]
            while pending:
                token = pending.pop()
                if token < 128:
                    output.append(token)
                else:
                    index = token - 128
                    if index >= len(self.pairs):
                        raise ValueError('Unknown dictionary token')
                    left, right = self.pairs[index]
                    pending.extend((right, left))
        return bytes(output)

    def cpp_decoder(self):
        """Return a shared unpack() body, intended inside mesh::packed."""
        dictionary = ',\n'.join('  {' + str(a) + ',' + str(b) + '}' for a, b in self.pairs)
        if not dictionary:
            dictionary = '  {0,0}'
        return '''// Generated immutable BPE dictionary; every child precedes its parent.
static const uint8_t output_dictionary[][2] = {
DICTIONARY
};
#if defined(__GNUC__) && !defined(__clang__)
__attribute__((noinline, noclone))
#elif defined(__GNUC__)
__attribute__((noinline))
#endif
size_t unpack(char* output, size_t capacity, const uint8_t* packed) {
  if (!output) capacity = 0;
  if (!packed) {
    if (capacity) output[0] = 0;
    return 0;
  }
  uint8_t pending[STACK_SIZE];
  unsigned depth = 0;
  size_t length = 0;
  for (uint8_t token; (token = *packed++) != 0;) {
    for (;;) {
      if (token < 128) {
        if (capacity && length < capacity - 1) output[length] = static_cast<char>(token);
        ++length;
        if (!depth) break;
        token = pending[--depth];
      } else {
        const unsigned index = token - 128;
        if (INVALID_TOKEN || depth >= STACK_SIZE) {
          if (capacity) output[length < capacity ? length : capacity - 1] = 0;
          return static_cast<size_t>(-1);
        }
        pending[depth++] = output_dictionary[index][1];
        token = output_dictionary[index][0];
      }
    }
  }
  if (capacity) output[length < capacity ? length : capacity - 1] = 0;
  return length;
}
'''.replace('INVALID_TOKEN', ('index >= ' + str(len(self.pairs))) if self.pairs else 'true').replace(
            'DICTIONARY', dictionary).replace('STACK_SIZE', str(max(1, self.max_depth)))


def train(texts, max_pairs=128, max_depth=6):
    if not 0 <= max_pairs <= 128 or not 1 <= max_depth <= MAX_DEPTH:
        raise ValueError('Invalid dictionary bounds')
    sequences = [list(_ascii(text)) for text in texts]
    depths = [0] * 128
    pairs = []
    for token in range(128, 128 + max_pairs):
        counts = Counter(pair for sequence in sequences for pair in zip(sequence, sequence[1:]))
        # Same-symbol runs have overlapping bigrams: count actual replacements.
        for pair in list(counts):
            if pair[0] == pair[1]:
                counts[pair] = 0
        for sequence in sequences:
            for value, group in groupby(sequence):
                count = sum(1 for _ in group)
                if count >= 2:
                    counts[value, value] += count // 2
        candidates = [(count, -(1 + max(depths[a], depths[b])), -a, -b, a, b)
                      for (a, b), count in counts.items()
                      if count >= 3 and 1 + max(depths[a], depths[b]) <= max_depth]
        if not candidates:
            break
        _, _, _, _, first, second = max(candidates)
        pairs.append((first, second))
        depths.append(1 + max(depths[first], depths[second]))
        replaced = []
        for sequence in sequences:
            output = []
            index = 0
            while index < len(sequence):
                if index + 1 < len(sequence) and sequence[index:index + 2] == [first, second]:
                    output.append(token)
                    index += 2
                else:
                    output.append(sequence[index])
                    index += 1
            replaced.append(output)
        sequences = replaced
    return BytePairCodec(pairs)
