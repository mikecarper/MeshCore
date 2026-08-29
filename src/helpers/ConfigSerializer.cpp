#include "ConfigSerializer.h"

bool ConfigSerializer::saveSerial(Stream& s) {
  Context context(&s, OP::WRITE);
  _context = &context;  // set the context for structure() call
  s.print("{");   // root object
  _first = true;
  structure();
  if (s.print("}") != 1) context.success = false;  // failure detect
  _context = NULL;
  return context.success;
}

#define TOK_ERROR     -1
#define TOK_EOF        0
#define TOK_KEY        1
#define TOK_VALUE      2
#define TOK_START_OBJ  3
#define TOK_END_OBJ    4
#define TOK_WHITESPACE 5

static bool is_whitespace(char c) {
  return c == ' ' || c == '\t' || c == '\r' || c == '\n';
}
static bool is_key_start_char(char c) {
  return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c == '_';
}
static bool is_key_char(char c) {
  return is_key_start_char(c) || (c >= '0' && c <= '9');
}
static bool is_value_char(char c) {
  return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || c == '-' || c == '.';
}

// ConfigSerializer writes floating-point preferences as fixed-point decimal
// text, and its tokenizer intentionally accepts neither exponent notation nor
// a leading '+'.  Parsing that small grammar locally avoids linking Newlib's
// general-purpose strtod implementation (and its locale/bignum support) into
// flash-constrained firmware.
static double parse_fixed_decimal(const char* text) {
  bool negative = false;
  if (*text == '-') {
    negative = true;
    ++text;
  }

  double value = 0.0;
  while (*text >= '0' && *text <= '9') {
    value = value * 10.0 + (*text++ - '0');
  }

  if (*text == '.') {
    double place = 0.1;
    while (*++text >= '0' && *text <= '9') {
      value += (*text - '0') * place;
      place *= 0.1;
    }
  }

  return negative ? -value : value;
}

#define EXPECT_OPEN_BRACE   0
#define EXPECT_KEY_OR_CLOSE 1
#define EXPECT_KEY          2
#define EXPECT_VAL_OR_OBJ   3
#define EXPECT_STRING_VAL   4
#define EXPECT_STRING_ESCAPE   5
#define EXPECT_COMMA_OR_CLOSE  6

