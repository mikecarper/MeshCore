"""Exercise exact P1/XIAO bootloader identities in the real C++ validators."""
import hashlib
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/mota'))
from test_mota import _set_two_valid_xiao_manifest_crcs, _xiao_manifest_crc
from cpp_source import body

SIZE, OFFSET = 40960, 40960 - 76


def bootloader(name='SCAP_DFU', board=0x28860044, version=0x02040CFF):
    image = bytearray(b'\xff' * SIZE)
    struct.pack_into('<II', image, 0, 0x20030000, 0xF4101)
    image[0x100:0x110] = b'MOTABLDR' + struct.pack('<HHBBBB', 3, 5, 0x0E, 0, 0, 0)
    struct.pack_into('<8sHHIII16sI', image, OFFSET, b'BLMFCRC1', 1, 44,
                     0xF4000, SIZE, board, name.encode().ljust(16, b'\0'), 0)
    struct.pack_into('<8sHHIHHIHHI', image, OFFSET + 44, b'BLM2SOFT', 2, 32,
                     version, 140, 0x123, 0x27000, 1, 0, 0)
    struct.pack_into('<I', image, OFFSET + 40, zlib.crc32(image) & 0xFFFFFFFF)
    return bytes(image)


SOURCE = r'''
#include <cassert>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>
#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <bcrypt.h>
#else
#include <openssl/evp.h>
#endif
#include "helpers/ota/BootloaderMigrationPolicy.h"
namespace mesh {
void Utils::sha256(uint8_t *out,size_t count,const uint8_t *a,int alen,const uint8_t *b,int blen) {
  assert(count<=32 && alen>=0 && blen>=0);
  uint8_t digest[32];
#ifdef _WIN32
  BCRYPT_ALG_HANDLE algorithm=nullptr; BCRYPT_HASH_HANDLE hash=nullptr;
  assert(BCryptOpenAlgorithmProvider(&algorithm,BCRYPT_SHA256_ALGORITHM,nullptr,0)>=0);
  assert(BCryptCreateHash(algorithm,&hash,nullptr,0,nullptr,0,0)>=0);
  if (alen) assert(BCryptHashData(hash,const_cast<uint8_t*>(a),alen,0)>=0);
  if (blen) assert(BCryptHashData(hash,const_cast<uint8_t*>(b),blen,0)>=0);
  assert(BCryptFinishHash(hash,digest,sizeof(digest),0)>=0);
  assert(BCryptDestroyHash(hash)>=0);
  assert(BCryptCloseAlgorithmProvider(algorithm,0)>=0);
  std::memcpy(out,digest,count);
#else
  unsigned size=0;
  EVP_MD_CTX *ctx=EVP_MD_CTX_new(); assert(ctx);
  assert(EVP_DigestInit_ex(ctx,EVP_sha256(),nullptr)==1);
  assert(EVP_DigestUpdate(ctx,a,alen)==1);
  if (blen) assert(EVP_DigestUpdate(ctx,b,blen)==1);
  assert(EVP_DigestFinal_ex(ctx,digest,&size)==1 && size==32);
  EVP_MD_CTX_free(ctx); std::memcpy(out,digest,count);
#endif
}
void Utils::sha256(uint8_t *out,size_t count,const uint8_t *a,int length) {
  sha256(out,count,a,length,nullptr,0);
}
}
using namespace mesh::ota;
static std::vector<uint8_t> load(const std::string &directory,const char *name) {
  std::ifstream input(directory+"/"+name,std::ios::binary); assert(input.good());
  std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(input)),{});
  assert(bytes.size()==OTA_BOOT_IMAGE_SIZE); return bytes;
}
static OtaBootloaderIdentity parse(const std::vector<uint8_t> &bytes) {
  OtaBootloaderIdentity id;
  assert(ota_bootloader_vector_sane(bytes.data()));
  assert(ota_bootloader_candidate_identity_from_image(bytes.data(),bytes.size(),id));
  return id;
}
struct Store {
  const std::vector<uint8_t> &bytes;
  bool read(uint32_t offset,uint8_t *out,uint32_t size) {
    if (offset>bytes.size() || size>bytes.size()-offset) return false;
    std::memcpy(out,bytes.data()+offset,size); return true;
  }
};
@CLI_HELPERS@
int main(int argc,char **argv) {
  assert(argc==3);
  const std::string directory=argv[1], scenario=argv[2];
  auto image=load(directory,"p1.bin"); auto p1=parse(image);
  auto base=parse(load(directory,"xiao-base.bin"));
  auto sense=parse(load(directory,"xiao-sense.bin"));
  auto old=parse(load(directory,"p1-old.bin"));
  const uint8_t p1_name[16]={'S','C','A','P','_','D','F','U'};
  const uint8_t xiao_name[16]={'X','I','A','O','_','D','F','U'};
  if (scenario=="canonical") {
    assert(p1.board_id==0x28860044 && p1.crc_ok && p1.continuity_present);
    assert(p1.softdevice_fwid==0x123 && p1.app_base==0x27000 && p1.boot_version==0x02040CFF);
    uint8_t hw[32]; assert(ota_bootloader_hw_id(p1,hw));
    const char expected[32]="NRF_BL_28860044_SCAP_DFU";
    assert(!std::memcmp(hw,expected,32));
    assert(ota_bootloader_target_id(p1)==0xD677B472);
    assert(ota_bootloader_target_id(p1)!=base.board_id);
  } else if (scenario=="legacy") {
    for (const auto &id:{base,sense}) {
      uint8_t hw[32]; assert(ota_bootloader_hw_id(id,hw));
      const char expected_base[32]="XIAO_BL_28860044", expected_sense[32]="XIAO_BL_28860045";
      assert(!std::memcmp(hw,id.board_id==base.board_id?expected_base:expected_sense,32));
      assert(ota_bootloader_target_id(id)==id.board_id);
      assert(ota_xiao_bootloader_identity(id.board_id,xiao_name));
    }
    assert(!ota_xiao_bootloader_identity(p1.board_id,p1_name));
    assert(!ota_bootloader_device_name_valid(0x28860045,p1_name));
    assert(ota_bootloader_target_id(0x28860045,p1_name)==0);
    uint8_t wrong[16]={'B','O','G','U','S'};
    assert(!ota_bootloader_device_name_valid(0x28860044,wrong));
    assert(ota_bootloader_target_id(0x28860044,wrong)==0);
    uint8_t unpadded[16]; std::memcpy(unpadded,p1_name,16); unpadded[15]=1;
    assert(!ota_bootloader_device_name_valid(0x28860044,unpadded));
    assert(ota_bootloader_target_id(0x28860044,unpadded)==0);
  } else if (scenario=="qspi") {
    Store store{image}; OtaBootloaderIdentity parsed; OtaBootloaderCapsMarker caps;
    assert(ota_bootloader_external_image_metadata(store,0,0x0E,parsed,caps));
    assert(ota_bootloader_identity_matches(p1,parsed) && caps.storage_flags==0x0E);
    assert(!ota_bootloader_external_image_metadata(store,0,0x0A,parsed,caps));
    image[0x200]^=1;
    assert(!ota_bootloader_candidate_identity_from_image(image.data(),image.size(),parsed));
    assert(!ota_bootloader_external_image_metadata(store,0,0x0E,parsed,caps));
  } else if (scenario=="migration") {
    using namespace mesh::ota::migration;
    assert(candidate_matches_resource(p1,0x28860044,reinterpret_cast<const char*>(p1_name),
                                      p1.boot_version,0x27000,0x123));
    assert(installed_can_migrate(old,p1,false,false,0x27000,0x123));
    assert(!installed_can_migrate(base,p1,true,true,0x27000,0x123));
    assert(!installed_can_migrate(p1,base,true,true,0x27000,0x123));
    assert(!installed_can_migrate(sense,p1,true,true,0x27000,0x123));
    assert(!installed_can_migrate(p1,p1,true,true,0x27000,0x123));
    assert(!installed_can_migrate(p1,old,true,true,0x27000,0x123));
    assert(!installed_can_migrate(old,p1,false,false,0x26000,0xB6));
  } else if (scenario=="duplicate") {
    image=load(directory,"duplicate.bin"); OtaBootloaderIdentity id;
    OtaBootloaderIdentity first,second;
    assert(ota_bootloader_manifest_parse(image.data()+0x400,0x400,first,76));
    assert(ota_bootloader_manifest_parse(image.data()+OTA_BOOT_CANDIDATE_MANIFEST_OFFSET,
                                       OTA_BOOT_CANDIDATE_MANIFEST_OFFSET,second,76));
    assert(ota_boot_image_crc32(image.data(),image.size(),0x400+40)==first.crc32);
    assert(ota_boot_image_crc32(image.data(),image.size(),OTA_BOOT_CANDIDATE_MANIFEST_OFFSET+40)==second.crc32);
    assert(!ota_bootloader_identity_from_image(image.data(),image.size(),id));
    Store store{image}; OtaBootloaderCapsMarker caps;
    assert(!ota_bootloader_external_image_metadata(store,0,0x0E,id,caps));
  } else if (scenario=="xiao-cli") {
    const uint8_t flags=MFLAG_FULL|MFLAG_SIGNED|MFLAG_BOOTLOADER;
    CliContext context;
    context.caps.present=true; context.caps.apply_abi=3;
    context.caps.codec_mask=OTA_BL_REQUIRED_APP_CODEC_MASK;
    context.caps.storage_flags=OTA_BL_PROFILE_QSPI_BOOT_UPDATE;
    char reply[160]={};
    for (const auto &installed:{base,sense}) {
      context.identity=installed;
      for (const auto &candidate:{base,sense}) {
        assert(cli_list_fit(context,candidate.board_id,flags,CODEC_FULL));
        assert(!cli_pull_rejected(context,candidate.board_id,flags,CODEC_FULL,reply));
      }
    }
    context.identity=p1;
    assert(!cli_list_fit(context,base.board_id,flags,CODEC_FULL));
    assert(cli_pull_rejected(context,base.board_id,flags,CODEC_FULL,reply));
    assert(std::strstr(reply,"does not match"));
    context.identity=sense;
    assert(!cli_list_fit(context,base.board_id,flags&~MFLAG_SIGNED,CODEC_FULL));
    assert(cli_pull_rejected(context,base.board_id,flags&~MFLAG_SIGNED,CODEC_FULL,reply));
    context.caps.present=false;
    assert(!cli_list_fit(context,base.board_id,flags,CODEC_FULL));
    assert(cli_pull_rejected(context,base.board_id,flags,CODEC_FULL,reply));
    assert(std::strstr(reply,"lacks safe"));
  } else if (scenario=="xiao-successor") {
    uint8_t hw[32],wrong_hw[32],digest[32],zero[8]={},mid[4]={1,2,3,4};
    mesh::Utils::sha256(digest,32,image.data(),image.size());
    MotaManifest manifest;
    manifest.format_ver=3; manifest.flags=MFLAG_FULL|MFLAG_SIGNED|MFLAG_BOOTLOADER;
    manifest.codec_id=CODEC_FULL; manifest.image_size=manifest.payload_size=OTA_BOOT_IMAGE_SIZE;
    manifest.block_size_log2=10; manifest.block_count=40; manifest.base_hash=zero;
    manifest.merkle_root=mid; manifest.image_hash=digest; manifest.hw_id=hw;
    for (const auto &installed:{base,sense}) {
      for (const auto &candidate:{base,sense}) {
        manifest.target_id=candidate.board_id;
        assert(ota_bootloader_hw_id(candidate,hw));
        assert(ota_bootloader_target_compatible(installed,manifest.target_id));
        assert(ota_bootloader_confirmation_gate(manifest,installed,mid,mid,digest)==OTA_BOOT_CONFIRM_OK);
        assert(ota_bootloader_update_identity_matches(installed,candidate,manifest));
        assert(ota_bootloader_identity_matches(installed,candidate)==(installed.board_id==candidate.board_id));
        manifest.target_id^=1;
        assert(ota_xiao_bootloader_hw_id(manifest.target_id,hw));
        assert(ota_bootloader_confirmation_gate(manifest,installed,mid,mid,digest)==OTA_BOOT_CONFIRM_OK);
        assert(!ota_bootloader_update_identity_matches(installed,candidate,manifest));
        assert(ota_bootloader_hw_id(candidate,wrong_hw));
        manifest.hw_id=wrong_hw;
        assert(ota_bootloader_confirmation_gate(manifest,installed,mid,mid,digest)==OTA_BOOT_CONFIRM_HW_ID);
        manifest.hw_id=hw;
      }
    }
    manifest.target_id=base.board_id;
    assert(ota_bootloader_hw_id(base,hw));
    assert(!ota_bootloader_target_compatible(p1,base.board_id));
    assert(!ota_bootloader_target_compatible(base,ota_bootloader_target_id(p1)));
    assert(!ota_bootloader_update_identity_matches(p1,base,manifest));
    assert(!ota_bootloader_update_identity_matches(base,p1,manifest));
    manifest.flags&=~MFLAG_SIGNED;
    assert(ota_bootloader_confirmation_gate(manifest,sense,mid,mid,digest)==OTA_BOOT_CONFIRM_NOT_BOOT_PACKAGE);
  } else {
    uint8_t hw[32],xhw[32],digest[32],zero[8]={},mid[4]={1,2,3,4};
    assert(ota_bootloader_hw_id(p1,hw) && ota_bootloader_hw_id(base,xhw));
    mesh::Utils::sha256(digest,32,image.data(),image.size());
    MotaManifest manifest;
    manifest.format_ver=3; manifest.flags=MFLAG_FULL|MFLAG_SIGNED|MFLAG_BOOTLOADER;
    manifest.codec_id=CODEC_FULL; manifest.image_size=manifest.payload_size=OTA_BOOT_IMAGE_SIZE;
    manifest.block_size_log2=10; manifest.block_count=40; manifest.base_hash=zero;
    manifest.target_id=ota_bootloader_target_id(p1); manifest.hw_id=hw;
    manifest.merkle_root=mid; manifest.image_hash=digest;
    assert(ota_bootloader_confirmation_gate(manifest,old,mid,mid,digest)==OTA_BOOT_CONFIRM_OK);
    assert(ota_bootloader_confirmation_gate(manifest,base,mid,mid,digest)==OTA_BOOT_CONFIRM_TARGET);
    manifest.target_id=base.board_id;
    assert(ota_bootloader_confirmation_gate(manifest,old,mid,mid,digest)==OTA_BOOT_CONFIRM_TARGET);
    manifest.target_id=ota_bootloader_target_id(p1); manifest.hw_id=xhw;
    assert(ota_bootloader_confirmation_gate(manifest,old,mid,mid,digest)==OTA_BOOT_CONFIRM_HW_ID);
    manifest.hw_id=hw; uint8_t wrong[8]={};
    assert(ota_bootloader_confirmation_gate(manifest,old,mid,wrong,digest)==OTA_BOOT_CONFIRM_MID);
    assert(ota_bootloader_confirmation_gate(manifest,old,mid,mid,wrong)==OTA_BOOT_CONFIRM_IMAGE_HASH);
  }
}
'''

