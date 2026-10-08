"""Extract original C++ compound bodies without choosing a build configuration.

This is a lexical test helper, not a C++ parser or preprocessor. Conditional
alternatives must agree on brace depth at their join. All alternatives and
directives remain in the returned source for the real compiler to select.
"""

import re


_RAW = re.compile(r'(?:u8|u|U|L)?R"([^\s()\\]{0,16})\(')
_DIRECTIVE = re.compile(r"#\s*([A-Za-z_]\w*)")


def _quoted_end(text, start):
    quote = text[start]
    i = start + 1
    while i < len(text):
        if text[i] == "\\":
            i += 3 if text.startswith("\r\n", i + 1) else 2
        elif text[i] == quote:
            return i + 1
        elif text[i] in "\r\n":
            raise AssertionError("unclosed C++ quoted literal")
        else:
            i += 1
    raise AssertionError("unclosed C++ quoted literal")


def _number_separator(text, i):
    if i + 1 == len(text) or not text[i + 1].isalnum():
        return False
    start = i
    while start and (text[start - 1].isalnum() or text[start - 1] in "_'"):
        start -= 1
    return start < i and text[start].isdigit()


def _line_end(text, start):
    end = text.find("\n", start)
    while end >= 0 and text[max(0, end - 2):end].endswith(("\\", "\\\r")):
        end = text.find("\n", end + 1)
    return len(text) if end < 0 else end + 1


def _block_comment_end(text, start):
    end = text.find("*/", start + 2)
    if end < 0:
        raise AssertionError("unclosed C++ block comment")
    return end + 2


def _raw_end(text, start):
    raw = _RAW.match(text, start) if text[start] in "uULR" else None
    if raw:
        closing = ")" + raw.group(1) + '"'
        end = text.find(closing, raw.end())
        if end < 0:
            raise AssertionError("unclosed C++ raw string literal")
        return end + len(closing)
    if text.startswith('R"', start):
        raise AssertionError("invalid C++ raw string delimiter")
    return None


def _directive_end(text, start):
    # Comments and raw literals may span physical lines inside a directive;
    # only an unescaped newline outside those tokens ends its logical line.
    i = start + 1
    while i < len(text):
        if text.startswith("\\\r\n", i):
            i += 3
        elif text.startswith("\\\n", i):
            i += 2
        elif text[i] in "\r\n":
            return i + (2 if text.startswith("\r\n", i) else 1)
        elif text.startswith("//", i):
            return _line_end(text, i + 2)
        elif text.startswith("/*", i):
            i = _block_comment_end(text, i)
        else:
            raw_end = _raw_end(text, i)
            if raw_end is not None:
                i = raw_end
            elif text[i] == '"' or (text[i] == "'" and not _number_separator(text, i)):
                i = _quoted_end(text, i)
            else:
                i += 1
    return i


def _events(text):
    i = 0
    line_start = True
    while i < len(text):
        start = i
        if text.startswith("\\\n", i) or text.startswith("\\\r\n", i):
            i += 3 if text.startswith("\\\r\n", i) else 2
            yield "ignored", start, i, None
            continue
        if text[i].isspace():
            if text[i] in "\r\n":
                line_start = True
            i += 1
            continue
        if text.startswith("//", i):
            i = _line_end(text, i + 2)
            line_start = True
            yield "ignored", start, i, None
            continue
        if text.startswith("/*", i):
            i = _block_comment_end(text, i)
            if "\n" in text[start:i] or "\r" in text[start:i]:
                line_start = True
            yield "ignored", start, i, None
            continue
        if text[i] == "#" and line_start:
            i = _directive_end(text, i)
            logical = re.sub(r"\\\r?\n", "", text[start:i])
            logical = re.sub(r"/\*.*?\*/", " ", logical, flags=re.DOTALL)
            match = _DIRECTIVE.match(logical)
            yield "directive", start, i, match.group(1) if match else ""
            line_start = True
            continue
        raw_end = _raw_end(text, i)
        if raw_end is not None:
            i = raw_end
            yield "literal", start, i, None
        elif text[i] == '"' or (text[i] == "'" and not _number_separator(text, i)):
            i = _quoted_end(text, i)
            yield "literal", start, i, None
        else:
            i += 1
            yield "brace" if text[start] in "{}" else "code", start, i, text[start]
        line_start = False


def body(text, signature):
    """Return the original signature and complete compound body.

    Preserve the legacy helper's ValueError for a missing signature and
    AssertionError for incomplete or ambiguous bodies. Signatures in comments,
    literals and macro definitions do not select a body.
    """
    if not signature:
        raise ValueError("empty C++ signature")
    start = text.find(signature)
    events = iter(_events(text))
    for kind, begin, end, value in events:
        if start < 0:
            raise ValueError("C++ signature not found: " + signature)
        while begin <= start < end and kind not in ("code", "brace"):
            start = text.find(signature, end)
        if begin <= start < end:
            break
    else:
        raise ValueError("C++ signature not found: " + signature)

    for kind, begin, end, value in events:
        if kind == "brace" and value == "{":
            break
    else:
        raise AssertionError("missing C++ body opening brace")

    depth = 1
    branches = []
    for kind, begin, end, value in events:
        if kind == "directive":
            if value in ("if", "ifdef", "ifndef"):
                branches.append({"entry": depth, "ends": [], "else": False})
            elif value in ("elif", "else"):
                if not branches or branches[-1]["else"]:
                    raise AssertionError("unexpected C++ conditional alternative")
                frame = branches[-1]
                frame["ends"].append(depth)
                frame["else"] = value == "else"
                depth = frame["entry"]
            elif value == "endif":
                if not branches:
                    raise AssertionError("unexpected C++ conditional endif")
                frame = branches.pop()
                frame["ends"].append(depth)
                if not frame["else"]:
                    frame["ends"].append(frame["entry"])
                if len(set(frame["ends"])) != 1:
                    raise AssertionError("C++ conditional alternatives have different brace depths")
                depth = frame["ends"][0]
                if depth == 0 and not branches:
                    return text[start:end]
        elif depth == 0 and kind != "ignored":
            raise AssertionError("code after a conditional C++ body closing brace")
        elif kind == "brace":
            depth += 1 if value == "{" else -1
            if depth < 0:
                raise AssertionError("unexpected C++ closing brace")
            if depth == 0 and not branches:
                return text[start:end]
    raise AssertionError("unclosed C++ body or conditional")
