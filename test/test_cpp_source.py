#!/usr/bin/env python3
"""Qualify the shared C++ extractor against real and conditional source."""

from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from cpp_source import MAX_CONDITIONAL_PATHS, body
from test_radio_receive_contract import method


ROOT = Path(__file__).resolve().parents[1]


class CppSourceTests(unittest.TestCase):
    def test_stops_at_the_actual_body_boundary(self):
        function = "void target() { if (true) { call(); } }"
        self.assertEqual(body(function + "\nvoid next() {}", "void target()"), function)

    def test_leading_whitespace_signature_is_exact_and_preserved(self):
        function = "\n  void target() { work(); }"
        source = "void before() {}" + function + "\nvoid next() {}"
        for extract in (body, method):
            with self.subTest(extractor=extract.__name__):
                self.assertEqual(extract(source, "\n  void target()"), function)
                with self.assertRaises(ValueError):
                    extract("void target() {}", "\nvoid target()")
                with self.assertRaises(ValueError):
                    extract(source, " \n\t")

    def test_leading_whitespace_signature_in_a_comment_cannot_select_a_body(self):
        source = "/*\nvoid target() { wrong(); }\n*/\nvoid target() { correct(); }"
        expected = "\nvoid target() { correct(); }"
        for extract in (body, method):
            with self.subTest(extractor=extract.__name__):
                self.assertEqual(extract(source, "\nvoid target()"), expected)

    def test_preserves_legacy_if_and_class_extraction(self):
        source = 'if (strcmp(config, "powersaving on") == 0) { set(true); } else { set(false); }'
        self.assertEqual(body(source, 'if (strcmp(config, "powersaving on") == 0'), source.split(" else")[0])
        self.assertEqual(body("class Stream { int read() { return 0; } };", "class Stream"),
                         "class Stream { int read() { return 0; } }")

    def test_ignores_comments_strings_chars_and_raw_strings(self):
        function = r'''void target(const char* value = "{") {
  // }}} #else
  /* { #endif
     } */
  const char* text = "escaped \" quote } {";
  char close = '}'; char escaped = '\'';
  const char* raw = u8R"mark({ } // /* "
#else
void target() { }}
)mark";
  unsigned number = 0xff'aa; unsigned decimal = 1'000'000;
}'''
        self.assertEqual(body(function + "\nvoid next() {}", "void target("), function)

    def test_signature_in_comment_literal_or_macro_does_not_select_a_body(self):
        prefix = '''// void target() { }
/* void target() { } */
const char* example = "void target() { }";
#define EXAMPLE void target() { }
'''
        actual = "void target() { work(); }"
        self.assertEqual(body(prefix + actual, "void target()"), actual)

    def test_escaped_newlines_keep_comments_and_literals_lexical(self):
        function = 'void target() {\n// } \\\n} still a comment\nconst char* x = "} \\\r\n {";\n}'
        self.assertEqual(body(function + "\nvoid next() {}", "void target()"), function)

    def test_macro_braces_and_continuations_are_not_body_braces(self):
        function = '''void target() {
#define OPEN { \\
  { }
#define CLOSE }
  work();
}'''
        self.assertEqual(body(function + "\nvoid next() {}", "void target()"), function)

    def test_nested_conditional_openings_share_the_common_close(self):
        function = '''void target() {
/* prefix */ #if OUTER
#if A
  if (first) {
#elif B
  if (second) {
#else
  if (third) {
#endif
#else
  if (fallback) {
#endif
    work();
  }
}'''
        self.assertEqual(body(function + "\nvoid next() {}", "void target()"), function)

    def test_multiline_macro_comments_and_raw_literals_hide_their_braces(self):
        function = '''void target() {
#define COMMENT /* comment
} } #endif
*/ 1
#define LITERAL R"tag(
} } #else
)tag"
# /* directive comment
} } */ if ENABLED
  if (first) {
#else
  if (second) {
#endif
    work();
  }
}'''
        self.assertEqual(body(function + "\nvoid next() {}", "void target()"), function)

    def test_conditional_closings_retain_the_endif_boundary(self):
        function = "void target() {\n#if A\n}\n#else\n}\n#endif\n"
        self.assertEqual(body(function + "void next() {}", "void target()"), function)

    def test_optional_balanced_blocks_join_the_implicit_else(self):
        function = "void target() {\n#if A\n  if (first) { work(); }\n#endif\n}"
        self.assertEqual(body(function + "\nvoid next() {}", "void target()"), function)

    def test_separated_feature_guards_rejoin_without_exponential_history(self):
        function = "void target() {\n"
        # Sequential balanced guards must not retain every historical flag.
        for index in range(100):
            function += f"#if FEATURE_{index}\n  if (ready) {{ work(); }}\n#endif\n"
        # Six independently guarded opens require exactly 64 live paths.
        for index in range(6):
            function += f"#ifdef FEATURE_{index}\n  if (ready) {{\n#endif\n"
        function += "    work();\n"
        for index in reversed(range(6)):
            function += f"#if defined ( FEATURE_{index} )\n  }}\n#endif\n"
        function += "}"
        self.assertEqual(body(function + "\nvoid next() {}", "void target()"), function)
        self.assertEqual(MAX_CONDITIONAL_PATHS, 64)
        excessive = function.replace("    work();\n", "#if EXTRA\nif (ready) {\n#endif\n    work();\n", 1)
        with self.assertRaisesRegex(AssertionError, "complexity exceeds 64"):
            body(excessive, "void target()")

    def test_opaque_identity_and_unary_precedence_cannot_create_false_correlations(self):
        pairs = (
            ("!A && B", "!(A && B)"),
            ("A B", "AB"),
            ("'a b' == VALUE", "'ab' == VALUE"),
            ("'defined A' == VALUE", "'defined(A)' == VALUE"),
            ("FLAG", "defined(FLAG)"),
        )
        for opening, closing in pairs:
            source = ("void target() {\n#if " + opening + "\nif (ready) {\n#endif\nwork();\n#if " +
                      closing + "\n}\n#endif\n}\nvoid next() {}")
            with self.subTest(opening=opening, closing=closing), self.assertRaises(AssertionError):
                body(source, "void target()")

    def test_symbolic_merge_and_body_are_reproducible_across_hash_seeds(self):
        script = r'''
from itertools import product
from cpp_source import _Path, _merge_paths, body

flags = ("FEATURE_A", "FEATURE_B", "FEATURE_C")
paths = [_Path(choices=dict(zip(flags, values)))
         for values in product((False, True), repeat=len(flags))]
merged = _merge_paths(paths)
assert len(merged) == 1 and merged[0].choices == {}, merged
source = "void target() {\n"
for flag in flags:
    source += "#ifdef " + flag + "\nif (ready) {\n#endif\n"
source += "work();\n"
for flag in reversed(flags):
    source += "#if defined(" + flag + ")\n}\n#endif\n"
source += "}"
assert body(source + "\nvoid next() { missing_symbol(); }", "void target()") == source
print([(path.depth, sorted(path.choices.items()), path.branches) for path in merged])
'''
        outputs = []
        for seed in (0, 1, 2, 3, 7, 11, 42, 123):
            with self.subTest(seed=seed):
                checked = subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT / "test",
                                         env=dict(os.environ, PYTHONHASHSEED=str(seed)),
                                         capture_output=True, text=True, timeout=10)
                self.assertEqual(checked.returncode, 0, checked.stderr)
                outputs.append(checked.stdout)
        self.assertEqual(set(outputs), {"[(1, [], ())]\n"})

    def test_macro_mutations_cannot_reuse_a_retained_guard(self):
        mutations = ("#undef A", "#define A 0", "#include <unknown.h>",
                     "#include_next <unknown.h>", "#line 20",
                     '#pragma push_macro("A")', '#pragma pop_macro("A")')
        for mutation in mutations:
            source = ("void target() {\n#ifdef A\nif (ready) {\n#endif\n" + mutation +
                      "\n#ifdef A\n}\n#endif\n}")
            with self.subTest(mutation=mutation), self.assertRaisesRegex(AssertionError, "invalidate"):
                body(source, "void target()")
        # Opaque A may expand through B outside this body. Do not pretend a
        # token-only dependency scan can prove an unrelated mutation safe.
        alias = ("#define A B\nvoid target() {\n#if A\nif (ready) {\n#endif\n#undef B\n"
                 "#define B 0\n#if A\n}\n#endif\n}")
        with self.assertRaisesRegex(AssertionError, "macro mutation"):
            body(alias, "void target()")
        # Defining B cannot alter whether A is defined. A mutation in the
        # known-inactive arm of the retained A guard is ignored as in C++.
        safe = ("void target() {\n#ifdef A\nif (ready) {\n#endif\n#define B 1\n"
                "#ifdef A\n#ifndef A\n#undef A\n#endif\n}\n#endif\n}")
        self.assertEqual(body(safe, "void target()"), safe)

    def test_volatile_condition_values_fail_closed_but_definedness_is_stable(self):
        for value in ("__LINE__", "(__COUNTER__ > 1)", "!__LINE__"):
            source = "void target() {\n#if " + value + "\nwork();\n#endif\n}"
            with self.subTest(value=value), self.assertRaisesRegex(AssertionError, "volatile"):
                body(source, "void target()")
        source = "void target() {\n#ifdef __LINE__\nif (ready) {\n#endif\nwork();\n#if defined(__LINE__)\n}\n#endif\n}"
        self.assertEqual(body(source, "void target()"), source)

    def test_balanced_local_macro_defaults_can_join_before_future_guards(self):
        source = '''void target() {
#if defined(RXEN) || defined(TXEN)
#ifndef RXEN
#define RXEN -1
#endif
#ifndef TXEN
#define TXEN -1
#endif
  use_pins(RXEN, TXEN);
#endif
#if defined(RXEN) || defined(TXEN)
  use_pins(RXEN, TXEN);
#endif
}'''
        for extract in (body, method):
            with self.subTest(extractor=extract.__name__):
                self.assertEqual(extract(source + "\nvoid next() {}", "void target()"), source)

    def test_malformed_or_ambiguous_sources_fail_explicitly(self):
        malformed = (
            "void target() {",
            "void target() { /* }",
            "void target() {\n#define VALUE /* }\n}",
            'void target() {\n#define VALUE R"tag(}\n}',
            'void target() { "}',
            "void target() { '}",
            'void target() { R"tag(})other"; }',
            'void target() { R"bad delimiter(})bad delimiter"; }',
            "void target() {\n#if A\n}",
            "void target() {\n#else\n}",
            "void target() {\n#endif\n}",
            "void target() {\n#if A\n#else\n#else\n#endif\n}",
            "void target() {\n#if A\n#else\n#elif B\n#endif\n}",
            "void target() {\n#if A\nif (a) {\n#else\n#endif\n}}",
            "void target() {\n#if A\nif (a) {\n#endif\n}}",
            "void target() {\n#if A\n}\nvoid hidden() {}\n#else\n}\n#endif",
        )
        for source in malformed:
            with self.subTest(source=source), self.assertRaises(AssertionError):
                body(source, "void target()")

    def test_missing_signature_and_opening_brace_keep_the_api_errors(self):
        with self.assertRaises(ValueError):
            body("void other() {}", "void target()")
        with self.assertRaises(ValueError):
            body("void target() {}", "")
        with self.assertRaises(AssertionError):
            body("void target();", "void target()")

    def test_actual_repeater_command_pump_with_both_ethernet_alternatives(self):
        source = (ROOT / "examples/simple_repeater/main.cpp").read_text()
        signature = "static void __attribute__((noinline)) serviceCommandInterfaces()"
        extracted = body(source, signature)
        expected = source[source.index(signature):source.index("\nstatic bool usbLoggingRecoverySafe")].rstrip()
        self.assertEqual(extracted, expected)
        self.assertIn("!ethernet_get_enabled()", extracted)
        self.assertIn("!ethernet_client.connected()", extracted)
        self.assertNotIn("usbLoggingRecoverySafe", extracted)

    def test_legacy_receive_extractor_retains_the_complete_actual_room_loop(self):
        source = (ROOT / "examples/simple_room_server/main.cpp").read_text()
        signature = "\nvoid loop() {"
        expected = source[source.index(signature):].rstrip()
        # The raw-brace counter previously ran off this source because both
        # preprocessor alternatives open an if, but share one closing brace.
        source += "\nvoid must_not_be_extracted() { missing_symbol(); }\n"
        for extract in (body, method):
            with self.subTest(extractor=extract.__name__):
                extracted = extract(source, signature)
                self.assertEqual(extracted, expected)
                self.assertIn("!ethernet_get_enabled()", extracted)
                self.assertIn("!ethernet_client.connected()", extracted)
                self.assertIn("mesh::serviceUsbLoggingWatchdog", extracted)
                self.assertNotIn("must_not_be_extracted", extracted)

    def test_actual_setup_begin_and_radio_init_keep_correlated_guard_boundaries(self):
        cases = (
            ("examples/companion_radio/main.cpp", "\nvoid setup()", "\nstatic void serviceUsbLoggingOnlySession"),
            ("examples/companion_radio/MyMesh.cpp", "void MyMesh::begin(", "\nvoid MyMesh::configureRadioFromPrefs("),
            ("src/helpers/radiolib/CustomSX1262.h", "bool std_init(SPIClass* spi = NULL)",
             "\n    int16_t startReceiveDutyCycle("),
        )
        for filename, signature, following in cases:
            source = (ROOT / filename).read_text()
            start = source.index(signature)
            expected = source[start:source.index(following, start)].rstrip()
            isolated = expected + "\nvoid must_not_be_extracted() { missing_symbol(); }"
            for extract in (body, method):
                with self.subTest(filename=filename, extractor=extract.__name__):
                    self.assertEqual(extract(source, signature), expected)
                    self.assertEqual(extract(isolated, signature), expected)
                    self.assertEqual(extract(expected, signature), expected)

    def test_separated_nested_inverted_and_elif_guards_compile_in_every_configuration(self):
        source = '''int target(bool condition) {
  int value = 0;
  (void)condition;
#ifdef EXCLUSIVE
  if (condition) {
#endif
    value += 1;
#if (defined ( EXCLUSIVE ))
  }
#endif
#ifndef SECOND
  if (condition) {
#endif
    value += 2;
#if !defined SECOND
  }
#endif
#ifdef OUTER
#ifndef INNER
  if (condition) {
#endif
#endif
    value += 4;
#if defined(OUTER)
#if !(defined(INNER))
  }
#endif
#endif
#ifdef EXCLUSIVE
  if (condition) {
#elif defined(SECOND)
  if (!condition) {
#endif
    value += 8;
#if defined EXCLUSIVE
  }
#elif defined (SECOND)
  }
#endif
  return value;
}'''
        main = r'''
int main() {
  for (int c = 0; c < 2; ++c) {
    int expected = 0;
#ifdef EXCLUSIVE
    if (c) expected += 1;
#else
    expected += 1;
#endif
#ifndef SECOND
    if (c) expected += 2;
#else
    expected += 2;
#endif
#if defined(OUTER) && !defined(INNER)
    if (c) expected += 4;
#else
    expected += 4;
#endif
#ifdef EXCLUSIVE
    if (c) expected += 8;
#elif defined(SECOND)
    if (!c) expected += 8;
#else
    expected += 8;
#endif
    assert(target(c != 0) == expected);
  }
}
'''
        with tempfile.TemporaryDirectory() as directory:
            cpp, binary = Path(directory) / "split.cpp", Path(directory) / "split"
            for extract in (body, method):
                extracted = extract(source + "\nvoid next() { missing_symbol(); }", "int target(")
                self.assertEqual(extracted, source)
                cpp.write_text("#include <cassert>\n" + extracted + main)
                for mask in range(16):
                    defines = ["-D" + name for bit, name in enumerate(("EXCLUSIVE", "SECOND", "OUTER", "INNER"))
                               if mask & (1 << bit)]
                    with self.subTest(extractor=extract.__name__, defines=defines):
                        compiled = subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", *defines,
                                                   str(cpp), "-o", str(binary)],
                                                  capture_output=True, text=True, timeout=30)
                        self.assertEqual(compiled.returncode, 0, compiled.stderr)
                        checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_both_real_conditional_forms_compile_without_firmware_changes(self):
        source = '''int target(bool condition) {
  int value = 0;
  (void)condition;
#ifdef ETHERNET_ENABLED
#if defined(RAK4631_COMBINED_ETHERNET)
  if (condition) {
#else
  if (!condition) {
#endif
    value = 3;
  }
#endif
  return value;
}
void must_not_be_extracted() { missing_symbol(); }
'''
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / "conditional.cpp"
            for conditional in ("RAK4631_COMBINED_ETHERNET", "OTA_RAK15002_SD_STORE"):
                actual = source.replace("RAK4631_COMBINED_ETHERNET", conditional)
                for extract in (body, method):
                    extracted = extract(actual, "int target(")
                    cpp.write_text("#include <cassert>\n" + extracted + r'''
int main() {
#if !defined(ETHERNET_ENABLED)
  assert(target(false) == 0 && target(true) == 0);
#elif defined(TEST_COMBINED_BRANCH)
  assert(target(false) == 0 && target(true) == 3);
#else
  assert(target(false) == 3 && target(true) == 0);
#endif
}
''')
                    for defines in ([], ["-DETHERNET_ENABLED"],
                                    ["-DETHERNET_ENABLED", f"-D{conditional}", "-DTEST_COMBINED_BRANCH"]):
                        with self.subTest(conditional=conditional, extractor=extract.__name__, defines=defines):
                            binary = Path(directory) / "conditional"
                            compile_result = subprocess.run(
                                ["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", *defines,
                                 str(cpp), "-o", str(binary)], capture_output=True, text=True, timeout=30)
                            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
                            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                            self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == "__main__":
    unittest.main()