UTILS = '''#pragma once
#include <stdint.h>
#include <stddef.h>
namespace mesh { class Utils { public:
static void sha256(uint8_t*,size_t,const uint8_t*,int);
static void sha256(uint8_t*,size_t,const uint8_t*,int,const uint8_t*,int);
}; }
'''

CLI_HELPERS = r'''
struct CliContext {
  OtaBootloaderIdentity identity;
  OtaBlCaps caps;
  const OtaBootloaderIdentity& bootloaderIdentity() const { return identity; }
  const OtaBlCaps& bootloaderUpdateCaps() const { return caps; }
};
static bool cli_pull_rejected(CliContext& c,uint32_t seltgt,uint8_t selflags,
                              uint8_t selcodec,char* reply) {
  const bool selboot=true;
  @PULL_BRANCH@
  return false;
}
static bool cli_list_fit(CliContext& c,uint32_t target,uint8_t flags,uint8_t codec) {
  struct Row { uint32_t target_id; uint8_t flags,codec; } row{target,flags,codec};
  const Row* h=&row;
  const OtaBootloaderIdentity& bid=c.bootloaderIdentity();
  const OtaBlCaps& list_bl_update=c.bootloaderUpdateCaps();
  bool installable=false;
  @LIST_ASSIGNMENT@
  return installable;
}
'''


