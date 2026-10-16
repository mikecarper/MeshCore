"""Extract original C++ compound bodies without choosing a build configuration.

This is a lexical test helper, not a C++ parser or preprocessor. Repeated,
normalized conditional expressions share symbolic choices, allowing a guarded
opening brace to be closed by a later identical guard. Other expressions are
opaque atoms, not evaluated C preprocessor arithmetic. All alternatives and
directives remain in the returned source for the real compiler to select.
Reusing a retained choice after a potentially invalidating macro change fails
closed; balanced local default definitions can join and forget their choices.
Expansion dependencies of opaque expressions are deliberately not simulated.
Conditions must be stable feature expressions, not aliases for volatile/location-dependent
macro expansions. Direct __LINE__/__COUNTER__ expressions are rejected too.
"""

import re


_RAW = re.compile(r'(?:u8|u|U|L)?R"([^\s()\\]{0,16})\(')
_DIRECTIVE = re.compile(r"#\s*([A-Za-z_]\w*)")
MAX_CONDITIONAL_PATHS = 64


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


def _uncommented(text):
    """Remove comments without changing literal contents or joining words."""
    cursor, pieces = 0, []
    for kind, begin, finish, _ in _events(text):
        if kind == "ignored":
            pieces.extend((text[cursor:begin], " "))
            cursor = finish
    pieces.append(text[cursor:])
    return "".join(pieces)


def _directive_expression(text, start, end):
    logical = re.sub(r"\\\r?\n", "", text[start:end])
    # Scan after '#' so the lexer can see comments before the keyword too.
    logical = "#" + _uncommented(logical[1:])
    match = _DIRECTIVE.match(logical)
    if not match:
        raise AssertionError("invalid C++ conditional directive")
    return logical[match.end():].strip()


def _outer_operand(expression):
    if expression.startswith("("):
        depth = 0
        for kind, begin, _, value in _events(expression):
            if kind == "code" and value in "()":
                depth += 1 if value == "(" else -1
                if depth == 0:
                    return expression[1:-1].strip() if begin == len(expression) - 1 else None
    return None


def _simple_condition(expression):
    match = re.fullmatch(r"defined\s*(?:\(\s*([A-Za-z_]\w*)\s*\)|([A-Za-z_]\w*))", expression)
    if match:
        return "defined(" + (match[1] or match[2]) + ")"
    if re.fullmatch(r"[A-Za-z_]\w*|(?:0[xX][0-9a-fA-F]+|[0-9]+)[uUlL]*", expression):
        return expression
    return None


def _condition_identifiers(expression):
    pieces, cursor = [], 0
    for kind, begin, end, _ in _events(expression):
        if kind == "literal":
            pieces.extend((expression[cursor:begin], " "))
            cursor = end
    pieces.append(expression[cursor:])
    return set(re.findall(r"\b[A-Za-z_]\w*", "".join(pieces)))


def _condition(text, start, end, directive):
    expression = _directive_expression(text, start, end)
    if directive in ("ifdef", "ifndef"):
        if not re.fullmatch(r"[A-Za-z_]\w*", expression):
            raise AssertionError("invalid C++ conditional macro name")
        expression = "defined(" + expression + ")"
    inverted = directive == "ifndef"
    while expression:
        outer = _outer_operand(expression)
        if outer is not None:
            expression = outer
            continue
        if expression.startswith("!") and not expression.startswith("!="):
            # '!A && B' is not the complement of 'A && B'. Peel only a
            # whole unary operand; arbitrary expressions stay opaque.
            operand = expression[1:].lstrip()
            probe = operand.lstrip("!").lstrip()
            if _simple_condition(probe) is not None or _outer_operand(probe) is not None:
                inverted = not inverted
                expression = operand
                continue
        simple = _simple_condition(expression)
        if simple is not None and simple.startswith("defined("):
            return simple, inverted
        if {"__LINE__", "__COUNTER__"} & _condition_identifiers(expression):
            raise AssertionError("volatile C++ conditional expression is unsupported")
        return simple or expression, inverted
    raise AssertionError("empty C++ conditional expression")


class _Path:
    def __init__(self, depth=1, choices=None, branches=()):
        self.depth = depth
        self.choices = {} if choices is None else choices
        # Each frame is (parent active, previous alternative taken, active).
        self.branches = branches

    def active(self):
        return not self.branches or self.branches[-1][2]


def _conditional_paths(paths, condition, inverted, invalidated, alternative=False):
    result = []
    for path in paths:
        parent, taken = (path.branches[-1][:2] if alternative else (path.active(), False))
        prefix = path.branches[:-1] if alternative else path.branches
        if not parent or taken:
            result.append(_Path(path.depth, path.choices, prefix + ((parent, taken, False),)))
            continue
        if condition in path.choices and condition in invalidated:
            raise AssertionError("C++ macro mutation or directive invalidates a retained conditional choice")
        values = (path.choices[condition],) if condition in path.choices else (False, True)
        for value in values:
            choices = dict(path.choices)
            choices[condition] = value
            active = value != inverted
            result.append(_Path(path.depth, choices, prefix + ((True, active, active),)))
            if len(result) > MAX_CONDITIONAL_PATHS:
                raise AssertionError("C++ conditional path complexity exceeds 64")
    if len(result) > MAX_CONDITIONAL_PATHS:
        raise AssertionError("C++ conditional path complexity exceeds 64")
    return result


