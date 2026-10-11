"""Execute the public-key CLI with production formatting and real identity data."""
from itertools import product
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <array>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <Identity.h>

namespace mesh {
@HEX_CHARS@
@TO_HEX@
@IDENTITY_METHODS@
}

class IdentityInput : public Stream {
public:
 std::vector<uint8_t> bytes;
 size_t offset=0;
 int available() override{return int(bytes.size()-offset);}
 int read() override{return offset<bytes.size()?bytes[offset++]:-1;}
};
struct MyMesh {
 mesh::LocalIdentity self_id;
 unsigned fallback_calls=0;
 bool handleLocalControlCommand(const char*,char*,size_t);
};
@COMMAND@

std::array<uint8_t,PRV_KEY_SIZE+PUB_KEY_SIZE> snapshot(const MyMesh& node){
 std::array<uint8_t,PRV_KEY_SIZE+PUB_KEY_SIZE> bytes;
 assert(node.self_id.writeTo(bytes.data(),bytes.size())==bytes.size());
 return bytes;
}
int main(){
 static_assert(PUB_KEY_SIZE==32&&PRV_KEY_SIZE==64,"Exercise the entire identity");
 const uint8_t public_key[PUB_KEY_SIZE]={
   0x00,0xff,0x01,0x80,0x0a,0xaf,0x10,0x7f,
   0x55,0xaa,0xfe,0xef,0x03,0x20,0x9c,0xcd,
   0x04,0x30,0xb2,0xd3,0x05,0x40,0x56,0xe4,
   0x06,0x50,0x78,0xf5,0x07,0x60,0x9a,0xc6
 };
 const std::string expected=
   "> 00FF01800AAF107F55AAFEEF03209CCD0430B2D3054056E4065078F507609AC6";
 assert(expected.size()==2+PUB_KEY_SIZE*2);
 IdentityInput input;
 input.bytes.insert(input.bytes.end(),public_key,public_key+sizeof(public_key));
 for(unsigned i=0;i<PRV_KEY_SIZE;++i)input.bytes.push_back(uint8_t(0xe7^i));
 MyMesh node;assert(node.self_id.readFrom(input));
 const auto original=snapshot(node);
 assert(!memcmp(node.self_id.pub_key,public_key,sizeof(public_key)));
 for(unsigned i=0;i<PRV_KEY_SIZE;++i)assert(original[i]==uint8_t(0xe7^i));
 // Every capacity around the 66-byte reply boundary is exercised with exact
 // prefix expectations and both leading/trailing canaries under sanitizers.
 std::vector<size_t> capacities;
 for(size_t size=1;size<=80;++size)capacities.push_back(size);
 for(size_t size : {size_t(128),size_t(160),size_t(176),size_t(256)})capacities.push_back(size);
 for(size_t size : capacities){
   std::vector<uint8_t> guarded(size+16,0xa7);
   char* reply=reinterpret_cast<char*>(guarded.data()+8);
   const unsigned fallback=node.fallback_calls;
   assert(node.handleLocalControlCommand("get public.key",reply,size));
   const size_t written=size-1<expected.size()?size-1:expected.size();
   assert(strlen(reply)==written&&!memcmp(reply,expected.data(),written));
   assert(reply[written]==0&&node.fallback_calls==fallback);
   for(size_t i=0;i<8;++i)assert(guarded[i]==0xa7);
   for(size_t i=8+size;i<guarded.size();++i)assert(guarded[i]==0xa7);
   for(size_t i=written+1;i<size;++i)assert(uint8_t(reply[i])==0xa7);
   assert(snapshot(node)==original);
 }
 // Invalid caller pointers and zero capacity never read commands or change
 // output, identity, or any later command-handler state.
 char guarded[160];memset(guarded,'X',sizeof(guarded));
 const unsigned before_invalid=node.fallback_calls;
 assert(!node.handleLocalControlCommand(nullptr,guarded,sizeof(guarded)));
 assert(!node.handleLocalControlCommand("get public.key",nullptr,sizeof(guarded)));
 assert(!node.handleLocalControlCommand("get public.key",guarded,0));
 assert(!node.handleLocalControlCommand(nullptr,nullptr,0));
 for(char byte : guarded)assert(byte=='X');
 assert(node.fallback_calls==before_invalid&&snapshot(node)==original);
 // A const input is accepted without modification, as is the established
 // leading-space normalization. Other whitespace is not an implicit alias.
 char command[]="    get public.key";
 const std::string command_before=command;
 assert(node.handleLocalControlCommand(command,guarded,sizeof(guarded)));
 assert(guarded==expected&&command_before==command&&snapshot(node)==original);
 auto reject=[&](const std::string& command){
   memset(guarded,'X',sizeof(guarded));
   const unsigned before=node.fallback_calls;
   assert(!node.handleLocalControlCommand(command.c_str(),guarded,sizeof(guarded)));
   assert(node.fallback_calls==before+1);
   for(char byte : guarded)assert(byte=='X');
   assert(snapshot(node)==original);
 };
 for(const char* command : {"", " ", "get public", "get public.keyx",
     "get public.key extra", "get public.key ", "get public.key\t",
     "get public.key\r", "get public.key\n", "get\tpublic.key",
     "\tget public.key", "GET public.key", "get Public.key", "get public.KEY",
     "get pub.key", "get publickey", "get public_key", "get private.key",
     "get prv.key", "set public.key", "set public.key 00", "set prv.key 00",
     "erase public.key", "public.key"})reject(command);
 const std::string exact="get public.key";
 for(size_t length=0;length<exact.size();++length)reject(exact.substr(0,length));
 for(size_t offset=0;offset<exact.size();++offset){
   std::string mutated=exact;mutated[offset]='#';reject(mutated);
 }
 // Read the live identity, not a hardcoded value or a cached response. The
 // public query must never expose the different, nonzero private-key bytes.
 for(unsigned i=0;i<PUB_KEY_SIZE;++i)node.self_id.pub_key[i]=uint8_t(0xff-i);
 const auto changed=snapshot(node);
 assert(!memcmp(changed.data(),original.data(),PRV_KEY_SIZE));
 assert(node.handleLocalControlCommand("get public.key",guarded,sizeof(guarded)));
 assert(!strcmp(guarded,
   "> FFFEFDFCFBFAF9F8F7F6F5F4F3F2F1F0EFEEEDECEBEAE9E8E7E6E5E4E3E2E1E0"));
 assert(snapshot(node)==changed);
}
'''


class CompanionIdentityCliTests(unittest.TestCase):
    def test_production_query_bounds_exact_matching_identity_and_feature_gates(self):
        source = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        local = method(source, 'bool MyMesh::handleLocalControlCommand(')
        # Keep the production method signature, null/capacity guard, leading
        # normalization and identity branch intact. The unrelated later
        # handlers are replaced only by an observable fallthrough sentinel.
        dispatch = local.index('  if (handleTxRoutingCommand(')
        command = local[:dispatch] + '  ++fallback_calls;\n  return false;\n}'
        utils = (ROOT / 'src/Utils.cpp').read_text()
        hex_chars = next(line for line in utils.splitlines()
                         if line.startswith('static const char hex_chars[]'))
        identity = (ROOT / 'src/Identity.cpp').read_text()
        identity_methods = '\n'.join(method(identity, signature) for signature in (
            'Identity::Identity()', 'LocalIdentity::LocalIdentity()',
            'bool LocalIdentity::readFrom(Stream& s)',
            'size_t LocalIdentity::writeTo(uint8_t* dest, size_t max_len) const'))
        harness = HARNESS.replace('@COMMAND@', command)
        harness = harness.replace('@HEX_CHARS@', hex_chars)
        harness = harness.replace('@TO_HEX@', method(utils, 'void Utils::toHex('))
        harness = harness.replace('@IDENTITY_METHODS@', identity_methods)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            cpp = work / 'identity.cpp'
            cpp.write_text(harness)
            binary = work / 'identity'
            for debug, packets, private_export in product((0, 1), repeat=3):
                with self.subTest(debug=debug, packets=packets, private_export=private_export):
                    flags = ['ARDUINO=1', 'MESH_DEBUG=' + str(debug),
                             'MESH_PACKET_LOGGING=' + str(packets),
                             'ENABLE_PRIVATE_KEY_EXPORT=' + str(private_export),
                             'COMPANION_FEATURE_TEXT_TERMINAL=' + str(private_export),
                             'COMPANION_FEATURE_OTA_CLI=' + str(packets)]
                    if not debug and not packets:
                        flags.append('MESH_USB_LOGGING_DISABLED=1')
                    if debug and packets:
                        flags += ['COMPANION_RADIO_FULL=1', 'WITH_MQTT_BRIDGE=1']
                    built = subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17',
                        '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                        '-fno-pie', '-no-pie', *['-D' + flag for flag in flags],
                        '-I', str(ROOT / 'src'), '-I', str(ROOT / 'test/mocks'),
                        str(cpp), '-o', str(binary)], capture_output=True, text=True)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    ran = subprocess.run([str(binary)], capture_output=True, text=True)
                    self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_terminal_binary_and_help_route_to_the_shared_readonly_query(self):
        source = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        terminal = method(source, 'void MyMesh::handleTerminalCommand(')
        self.assertIn('handleLocalControlCommand(command, local_reply, sizeof(local_reply))', terminal)
        self.assertIn('terminalOutput().printf("  -> %s\\r\\n", local_reply);', terminal)
        self.assertIn('"  get public.key\\r\\n"', terminal)
        shared = method(source, 'bool MyMesh::handleCommand(')
        self.assertIn('if (handleLocalControlCommand(command, reply, reply_capacity)) return true;', shared)
        self.assertIn('return handleCommand(command, 0, reply);',
                      (ROOT / 'examples/companion_radio/MyMesh.h').read_text())
        start = source.index('} else if (mesh::companion::isRunCliFrame(')
        end = source.index('} else if (cmd_frame[0] == CMD_SEND_TXT_MSG', start)
        framed = source[start:end]
        self.assertIn('handled = handleCommand(text, 0, reply_buf);', framed)
        self.assertIn('out_frame[0] = RESP_CODE_CLI_REPLY;', framed)
        # CLI setters may change transport ownership while they execute. The
        # reply must use the captured requester and check complete admission.
        capture = 'BaseSerialInterface* reply_route = _serial->captureReplyRoute();'
        self.assertIn(capture, framed)
        self.assertLess(framed.index(capture), framed.index('handleCommand(text, 0, reply_buf)'))
        self.assertRegex(framed, r'_serial->writeFrameToRoute\(\s*'
                         r'reply_route,\s*out_frame,\s*1 \+ rlen\)\s*==\s*'
                         r'static_cast<size_t>\(1 \+ rlen\)')
        self.assertNotIn('_serial->writeFrame(out_frame, 1 + rlen);', framed)
        local = method(source, 'bool MyMesh::handleLocalControlCommand(')
        branch = local[:local.index('  if (handleTxRoutingCommand(')]
        self.assertIn('strcmp(command, "get public.key") == 0', branch)
        self.assertNotIn('#if', branch)  # Not gated by private export or logging.
        self.assertNotIn('prv_key', branch)
        self.assertNotIn('save', branch)


if __name__ == '__main__':
    unittest.main()