def runtime_source():
    cli = (ROOT / 'src/helpers/ota/OtaCli.cpp').read_text()
    assignment = re.search(r'installable = h->flags.*?;', cli, re.S)
    if assignment is None:
        raise AssertionError('production bootloader catalog fit assignment not found')
    helpers = (CLI_HELPERS.replace('@PULL_BRANCH@', body(cli, 'if (selboot)'))
               .replace('@LIST_ASSIGNMENT@', assignment.group()))
    return SOURCE.replace('@CLI_HELPERS@', helpers)


class SensecapBootloaderRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='sensecap-runtime-')
        cls.addClassCleanup(cls.directory.cleanup)
        cls.path = Path(cls.directory.name)
        (cls.path / 'Utils.h').write_text(UTILS)
        (cls.path / 'test.cpp').write_text(runtime_source())
        for name, image in {
            'p1.bin': bootloader(), 'p1-old.bin': bootloader(version=0x02040BFF),
            'xiao-base.bin': bootloader('XIAO_DFU', version=0x02040BFF),
            'xiao-sense.bin': bootloader('XIAO_DFU', 0x28860045, 0x02040BFF),
        }.items():
            (cls.path / name).write_bytes(image)
        duplicate = bytearray(bootloader())
        duplicate[0x400:0x400+76] = bootloader('XIAO_DFU')[OFFSET:]
        _set_two_valid_xiao_manifest_crcs(duplicate, 0x400, OFFSET)
        assert _xiao_manifest_crc(duplicate, 0x400) == struct.unpack_from('<I', duplicate, 0x428)[0]
        (cls.path / 'duplicate.bin').write_bytes(duplicate)
        cls.binary = cls.compile(cls.path)

    @staticmethod
    def compile(path, include=None):
        flags = [] if os.name == 'nt' else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie']
        binary = path / 'runtime'
        command = [os.environ.get('CXX', 'g++'), '-std=c++17', '-O1', '-Wall', '-Wextra', '-Werror', *flags]
        command += ['-DNRF52_PLATFORM=1', '-DOTA_QSPI_BOOTLOADER_UPDATE=1',
                    '-DOTA_QSPI_STORE=1', '-DXIAO_NRF52=1']
        if include:
            command += ['-I', str(include)]
        crypto_library = '-lbcrypt' if os.name == 'nt' else '-lcrypto'
        command += ['-I', str(path), '-I', str(ROOT / 'src'), '-I', str(ROOT / 'src/helpers/ota'),
                    str(path / 'test.cpp'), crypto_library, '-o', str(binary)]
        subprocess.run(command, capture_output=True, text=True, check=True)
        return binary

    def scenario(self, name):
        subprocess.run([str(self.binary), str(self.path), name], check=True)

    def test_p1_canonical_signed_identity_uses_real_sha256(self):
        hw = b'NRF_BL_28860044_SCAP_DFU'.ljust(32, b'\0')
        self.assertEqual(int.from_bytes(hashlib.sha256(hw).digest()[:4], 'little'), 0xD677B472)
        self.scenario('canonical')

    def test_legacy_xiao_pairing_and_ids_remain_exact(self):
        self.scenario('legacy')

    def test_real_qspi_scanner_requires_crc_and_exact_storage(self):
        self.scenario('qspi')

    def test_embedded_migration_requires_exact_identity_and_strict_upgrade(self):
        self.scenario('migration')

    def test_two_independently_crc_valid_identities_are_rejected(self):
        self.scenario('duplicate')

    def test_signed_package_confirmation_cannot_cross_xiao_and_p1(self):
        self.scenario('confirmation')

    def test_xiao_successors_accept_both_variants_and_bind_the_signed_package_identity(self):
        self.scenario('xiao-successor')

    def test_real_cli_lists_and_admits_both_xiao_variants_without_weakening_other_guards(self):
        self.scenario('xiao-cli')

    def test_negative_control_board_only_target_shortcut_is_caught(self):
        header = (ROOT / 'src/helpers/ota/OtaBootloaderUpdate.h').read_text()
        changed = header.replace('if (ota_xiao_bootloader_identity(board_id, device_name)) return board_id;',
                                 'if (ota_xiao_bootloader_board_id(board_id)) return board_id;')
        self.assertNotEqual(header, changed)
        with tempfile.TemporaryDirectory(prefix='sensecap-shortcut-negative-') as directory:
            path = Path(directory)
            headers = path / 'helpers/ota'
            headers.mkdir(parents=True)
            (headers / 'OtaBootloaderUpdate.h').write_text(changed)
            (headers / 'BootloaderMigrationPolicy.h').write_text(
                (ROOT / 'src/helpers/ota/BootloaderMigrationPolicy.h').read_text())
            (path / 'Utils.h').write_text(UTILS)
            (path / 'test.cpp').write_text(runtime_source())
            binary = self.compile(path, path)
            result = subprocess.run([str(binary), str(self.path), 'canonical'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('ota_bootloader_target_id(p1)==0xD677B472', result.stderr)


if __name__ == '__main__':
    unittest.main()