int ConfigSerializer::Context::readNext() {
  char c;
  if (pending) {
    c = pending;
    pending = 0;
  } else {
    if (_f->available() == 0) return TOK_EOF;

    int n = _f->read();
    if (n < 0) return TOK_EOF;
    c = (char)n;
  }

  if (rd_root_complete) {
    return is_whitespace(c) ? TOK_WHITESPACE : TOK_ERROR;
  }

  switch (rd_mode) {
    case EXPECT_OPEN_BRACE:
      if (c == '{') { rd_mode = EXPECT_KEY_OR_CLOSE; return TOK_START_OBJ; }
      if (is_whitespace(c)) return TOK_WHITESPACE;
      return TOK_ERROR;

    case EXPECT_KEY_OR_CLOSE:
      // Empty objects are valid and are emitted by serializers such as the
      // dynamic `custom` preferences object before it has any entries.
      if (rd_len == 0 && c == '}') {
        rd_mode = EXPECT_COMMA_OR_CLOSE;
        return TOK_END_OBJ;
      }
      // A non-empty object follows the same key grammar, but only this state
      // (entered directly after '{') permits an immediate closing brace.
      // fall through
    case EXPECT_KEY:
      if (rd_len > 0 && c == ':') { rd_buf[rd_len] = 0; rd_len = 0; rd_mode = EXPECT_VAL_OR_OBJ; return TOK_KEY; }
      if (rd_len == 0 && is_whitespace(c)) return TOK_WHITESPACE;
      if (rd_len < CONFIG_MAX_KEYLEN-1 &&
          ((rd_len == 0 && is_key_start_char(c)) ||
           (rd_len > 0 && is_key_char(c)))) {
        rd_buf[rd_len++] = c;
        return TOK_WHITESPACE;
      }
      return TOK_ERROR;

    case EXPECT_VAL_OR_OBJ:
      if (rd_len == 0 && is_whitespace(c)) return TOK_WHITESPACE;
      if (rd_len == 0 && c == '"') {
        rd_token_quoted = true;
        rd_mode = EXPECT_STRING_VAL;
        return TOK_WHITESPACE;
      }
      if (rd_len == 0 && c == '{') { rd_mode = EXPECT_KEY_OR_CLOSE; return TOK_START_OBJ; }
      if (is_value_char(c) && rd_len < CONFIG_MAX_TOKEN_LEN-1) {
        if (rd_len == 0) rd_token_quoted = false;
        rd_buf[rd_len++] = c;
        return TOK_WHITESPACE;
      }
      if (rd_len > 0 && (c == ',' || c == '}' || is_whitespace(c))) { pending = c; rd_buf[rd_len] = 0; rd_len = 0; rd_mode = EXPECT_COMMA_OR_CLOSE; return TOK_VALUE;  }
      return TOK_ERROR;

    case EXPECT_STRING_ESCAPE:
      if ((c == 'n') && rd_len < CONFIG_MAX_TOKEN_LEN-1) { rd_buf[rd_len++] = '\n'; rd_mode = EXPECT_STRING_VAL; return TOK_WHITESPACE; }
      if ((c == 'r') && rd_len < CONFIG_MAX_TOKEN_LEN-1) { rd_buf[rd_len++] = '\r'; rd_mode = EXPECT_STRING_VAL; return TOK_WHITESPACE; }
      if ((c == '"' || c == '\\' || c == '/') && rd_len < CONFIG_MAX_TOKEN_LEN-1) { rd_buf[rd_len++] = c; rd_mode = EXPECT_STRING_VAL; return TOK_WHITESPACE; }
      return TOK_ERROR;  // unsupport escape

    case EXPECT_STRING_VAL:
      if (c == '"') { rd_buf[rd_len] = 0; rd_len = 0; rd_mode = EXPECT_COMMA_OR_CLOSE; return TOK_VALUE; }
      if (c == '\\') { rd_mode = EXPECT_STRING_ESCAPE; return TOK_WHITESPACE; }
      if (c == '\0') return TOK_ERROR;  // never silently truncate a persisted string
      if (rd_len < CONFIG_MAX_TOKEN_LEN-1) { rd_buf[rd_len++] = c; return TOK_WHITESPACE; }
      return TOK_ERROR;

    case EXPECT_COMMA_OR_CLOSE:
      if (c == ',') { rd_mode = EXPECT_KEY; return TOK_WHITESPACE; }
      if (c == '}') { rd_mode = EXPECT_COMMA_OR_CLOSE; return TOK_END_OBJ; }
      if (is_whitespace(c)) return TOK_WHITESPACE;
      return TOK_ERROR;
  }
  return TOK_ERROR;   // unknown mode
}

bool ConfigSerializer::loadSerial(Stream& s) {
  Context context(&s, OP::READ);
  _context = &context;  // set the context for structure() call
  uint8_t sp = 0;   // object nesting stack pointer
  int next_tok;

  // parse the Json file
  while ((next_tok = context.readNext()) > TOK_EOF) {
    if (next_tok == TOK_KEY) {
      context.setKey(sp, context.getToken());
    } else if (next_tok == TOK_VALUE) {
      context.setValueEvent(sp, false);
      _depth = 1;  // re-run the structure() hierarchy again (looking for specific key, at specific depth)
      structure();
    } else if (next_tok == TOK_START_OBJ) {
      // The root has no key. For every nested object, rerun the schema at the
      // parent depth so known scalar fields can reject an object value and
      // known object fields can confirm their expected shape.
      if (sp > 0) {
        context.setValueEvent(sp, true);
        _depth = 1;
        structure();
        if (!context.success) break;
      }
      if (sp < CONFIG_MAX_DEPTH - 1) {
        sp++;
      } else {
        //Serial.printf("Error: max nesting reached"); // TODO: debug logging
        context.success = false;
        break;
      }
    } else if (next_tok == TOK_END_OBJ) {
      if (sp > 0) {
        sp--;
        if (sp == 0) context.markRootComplete();
      } else {
        //Serial.printf("Error: too many closing '}'"); // TODO: debug logging
        context.success = false;
        break;
      }
    }
  }
  if (sp != 0 || !context.rootComplete() || next_tok == TOK_ERROR) {
    context.success = false;   // missing/unmatched root object, or other parse error
  }
  _context = NULL;
  return context.success;
}

void ConfigSerializer::writeComma() {
  if (_first) {
    _first = false;
  } else {
    _context->file()->print(",");  // comma separated properties
  }
}

