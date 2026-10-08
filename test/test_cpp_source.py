#!/usr/bin/env python3
"""Qualify the shared C++ extractor against real and conditional source."""

from pathlib import Path
import subprocess
import tempfile
import unittest

from cpp_source import body


ROOT = Path(__file__).resolve().parents[1]


class CppSourceTests(unittest.TestCase):
    def test_stops_at_the_actual_body_boundary(self):
        function = "void target() { if (true) { call(); } }"
        self.assertEqual(body(function + "\nvoid next() {}", "void target()"), function)

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
        extracted = body(source, "int target(")
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / "conditional.cpp"
            cpp.write_text("#include <cassert>\n" + extracted + r'''
int main() {
#if !defined(ETHERNET_ENABLED)
  assert(target(false) == 0 && target(true) == 0);
#elif defined(RAK4631_COMBINED_ETHERNET)
  assert(target(false) == 0 && target(true) == 3);
#else
  assert(target(false) == 3 && target(true) == 0);
#endif
}
''')
            for defines in ([], ["-DETHERNET_ENABLED"],
                            ["-DETHERNET_ENABLED", "-DRAK4631_COMBINED_ETHERNET"]):
                with self.subTest(defines=defines):
                    binary = Path(directory) / "conditional"
                    compile_result = subprocess.run(
                        ["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", *defines,
                         str(cpp), "-o", str(binary)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
                    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == "__main__":
    unittest.main()
