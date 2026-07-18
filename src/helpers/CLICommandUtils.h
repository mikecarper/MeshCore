#pragma once

namespace mesh {
namespace cli {

// Mobile keyboards commonly capitalize the first word entered into a CLI
// field. Command verbs are identifiers, so normalize only that first token and
// leave every argument (names, passwords, keys, and other values) untouched.
inline void normalizeCommandVerb(char* command) {
  if (command == nullptr) return;

  while (*command == ' ' || *command == '\t') command++;
  while (*command != 0 && *command != ' ' && *command != '\t') {
    if (*command >= 'A' && *command <= 'Z') {
      *command = static_cast<char>(*command - 'A' + 'a');
    }
    command++;
  }
}

}  // namespace cli
}  // namespace mesh