#include <Utils.h>

void ConfigSerializer::def(const char* key, void* value, size_t len) {
  if (_context->op() == OP::WRITE) {
    writeComma();
    _context->file()->print(key);
    _context->file()->print(":\"");
    mesh::Utils::printHex(*_context->file(), (uint8_t*) value, len);
    _context->file()->print("\"");
  } else {
    if (_context->keyMatch(_depth, key)) {
      memset(value, 0, len);
      mesh::Utils::fromHex((uint8_t *)value, len, _context->getToken());
    }
  }
}

void ConfigSerializer::def(const char* key, char* value, size_t max_len) {
  if (_context->op() == OP::WRITE) {
    writeComma();
    _context->file()->print(key);
    _context->file()->print(":\"");
    char c;
    while ((c = *value++) != 0) {  // TODO: handle UTF-8 encoding
      if (c == '"') {
        _context->file()->print("\\\"");
      } else if (c == '\\') {
        _context->file()->print("\\\\");
      } else if (c == '\n') {
        _context->file()->print("\\n");
      } else if (c == '\r') {
        _context->file()->print("\\r");
      } else {
        _context->file()->print(c);
      }
    }
    _context->file()->print("\"");
  } else {
    if (_context->keyMatch(_depth, key)) {
      strncpy(value, _context->getToken(), max_len - 1);
      value[max_len - 1] = 0;
    }
  }
}

void ConfigSerializer::def(const char* key, int32_t& value) {
  if (_context->op() == OP::WRITE) {
    writeComma();
    _context->file()->print(key);
    _context->file()->print(":");
    _context->file()->print(value);
  } else {
    if (_context->keyMatch(_depth, key)) {
      value = atol(_context->getToken());
    }
  }
}

void ConfigSerializer::def(const char* key, uint32_t& value) {
  if (_context->op() == OP::WRITE) {
    writeComma();
    _context->file()->print(key);
    _context->file()->print(":");
    _context->file()->print(value);
  } else {
    if (_context->keyMatch(_depth, key)) {
      value = atol(_context->getToken());
    }
  }
}

void ConfigSerializer::def(const char* key, int16_t& value) {
  if (_context->op() == OP::WRITE) {
    writeComma();
    _context->file()->print(key);
    _context->file()->print(":");
    _context->file()->print((int32_t) value, 10);
  } else {
    if (_context->keyMatch(_depth, key)) {
      value = atol(_context->getToken());
    }
  }
}

void ConfigSerializer::def(const char* key, uint16_t& value) {
  if (_context->op() == OP::WRITE) {
    writeComma();
    _context->file()->print(key);
    _context->file()->print(":");
    _context->file()->print((uint32_t) value, 10);
  } else {
    if (_context->keyMatch(_depth, key)) {
      value = atoi(_context->getToken());
    }
  }
}

void ConfigSerializer::def(const char* key, uint8_t& value) {
  if (_context->op() == OP::WRITE) {
    writeComma();
    _context->file()->print(key);
    _context->file()->print(":");
    _context->file()->print((uint32_t) value, 10);
  } else {
    if (_context->keyMatch(_depth, key)) {
      value = atoi(_context->getToken());
    }
  }
}

void ConfigSerializer::def(const char* key, int8_t& value) {
  if (_context->op() == OP::WRITE) {
    writeComma();
    _context->file()->print(key);
    _context->file()->print(":");
    _context->file()->print((int32_t) value, 10);
  } else {
    if (_context->keyMatch(_depth, key)) {
      value = atoi(_context->getToken());
    }
  }
}

void ConfigSerializer::def(const char* key, bool& value) {
  if (_context->op() == OP::WRITE) {
    writeComma();
    _context->file()->print(key);
    _context->file()->print(":");
    _context->file()->print(value ? "true" : "false");
  } else {
    if (_context->keyMatch(_depth, key)) {
      value = strcmp(_context->getToken(), "true") == 0 || atoi(_context->getToken()) != 0;  // 'true' or a non-zero number
    }
  }  
}

void ConfigSerializer::def(const char* key, double& value) {
  if (_context->op() == OP::WRITE) {
    writeComma();
    _context->file()->print(key);
    _context->file()->print(":");
    if (value == 0.0) {
      _context->file()->print("0");  // shorter encoding
    } else {
      _context->file()->print(value, 6);  // REVISIT: how many dec places?
    }
  } else {
    if (_context->keyMatch(_depth, key)) {
      value = parse_fixed_decimal(_context->getToken());
    }
  }
}

