#pragma once

#include <string.h>

namespace RegionNameUtils {

inline const char* canonical(const char* name) {
  return name != NULL && name[0] == '#' ? name + 1 : name;
}

inline bool equivalent(const char* first, const char* second) {
  return first != NULL && second != NULL
    && strcmp(canonical(first), canonical(second)) == 0;
}

}  // namespace RegionNameUtils
