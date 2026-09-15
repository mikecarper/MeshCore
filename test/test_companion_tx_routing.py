"""Compile real Companion TX-route CLI and channel persistence; no radio required."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_radio_receive_contract import method
from test_companion_preferences_transaction import esp_recovery_helpers

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cerrno>
#include <vector>
#include <RadioProfiles.h>
#include <helpers/CLICommandUtils.h>
#include <helpers/CompanionTxRoutingCLI.h>
#include <helpers/IdentityStore.h>
#include "ContactFileTransaction.h"
#if defined(STM32_PLATFORM)
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#include <helpers/AtomicFileWriter.h>
#endif
#define MESH_DEBUG_PRINTLN(...) ((void)0)
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
 bool _channel_load_incomplete=false;
 bool _uncached_contact_load_incomplete=false;
 const char* _channel_recovery_source=nullptr;
 bool hasIncompleteContactLoad() const;
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
 bool canMutateContacts() const { return writable && !store.hasIncompleteContactLoad(); }
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
#if defined(STM32_PLATFORM)
 node.store.fs.rename_replaces=true;
#endif
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
 // Loading is all-or-nothing. ESP32 retries recovery and then permits an
 // explicitly requested defaults save; the other backends remain fail-closed.
 for(int fault : {0,1,2,3,4}) {
   MyMesh boot;
   boot.store.fs.files["/channels2"]=disk;
   strcpy(boot.channels[0].name,"keep existing");
   if(fault==0) boot.store.fs.fail_read_open=true;
   if(fault==1) boot.store.fs.fail_read_after=0;
   if(fault==2) boot.store.fs.fail_read_after=68; // one complete record read
   if(fault==3) boot.store.fs.files["/channels2"].pop_back();
   if(fault==4) boot.store.fs.files["/channels2"].resize((MAX_GROUP_CHANNELS+1)*68);
   auto durable=boot.store.fs.files["/channels2"];
   boot.store.loadChannels(&boot);
#if defined(ESP32_PLATFORM)
   assert(!boot.store.hasIncompleteContactLoad());
#else
   assert(boot.store.hasIncompleteContactLoad());
#endif
   assert(!strcmp(boot.channels[0].name,"keep existing"));
   assert(!boot.channels[1].name[0]);
   boot.store.fs.fail_read_open=false;boot.store.fs.fail_read_after=-1;
#if defined(ESP32_PLATFORM)
   assert(boot.store.fs.files["/channels2"]==durable);
   assert(boot.canMutateContacts()&&boot.store.saveChannels(&boot));
   assert(boot.store.fs.files["/channels2"]!=disk);
#else
   assert(!boot.canMutateContacts()&&!boot.store.saveChannels(&boot));
   assert(boot.store.fs.files["/channels2"]==durable);
#endif
 }
 MyMesh fresh;fresh.store.loadChannels(&fresh);
 assert(!fresh.store.hasIncompleteContactLoad()); // absent file is a fresh boot
 node.channels[0].channel.tx_radio=node.channels[1].channel.tx_radio=0;
 node.store.loadChannels(&node);
 assert(node.channels[0].channel.tx_radio==mesh::RADIO_TX_BOTH);
 assert(node.channels[1].channel.tx_radio==mesh::RADIO_TX_SECONDARY);
 node.radio.config.secondary.mode=mesh::RadioProfileMode::Rx;
 cmd("get tx.channel 1"); assert(strstr(reply,"active=off") && strstr(reply,"unavailable"));
 cmd("get tx.channel 0"); assert(strstr(reply,"active=radio "));
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM) || defined(STM32_PLATFORM)
 // A failed real filesystem write must preserve every durable channel,
 // including its secret, rather than just rolling back the live TX policy.
 node.store.fs.fail_write=true;
 cmd("set tx.channel 0 off"); assert(strstr(reply,"not saved"));
 assert(node.channels[0].channel.tx_radio==mesh::RADIO_TX_BOTH);
 assert(node.store.fs.files["/channels2"]==disk);
 node.store.fs.fail_write=false;
#if defined(STM32_PLATFORM)
 for(int failed_rename : {1}) {
#else
 for(int failed_rename : {1,2}) {
#endif
   node.store.fs.fail_rename=failed_rename;
   cmd("set tx.channel 0 off"); assert(strstr(reply,"not saved"));
   assert(node.channels[0].channel.tx_radio==mesh::RADIO_TX_BOTH);
   assert(node.store.fs.files["/channels2"]==disk);
 }
#if !defined(STM32_PLATFORM)
 // If both publish and rollback fail, the verified backup remains available
 // on reboot. A failed boot recovery quarantines writes for that whole boot.
 node.store.fs.fail_rename_from={"/channels2.tmp","/channels2.bak"};
 cmd("set tx.channel 0 off"); assert(strstr(reply,"not saved"));
 assert(!node.store.fs.exists("/channels2"));
 assert(node.store.fs.files["/channels2.bak"]==disk);
 MyMesh failed_boot;failed_boot.store.fs=node.store.fs;
 failed_boot.store.loadChannels(&failed_boot);
#if defined(ESP32_PLATFORM)
 assert(!failed_boot.store.hasIncompleteContactLoad());
 assert(failed_boot.channels[0].channel.tx_radio==mesh::RADIO_TX_BOTH);
 assert(!failed_boot.store.saveChannels(&failed_boot));
 assert(failed_boot.store.fs.files["/channels2.bak"]==disk);
 failed_boot.store.fs.fail_rename_from.clear();
 assert(failed_boot.store.saveChannels(&failed_boot));
 assert(failed_boot.store.fs.files["/channels2"]==disk);
#else
 assert(failed_boot.store.hasIncompleteContactLoad());
 failed_boot.store.fs.fail_rename_from.clear();
 failed_boot.store.loadChannels(&failed_boot);
 assert(failed_boot.store.hasIncompleteContactLoad());
 assert(!failed_boot.store.saveChannels(&failed_boot));
 assert(failed_boot.store.fs.files["/channels2.bak"]==disk);
#endif
 node.store.fs.fail_rename_from.clear();
 node.store.loadChannels(&node);
 assert(!node.store.hasIncompleteContactLoad());
 assert(node.store.fs.files["/channels2"]==disk);
 assert(node.channels[0].channel.tx_radio==mesh::RADIO_TX_BOTH);
#endif
#endif
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
        methods = esp_recovery_helpers(store) + method(companion, 'bool MyMesh::handleTxRoutingCommand(')
        methods += '\n' + method(store, 'void DataStore::loadChannels(')
        methods += '\n' + method(store, 'bool DataStore::saveChannels(')
        methods += '\n' + method(store, 'bool DataStore::hasIncompleteContactLoad(')
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            # Keep the real transaction implementation, substituting only its
            # filesystem include with the fault-injectable test backend.
            transaction = (ROOT / 'src/helpers/ContactFileTransaction.h').read_text()
            (work / 'ContactFileTransaction.h').write_text(transaction.replace(
                '#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>'))
            (work / 'test.cpp').write_text(HARNESS.replace('@METHODS@', methods))
            for platform in ('ESP32_PLATFORM', 'RP2040_PLATFORM', 'STM32_PLATFORM'):
                with self.subTest(platform=platform):
                    build = subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-D'+platform+'=1',
                        '-fsanitize=address,undefined', '-fno-pie', '-no-pie',
                        '-I', str(ROOT / 'test/fixtures/radio_profiles/mocks'),
                        '-I', str(ROOT / 'test/mocks'), '-I', str(ROOT / 'src'),
                        '-I', str(ROOT / 'src/helpers'),
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
