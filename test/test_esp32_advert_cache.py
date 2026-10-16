#!/usr/bin/env python3
"""Exercise production ESP advert jobs and their MyMesh route under sanitizers."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
from test_t096_full_memory import method
from test_serial_wifi_sessions import SANITIZER_FLAGS

ROOT = Path(__file__).resolve().parents[1]


class AdvertCacheTest(unittest.TestCase):
    def test_inline_contacts(self):
        self.run_fixture(0)

    def test_cached_contacts(self):
        self.run_fixture(1)

    def test_synchronous_received_advert_is_detected(self):
        self.run_fixture(0, synchronous=True)

    def test_contact_sharing_validates_restored_cached_adverts(self):
        self.run_share_fixture()

    def test_unchecked_cached_advert_share_negative_control(self):
        self.run_share_fixture(legacy=True)

    def run_share_fixture(self, legacy=False):
        compiler = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        if compiler is None:
            self.skipTest('a host C++17 compiler is required')
        store = (ROOT / 'examples/companion_radio/DataStore.cpp').read_text()
        header = (ROOT / 'examples/companion_radio/DataStore.h').read_text()
        start = header.index('  struct AdvertWriteState {')
        state = header[start:header.index('\n#endif', start)]
        blob = store[store.rindex('inline void makeBlobPath('):]
        presence = method(store, 'static bool companionPathPresence(').replace(
            '::stat(vfs_path, &info)', 'fixtureStat(fs, vfs_path, &info)')
        implementation = '\n'.join((presence, method(store, 'static File openWrite(')))
        implementation += '\n' + '\n'.join(method(blob, signature) for signature in (
            'inline void makeBlobPath(', 'uint8_t DataStore::getBlobByKey(',
            'bool DataStore::putBlobByKey('))
        implementation += '\n' + '\n'.join(method(store, signature) for signature in (
            'void DataStore::invalidateAdvertWrite(', 'void DataStore::retireAdvertWrite(',
            'bool DataStore::queueAdvertByKey('))
        share = method((ROOT / 'src/helpers/BaseChatMesh.cpp').read_text(),
                       'bool BaseChatMesh::shareContactZeroHop(')
        if legacy:
            start = share.index('  // A cache read may be short or corrupt.')
            stop = share.index('  uint16_t codes[2];', start)
            share = share[:start] + "  packet->readFrom(temp_buf, plen);\n" + share[stop:]
        obtain = method((ROOT / 'src/Dispatcher.cpp').read_text(),
                        'Packet* Dispatcher::obtainNewPacket()').replace(
                            'Packet* Dispatcher::', 'mesh::Packet* BaseChatMesh::', 1)
        zero_hop = method((ROOT / 'src/Mesh.cpp').read_text(),
                          'void Mesh::sendZeroHop(Packet* packet, uint16_t*').replace(
                              'void Mesh::sendZeroHop(Packet*',
                              'void BaseChatMesh::sendZeroHop(mesh::Packet*', 1)
        scenarios = ('valid', 'valid-reused', 'valid-min', 'valid-max', 'ram-valid',
                     'short1', 'short3', 'truncated-path', 'reserved-path', 'bad-version',
                     'truncated-transport', 'wrong-type', 'short-advert', 'oversize-advert',
                     'wrong-key', 'ram-wrong-key', 'empty-cache', 'missing-cache', 'no-allocation')
        controls = {'valid', 'valid-reused', 'valid-min', 'valid-max', 'ram-valid',
                    'empty-cache', 'missing-cache', 'no-allocation'}
        fixture = ROOT / 'test/fixtures/advert_cache'
        with tempfile.TemporaryDirectory(prefix='mesh-advert-share-') as directory:
            temp = Path(directory)
            (temp / 'advert_state.h').write_text(state, encoding='ascii')
            (temp / 'share_store.h').write_text(implementation, encoding='ascii')
            (temp / 'share_methods.h').write_text('\n'.join((obtain, zero_hop, share)),
                                                encoding='ascii')
            binary = temp / 'share.exe'
            built = subprocess.run([
                compiler, '-std=c++17', '-O1', '-g', '-Wall', '-Wextra',
                '-Wno-unused-parameter', '-Wno-reorder', *SANITIZER_FLAGS,
                '-DESP32_PLATFORM=1', '-I' + str(fixture / 'share_mocks'),
                '-I' + str(ROOT / 'test/mocks'), '-I' + str(fixture),
                '-I' + str(temp), '-I' + str(ROOT / 'src'),
                str(fixture / 'share_contact.cpp'), str(ROOT / 'src/Packet.cpp'),
                str(ROOT / 'src/helpers/StaticPoolPacketManager.cpp'), '-o', str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            # The actual pool owns firmware-lifetime allocations and has no
            # destructor. Keep ASan/UBSan active without treating those as leaks.
            environment = dict(os.environ, ASAN_OPTIONS='detect_leaks=0')
            for scenario in scenarios:
                with self.subTest(scenario=scenario, legacy=legacy):
                    result = subprocess.run([str(binary), scenario], capture_output=True,
                                            text=True, timeout=10, env=environment)
                    if legacy and scenario not in controls:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn('invalid cached advert must fail without transmission',
                                      result.stderr)
                    else:
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertIn('PASS: cached advert share ' + scenario, result.stdout)

    def run_fixture(self, cache, synchronous=False):
        source = (ROOT / 'examples/companion_radio/DataStore.cpp').read_text()
        header = (ROOT / 'examples/companion_radio/DataStore.h').read_text()
        start = header.index('  struct AdvertWriteState {')
        end = header.index('\n#endif', start)
        state = header[start:end]
        signatures = ('static File openWrite(', 'inline void makeBlobPath(', 'bool DataStore::queueAdvertByKey(',
                      'void DataStore::retireAdvertWrite(', 'void DataStore::invalidateAdvertWrite(',
                      'bool DataStore::consumeSynchronousAdvertIO(',
                      'bool DataStore::hasPendingAdvertWrites() const',
                      'bool DataStore::isAdvertWriteDue(', 'bool DataStore::serviceAdvertWrites(',
                      'bool DataStore::flushAdvertWrites(', 'void DataStore::begin()',
                      'bool DataStore::formatFileSystem()')
        # ESP/non-LittleFS blob methods follow the final makeBlobPath helper.
        blob_source = source[source.rindex('inline void makeBlobPath('):]
        implementation = '\n'.join(method(source, s) for s in signatures)
        implementation += '\n' + '\n'.join(method(blob_source, s) for s in (
            'uint8_t DataStore::getBlobByKey(', 'bool DataStore::putBlobByKey(',
            'bool DataStore::deleteBlobByKey('))
        presence = method(source, 'static bool companionPathPresence(').replace(
            '::stat(vfs_path, &info)', 'fixtureStat(fs, vfs_path, &info)')
        mesh_header = (ROOT / 'examples/companion_radio/MyMesh.h').read_text()
        route = method(mesh_header, '  bool putBlobByKey(').replace(' override', '')
        route = route.replace('bool putBlobByKey(', 'bool MyMesh::putBlobByKey(', 1)
        if synchronous:
            route = route.replace('_store->queueAdvertByKey(', '_store->putBlobByKey(')
        mesh_source = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        scheduling = method(mesh_source, 'void MyMesh::servicePersistence()')
        flush = method(mesh_source, 'bool MyMesh::flushContactsBeforeReboot()')
        work = method(mesh_source, 'bool MyMesh::hasPendingWork() const')
        recovery = method(mesh_source, 'bool MyMesh::canRecoverUsbLogging() const')
        # The entire production scheduling body runs below. This order check
        # additionally pins its call site after either serial dispatch route.
        loop = method(mesh_source, 'void MyMesh::loop()')
        self.assertLess(loop.index('checkSerialInterface();'), loop.index('servicePersistence();'))
        self.assertLess(loop.index('checkCLIRescueCmd();'), loop.index('servicePersistence();'))
        with tempfile.TemporaryDirectory(prefix='mesh-advert-jobs-') as directory:
            temp = Path(directory)
            (temp / 'advert_state.h').write_text(state)
            (temp / 'presence.h').write_text(presence)
            (temp / 'store.h').write_text(implementation)
            (temp / 'mesh.h').write_text('\n'.join((route, scheduling, flush, work, recovery)))
            binary = temp / 'test'
            result = subprocess.run([
                'c++', '-std=c++17', '-O1', '-g', '-Wall', '-Wextra',
                '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                '-DESP32_PLATFORM=1', '-DESP32=1', f'-DMESH_CONTACT_CACHE={cache}',
                '-I', str(ROOT / 'test/fixtures/advert_cache'),
                # Ensure the generated mesh.h wins over Mesh.h on case-folded
                # Windows/WSL filesystems as well as case-sensitive CI hosts.
                '-I', str(temp), '-I', str(ROOT / 'src'),
                str(ROOT / 'test/fixtures/advert_cache/test.cpp'), '-o', str(binary),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            if synchronous:
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('advert_rx_no_fs', result.stderr)
            else:
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)


if __name__ == '__main__':
    unittest.main()
