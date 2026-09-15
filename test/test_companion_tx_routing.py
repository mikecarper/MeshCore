"""Compile real Companion TX-route CLI and channel persistence; no radio required."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <vector>
#include <RadioProfiles.h>
#include <helpers/CLICommandUtils.h>
#include <helpers/CompanionTxRoutingCLI.h>
#include <helpers/IdentityStore.h>
#define MAX_GROUP_CHANNELS 4
#define MAX_ANON_CONTACTS 1
#define ADV_TYPE_NONE 0
namespace mesh {
struct Identity { uint8_t pub_key[32] = {}; };
struct GroupChannel { uint8_t secret[32] = {}, hash[1] = {}, tx_radio = RADIO_TX_AUTO; };
struct Utils {
 static void toHex(char* out, const uint8_t* in, size_t n) {
   for (size_t i=0;i<n;++i) sprintf(out+2*i,"%02X",in[i]);
 }
};
}
struct ContactInfo { mesh::Identity id; char name[32] = {}; uint8_t type=1, tx_radio=0; };
struct ChannelDetails { mesh::GroupChannel channel; char name[32] = {}; };
struct DataStoreHost {
 virtual bool onChannelLoaded(uint8_t, const ChannelDetails&)=0;
 virtual bool getChannelForSave(uint8_t, ChannelDetails&)=0;
};
struct Radio { mesh::RadioProfiles config; mesh::RadioProfiles* profiles() { return &config; } };
struct DataStore {
 FILESYSTEM fs;
 FILESYSTEM* _getContactsChannelsFS() { return &fs; }
 File openRead(FILESYSTEM* fs, const char* name) { return fs->open(name,"r"); }
 void loadChannels(DataStoreHost*);
 bool saveChannels(DataStoreHost*);
};
File openWrite(FILESYSTEM* fs, const char* name) { return fs->open(name,"w"); }
class MyMesh : public DataStoreHost {
public:
 std::vector<ContactInfo> contacts{4};
 ChannelDetails channels[MAX_GROUP_CHANNELS];
 Radio radio; Radio* _radio=&radio;
 DataStore store;
 bool writable=true, writes_ok=true; unsigned writes=0;
 bool canMutateContacts() const { return writable; }
 int getTotalContactSlots() const { return contacts.size(); }
 ContactInfo* getContactPtrByIdx(int i) { return &contacts.at(i); }
 bool getChannel(int i, ChannelDetails& c) { if (i<0 || i>=MAX_GROUP_CHANNELS) return false; c=channels[i]; return true; }
 bool setChannel(int i, const ChannelDetails& c) { if (i<0 || i>=MAX_GROUP_CHANNELS) return false; channels[i]=c; return true; }
 bool saveChannels() { ++writes; return writes_ok && store.saveChannels(this); }
 bool scheduleContactWrite(const ContactInfo&) { ++writes; return writes_ok; }
 bool flushContactsBeforeReboot() { return writes_ok; }
 bool onChannelLoaded(uint8_t i,const ChannelDetails& c) override { return setChannel(i,c); }
 bool getChannelForSave(uint8_t i,ChannelDetails& c) override { return getChannel(i,c); }
 bool handleTxRoutingCommand(const char*,char*,size_t);
};
@METHODS@

int main() {
 MyMesh node;
 strcpy(node.contacts[1].name,"Alice Jones"); strcpy(node.contacts[2].name,"Bob");
 strcpy(node.contacts[3].name,"Bob");
 for (int i=1;i<4;++i) { memset(node.contacts[i].id.pub_key,0x11,32); node.contacts[i].id.pub_key[31]=i; }
 strcpy(node.channels[0].name,"Public"); strcpy(node.channels[1].name,"#wardriving");
 node.channels[1].channel.secret[0]=17;
 node.radio.config.secondary.mode=mesh::RadioProfileMode::RxTx;
 node.radio.config.cross=mesh::RadioCrossMode::Off;
 node.radio.config.secondary_temporary=true;
 char reply[160];
 auto cmd=[&](const char* text) { assert(node.handleTxRoutingCommand(text,reply,sizeof(reply))); };
 cmd("set tx.user \"Alice Jones\" radio2"); assert(!strncmp(reply,"OK",2));
 assert(strstr(reply,"active=radio2") && node.contacts[1].tx_radio==mesh::RADIO_TX_SECONDARY);
 assert(node.contacts[2].tx_radio==mesh::RADIO_TX_AUTO);
 cmd("get tx.user Alice Jones"); assert(strstr(reply,"radio2"));
 cmd("set tx.user Bob off"); assert(strstr(reply,"ambiguous"));
 cmd("set tx.user key:111111111111 off"); assert(strstr(reply,"ambiguous"));
 char key[65], command[140]; mesh::Utils::toHex(key,node.contacts[2].id.pub_key,32);
 snprintf(command,sizeof(command),"set tx.user key:%s off",key); cmd(command);
 assert(!strncmp(reply,"OK",2) && node.contacts[2].tx_radio==mesh::RADIO_TX_OFF);
 assert(node.contacts[3].tx_radio==mesh::RADIO_TX_AUTO);
 // Quoting selects a literal name, even when it resembles a slot or key.
 strcpy(node.channels[2].name,"1");
 cmd("set tx.channel \"1\" off"); assert(!strncmp(reply,"OK",2));
 assert(node.channels[2].channel.tx_radio==mesh::RADIO_TX_OFF);
 assert(node.channels[1].channel.tx_radio==mesh::RADIO_TX_AUTO);
 cmd("set tx.channel 1 radio"); assert(!strncmp(reply,"OK",2));
 assert(node.channels[1].channel.tx_radio==mesh::RADIO_TX_PRIMARY);
 cmd("set tx.channel \"1\" auto");
 node.channels[1].channel.tx_radio=mesh::RADIO_TX_AUTO;
 strcpy(node.contacts[3].name,"key:111111111111");
 cmd("set tx.user \"key:111111111111\" both"); assert(!strncmp(reply,"OK",2));
 assert(node.contacts[3].tx_radio==mesh::RADIO_TX_BOTH);
 cmd("set tx.user key:111111111111 off"); assert(strstr(reply,"ambiguous"));
 node.contacts[3].tx_radio=mesh::RADIO_TX_AUTO;
 strcpy(node.contacts[3].name,"Bob");
 for (auto text : {"set tx.user Alice Jones", "set tx.user Alice Jones radio3", "set tx.user key:12 radio", "set tx.user \"Alice radio"}) {
   cmd(text); assert(!strncmp(reply,"Error",5));
 }
 assert(!node.handleTxRoutingCommand("get tx.username",reply,sizeof(reply)));
 cmd("set tx.channel 0 both"); assert(strstr(reply,"active=both"));
 cmd("set tx.channel #wardriving radio2"); assert(strstr(reply,"active=radio2"));
 auto disk=node.store.fs.files["/channels2"];
 assert(disk.size()==MAX_GROUP_CHANNELS*68);
 assert(disk[0]==mesh::encodeRadioTxPolicy(mesh::RADIO_TX_BOTH));
 assert(disk[68]==mesh::encodeRadioTxPolicy(mesh::RADIO_TX_SECONDARY));
 node.channels[0].channel.tx_radio=node.channels[1].channel.tx_radio=0;
 node.store.loadChannels(&node);
 assert(node.channels[0].channel.tx_radio==mesh::RADIO_TX_BOTH);
 assert(node.channels[1].channel.tx_radio==mesh::RADIO_TX_SECONDARY);
 node.radio.config.secondary.mode=mesh::RadioProfileMode::Rx;
 cmd("get tx.channel 1"); assert(strstr(reply,"active=off") && strstr(reply,"unavailable"));
 cmd("get tx.channel 0"); assert(strstr(reply,"active=radio "));
 node.writes_ok=false;
 cmd("set tx.channel 0 off"); assert(strstr(reply,"not saved"));
 assert(node.channels[0].channel.tx_radio==mesh::RADIO_TX_BOTH && node.store.fs.files["/channels2"]==disk);
 cmd("set tx.user Alice Jones both"); assert(strstr(reply,"not saved"));
 assert(node.contacts[1].tx_radio==mesh::RADIO_TX_SECONDARY);
 node.writes_ok=true; node.writable=false;
 cmd("set tx.channel 0 auto"); assert(strstr(reply,"storage unavailable"));
 node.writable=true;
 cmd("set tx.channel 0 auto"); assert(!strncmp(reply,"OK",2));
 cmd("get tx.channel"); assert(strstr(reply,"1 overrides"));
 for (unsigned i=0;i<256;++i) {
   auto policy=mesh::decodeRadioTxPolicy(i);
   assert(policy==((i>=0xa1 && i<=0xa4) ? (i&7) : 0));
 }
 for (auto& byte : node.store.fs.files["/channels2"]) byte=0;
 node.store.loadChannels(&node);
 for (auto& c : node.channels) assert(c.channel.tx_radio==mesh::RADIO_TX_AUTO);
}
'''


class CompanionTxRoutingTest(unittest.TestCase):
    def test_cli_resolution_persistence_and_failures(self):
        companion = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        store = (ROOT / 'examples/companion_radio/DataStore.cpp').read_text()
        methods = method(companion, 'bool MyMesh::handleTxRoutingCommand(')
        methods += '\n' + method(store, 'void DataStore::loadChannels(')
        methods += '\n' + method(store, 'bool DataStore::saveChannels(')
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / 'test.cpp').write_text(HARNESS.replace('@METHODS@', methods))
            build = subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra',
                '-fsanitize=address,undefined', '-fno-pie', '-no-pie',
                '-I', str(ROOT / 'test/fixtures/radio_profiles/mocks'),
                '-I', str(ROOT / 'test/mocks'), '-I', str(ROOT / 'src'),
                str(work / 'test.cpp'), '-o', str(work / 'test')], capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(work / 'test')], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_phone_protocol_updates_preserve_same_channel_policy(self):
        source = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        command = source[source.index('const bool have_previous = getChannel(channel_idx, previous);'):]
        command = command[:command.index('} else if (cmd_frame[0] == CMD_SIGN_START')]
        self.assertIn('!memcmp(channel.channel.secret, previous.channel.secret, sizeof(channel.channel.secret))', command)
        self.assertIn('channel.channel.tx_radio = previous.channel.tx_radio;', command)
        self.assertIn('setChannel(channel_idx, previous);', command)
        self.assertIn('handleTxRoutingCommand(command, reply, reply_size)',
            method(source, 'bool MyMesh::handleLocalControlCommand('))


if __name__ == '__main__':
    unittest.main()