def _merge_paths(paths):
    # Forget irrelevant conditions as soon as both possibilities leave the
    # same brace/conditional state. Only unresolved brace effects retain them.
    # Pair and emit cubes in a stable order so conservative complexity limits
    # do not depend on Python's randomized string/set iteration order.
    def choice_order(choice):
        return len(choice), tuple(sorted(choice))

    grouped = {}
    for path in paths:
        grouped.setdefault((path.depth, path.branches), set()).add(frozenset(path.choices.items()))
    result = []
    for (depth, branches), choices in sorted(grouped.items()):
        changed = True
        while changed:
            changed = False
            items = sorted(choices, key=choice_order)
            for index, first in enumerate(items):
                for second in items[index + 1:]:
                    if first <= second or second <= first:
                        choices.discard(second if first <= second else first)
                    elif (len(first ^ second) == 2 and
                          {key for key, _ in first} == {key for key, _ in second}):
                        choices.difference_update((first, second))
                        choices.add(first & second)
                    else:
                        continue
                    changed = True
                    break
                if changed:
                    break
        result.extend(_Path(depth, dict(sorted(choice)), branches)
                      for choice in sorted(choices, key=choice_order))
    return result


def _invalidate_macro_choices(paths, invalidated, text, start, end):
    name = re.match(r"([A-Za-z_]\w*)", _directive_expression(text, start, end))
    if not name:
        raise AssertionError("invalid C++ macro mutation")
    for path in paths:
        if not path.active():
            continue
        for condition in path.choices:
            defined = re.fullmatch(r"defined\(([A-Za-z_]\w*)\)", condition)
            # A numeric/opaque expression may expand through another macro
            # supplied outside this body. Pure defined(NAME) does not expand
            # NAME, so unrelated changes cannot alter that particular atom.
            if defined is None or defined[1] == name[1]:
                invalidated.add(condition)


def body(text, signature):
    """Return the original signature and complete compound body.

    Preserve the legacy helper's ValueError for a missing signature and
    AssertionError for incomplete or ambiguous bodies. Signatures in comments,
    literals and macro definitions do not select a body.
    """
    if not signature.strip():
        raise ValueError("empty C++ signature")
    start = text.find(signature)
    leading = len(signature) - len(signature.lstrip())
    anchor = start + leading if start >= 0 else -1
    events = iter(_events(text))
    for kind, begin, end, value in events:
        if start < 0:
            raise ValueError("C++ signature not found: " + signature)
        while begin <= anchor < end and kind not in ("code", "brace"):
            start = text.find(signature, end)
            anchor = start + leading if start >= 0 else -1
        if begin <= anchor < end:
            break
    else:
        raise ValueError("C++ signature not found: " + signature)

    for kind, begin, end, value in events:
        if kind == "brace" and value == "{":
            break
    else:
        raise AssertionError("missing C++ body opening brace")

    paths = [_Path()]
    branches = []
    invalidated = set()
    for kind, begin, end, value in events:
        if kind == "directive":
            if value in ("if", "ifdef", "ifndef"):
                paths = _conditional_paths(paths, *_condition(text, begin, end, value), invalidated)
                branches.append(False)
            elif value in ("elif", "else"):
                if not branches or branches[-1]:
                    raise AssertionError("unexpected C++ conditional alternative")
                if value == "elif":
                    paths = _conditional_paths(paths, *_condition(text, begin, end, value), invalidated,
                                               alternative=True)
                else:
                    branches[-1] = True
                    for path in paths:
                        parent, taken, _ = path.branches[-1]
                        path.branches = path.branches[:-1] + ((parent, True, parent and not taken),)
            elif value == "endif":
                if not branches:
                    raise AssertionError("unexpected C++ conditional endif")
                branches.pop()
                for path in paths:
                    path.branches = path.branches[:-1]
                paths = _merge_paths(paths)
                # A balanced local #ifndef/#define needs no later correlation.
                # Keep taint only while some outstanding brace/branch effect
                # still depends on that choice; never reuse an invalidated one.
                invalidated.intersection_update(condition for path in paths for condition in path.choices)
            elif value in ("define", "undef"):
                _invalidate_macro_choices(paths, invalidated, text, begin, end)
            elif value in ("include", "include_next", "line") or (value == "pragma" and
                    re.match(r"(?:push_macro|pop_macro)\b", _directive_expression(text, begin, end))):
                invalidated.update(condition for path in paths if path.active() for condition in path.choices)
        elif kind != "ignored":
            for path in paths:
                if not path.active():
                    continue
                if path.depth == 0:
                    raise AssertionError("code after a conditional C++ body closing brace")
                if kind == "brace":
                    path.depth += 1 if value == "{" else -1
                    if path.depth < 0:
                        raise AssertionError("unexpected C++ closing brace")
        if not branches and all(path.depth == 0 for path in paths):
            return text[start:end]
    raise AssertionError("unclosed C++ body or conditional")
