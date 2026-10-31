"""Run actual role preference hooks against the detected SX1268 frequency band."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_t096_full_memory import method

ROOT = Path(__file__).resolve().parents[1]
GUARD = "#if defined(MESH_RADIO_AUTO_SX1268) && MESH_RADIO_AUTO_SX1268"
ROLES = (
    ("common", "src/helpers/CommonCLI.cpp", "void CommonCLI::loadPrefs(", "_prefs->freq"),
    ("companion", "examples/companion_radio/MyMesh.cpp", "void MyMesh::begin(", "_prefs.freq"),
    ("terminal", "examples/simple_secure_chat/main.cpp", "void begin(FILESYSTEM& fs", "_prefs.freq"),
)


def frequency_hooks(source):
    result = []
    lines = source.splitlines(keepends=True)
    for start, line in enumerate(lines):
        if line.strip() != GUARD:
            continue
        level = 1
        for end in range(start + 1, len(lines)):
            text = lines[end].lstrip()
            if re.match(r"#\s*if(?:def|ndef)?\b", text):
                level += 1
            elif re.match(r"#\s*endif\b", text):
                level -= 1
                if level == 0:
                    block = "".join(lines[start:end + 1])
                    if "autoLoRaInitialFrequency" in block:
                        result.append(block)
                    break
    return result


PROGRAM = r'''
#include <helpers/LoRaRadioFamily.h>
#include <cassert>
#include <cmath>
#include <initializer_list>
#include <limits>
static mesh::LoRaRadioFamily detected=mesh::LoRaRadioFamily::None;
static mesh::LoRaRadioFamily universalLoRaRadioFamily(){return detected;}
static unsigned debug_lines=0;
#define MESH_DEBUG_PRINTLN(...) do { ++debug_lines; } while(0)
struct Prefs {
  float freq; float bw=62.5f; unsigned sf=7,cr=5; uint8_t tx=9;
  explicit Prefs(float initial):freq(initial){}
};
@ROLE_CODE@
static bool same(float a,float b){return a==b || (std::isnan(a)&&std::isnan(b));}
static void check(void(*before)(Prefs&),void(*after)(Prefs&)) {
  for(const auto family:{mesh::LoRaRadioFamily::SX1268,mesh::LoRaRadioFamily::SX126x,
                         mesh::LoRaRadioFamily::SX127x}) {
    detected=family;
    const bool low_band=family==mesh::LoRaRadioFamily::SX1268;
    for(float preset:{434.0f,869.525f,910.525f,915.0f}) {
      Prefs fresh{preset};
      before(fresh);
#if MESH_RADIO_AUTO_SX1268
      const float expected=low_band?434.0f:preset;
#else
      const float expected=preset;
#endif
      assert(same(fresh.freq,expected));
      const auto lines=debug_lines;
      after(fresh);
      assert(same(fresh.freq,expected) && debug_lines==lines);
      for(float saved:{410.0f,434.0f,470.5f,510.0f,780.0f,810.0f,
                       409.999f,810.001f,915.0f,0.0f,-1.0f,
                       std::numeric_limits<float>::quiet_NaN(),
                       std::numeric_limits<float>::infinity(),
                       -std::numeric_limits<float>::infinity()}) {
        Prefs prefs{preset};
        before(prefs);
        // Model the existing file reader/migration boundary between the two
        // extracted hooks. Disk stays the authority until loading completes.
        const float durable=saved;
        prefs.freq=durable;
        const auto old_lines=debug_lines;
        after(prefs);
#if MESH_RADIO_AUTO_SX1268
        const bool replaced=low_band && !(saved>=410.0f && saved<=810.0f);
        const float applied=replaced?434.0f:saved;
#else
        const bool replaced=false;
        const float applied=saved;
#endif
        assert(same(prefs.freq,applied) && same(durable,saved));
        assert(debug_lines==old_lines+(replaced?1:0));
        assert(prefs.bw==62.5f && prefs.sf==7 && prefs.cr==5 && prefs.tx==9);
      }
    }
  }
}
int main(){
@CALLS@
}
'''


class UniversalSx126xDefaultsTest(unittest.TestCase):
    def sites(self):
        result=[]
        for role,path,signature,field in ROLES:
            source=(ROOT/path).read_text()
            body=method(source,signature)
            hooks=frequency_hooks(body)
            self.assertEqual(len(hooks),2,(role,hooks))
            result.append((role,body,hooks,field))
        return result

    def test_actual_role_hooks_keep_valid_saved_channels_and_use_safe_fresh_defaults(self):
        compiler=os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:self.skipTest("host C++ compiler required")
        code=[];calls=[]
        for role,body,hooks,field in self.sites():
            binding="Prefs* _prefs=&prefs;" if "->" in field else "Prefs& _prefs=prefs;"
            for index,hook in enumerate(hooks):
                code.append("static void %s_%s(Prefs& prefs) { %s\n%s\n}\n" %
                            (role,index,binding,hook))
            calls.append("check(%s_0,%s_1);"%(role,role))
        program=PROGRAM.replace("@ROLE_CODE@","\n".join(code)).replace("@CALLS@","\n".join(calls))
        with tempfile.TemporaryDirectory(prefix="universal-lora-defaults-") as directory:
            work=Path(directory);source=work/"test.cpp";source.write_text(program)
            for setting in (None,0,1):
                with self.subTest(automatic_sx1268=setting):
                    executable=work/("check"+str(setting)+(".exe" if os.name=="nt" else ""))
                    definitions=[] if setting is None else ["-DMESH_RADIO_AUTO_SX1268="+str(setting)]
                    sanitize=(["-fsanitize=address,undefined","-fno-sanitize-recover=all","-fno-pie","-no-pie"]
                              if sys.platform.startswith("linux") else [])
                    result=subprocess.run([compiler,"-std=c++11","-O2","-Wall","-Wextra","-Werror",
                        "-Wno-unused-function","-Wno-unused-variable",*sanitize,*definitions,
                        "-I"+str(ROOT/"src"),str(source),"-o",str(executable)],capture_output=True,text=True)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                    result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=20)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_hooks_are_after_detection_before_apply_and_do_not_persist_fallback(self):
        for role,body,hooks,field in self.sites():
            before,after=hooks
            with self.subTest(role=role):
                for hook in hooks:
                    self.assertNotRegex(hook,r"\b(?:savePrefs|saveCommonPrefsImageAtomically|write|Serial)\b")
                    self.assertIn(GUARD,hook)
                    self.assertIn(field,hook)
                self.assertIn("MESH_DEBUG_PRINTLN",after)
                if role=="common":
                    self.assertLess(body.index(before),body.index("loadPrefsInt(fs"))
                    self.assertGreater(body.index(after),body.index('hilStartupTrace("prefs_migration_ready")'))
                    self.assertLess(body.index(after),body.index("_radio_profiles.stagePrimary("))
                elif role=="companion":
                    self.assertLess(body.index(before),body.index("_store->loadPrefs(_prefs"))
                    self.assertGreater(body.index(after),body.index("_store->savePrefs(_prefs"))
                    self.assertLess(body.index(after),body.index("configureRadioFromPrefs();"))
                else:
                    self.assertLess(body.index(before),body.index('_fs->exists("/node_prefs")'))
                    self.assertGreater(body.index(after),body.index("file.read((uint8_t *) &_prefs"))
                    self.assertLess(body.index(after),body.index("loadContacts();"))
        # All affected setup paths select the board before loading role prefs.
        for path in ("examples/companion_radio/main.cpp","examples/simple_repeater/main.cpp",
                     "examples/simple_room_server/main.cpp","examples/simple_secure_chat/main.cpp"):
            source=(ROOT/path).read_text()
            self.assertLess(source.index("board.begin();"),source.index("the_mesh.begin("),path)


if __name__=="__main__":
    unittest.main()