void ConfigSerializer::def(const char* key, float& value) {
  if (_context->op() == OP::WRITE) {
    writeComma();
    _context->file()->print(key);
    _context->file()->print(":");
    if (value == 0.0f) {
      _context->file()->print("0");  // shorter encoding
    } else {
      _context->file()->print(value, 4);  // REVISIT: how many dec places?
    }
  } else {
    if (_context->keyMatch(_depth, key)) {
      value = (float) parse_fixed_decimal(_context->getToken());
    }
  }
}

void ConfigSerializer::def(const char* key, ConfigSerializer& sub_obj) {
  if (_context->op() == OP::WRITE) {
    writeComma();
    _context->file()->print(key);
    _context->file()->print(":{");
    sub_obj._context = _context;  // inherit the Context
    sub_obj._first = true;
    sub_obj.structure();   // recurse into sub object
    if (_context->file()->print("}") != 1) _context->success = false;  // failure detect
  } else {
    if (_context->keyMatch(_depth, key)) {
      if (_context->objectStart() && _context->valueDepth() == _depth) {
        return;  // the object itself; descendant values recurse below
      }
      if (_context->valueDepth() <= _depth) {
        _context->success = false;  // known object supplied as a scalar
        return;
      }
      sub_obj._context = _context;  // inherit the Context
      sub_obj._depth = _depth + 1;
      sub_obj.structure();   // recurse into sub object
    }
  }
}

bool ConfigSerializer::defStrict(const char* key, char* value, size_t max_len,
                                 bool& seen) {
  if (_context->op() == OP::WRITE) {
    def(key, value, max_len);
    return true;
  }
  if (!_context->keyMatch(_depth, key)) return false;
  if (seen || max_len == 0 || _context->objectStart() ||
      _context->valueDepth() != _depth || !_context->tokenQuoted()) {
    _context->success = false;
    return false;
  }
  seen = true;
  const char* token = _context->getToken();
  const size_t len = strlen(token);
  if (len >= max_len) {
    _context->success = false;
    return false;
  }
  memcpy(value, token, len + 1);
  return true;
}

bool ConfigSerializer::defStrict(const char* key, int32_t& value, bool& seen) {
  if (_context->op() == OP::WRITE) {
    def(key, value);
    return true;
  }
  if (!_context->keyMatch(_depth, key)) return false;
  if (seen || _context->objectStart() || _context->valueDepth() != _depth ||
      _context->tokenQuoted()) {
    _context->success = false;
    return false;
  }
  seen = true;

  const char* token = _context->getToken();
  bool negative = false;
  if (*token == '-') {
    negative = true;
    ++token;
  }
  if (*token < '0' || *token > '9') {
    _context->success = false;
    return false;
  }

  // Accumulate the magnitude as unsigned so INT32_MIN remains representable.
  const uint32_t limit = negative ? 2147483648UL : 2147483647UL;
  uint32_t magnitude = 0;
  do {
    const uint32_t digit = static_cast<uint32_t>(*token++ - '0');
    if (magnitude > (limit - digit) / 10U) {
      _context->success = false;
      return false;
    }
    magnitude = magnitude * 10U + digit;
  } while (*token >= '0' && *token <= '9');

  if (*token != '\0') {
    _context->success = false;
    return false;
  }
  if (negative && magnitude == 2147483648UL) {
    value = INT32_MIN;
  } else {
    value = negative ? -static_cast<int32_t>(magnitude)
                     : static_cast<int32_t>(magnitude);
  }
  return true;
}

bool ConfigSerializer::defStrict(const char* key, ConfigSerializer& sub_obj,
                                 bool& seen) {
  if (_context->op() == OP::WRITE) {
    def(key, sub_obj);
    return true;
  }
  if (!_context->keyMatch(_depth, key)) return false;

  if (_context->objectStart() && _context->valueDepth() == _depth) {
    if (seen) {
      _context->success = false;
      return false;
    }
    seen = true;
    return true;
  }
  if (!seen || _context->valueDepth() <= _depth) {
    _context->success = false;
    return false;
  }

  sub_obj._context = _context;
  sub_obj._depth = _depth + 1;
  sub_obj.structure();
  return true;
}
