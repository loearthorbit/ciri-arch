"""Model IPA RTTI interposition with native mock DSOs; no vendor execution."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which('c++'), 'native compiler unavailable')
class IPAScopeTests(unittest.TestCase):
    def test_worker_and_module_share_interface_rtti_only_with_global_scope(self):
        with tempfile.TemporaryDirectory(prefix='ciri-ipa-scope.') as directory:
            work = Path(directory)
            (work / 'interface.h').write_text('''
#include <typeinfo>
struct Interface { virtual ~Interface() = default; virtual int run() = 0; };
''')
            (work / 'module.cpp').write_text('''
#include "interface.h"
extern "C" const std::type_info *module_rtti() { return &typeid(Interface); }
''')
            (work / 'worker.cpp').write_text('''
#include "interface.h"
#include <cstdlib>
#include <dlfcn.h>
#include <sys/socket.h>
#include <unistd.h>
extern "C" int ipaRun(int fd) {
    void *module = dlopen(getenv("MOCK_IPA_MODULE"), RTLD_NOW | RTLD_LOCAL);
    if (!module) return 1;
    auto rtti = reinterpret_cast<const std::type_info *(*)()>(dlsym(module, "module_rtti"));
    if (!rtti) return 2;
    char result = rtti() == &typeid(Interface) ? 'G' : 'L';
    if (send(fd, &result, 1, MSG_NOSIGNAL) != 1) return 3;
    close(fd);
    return 0;
}
''')
            (work / 'host.cpp').write_text('''
#include "ciri-camera-ipa-worker.h"
#include <cstdlib>
int main(int argc, char **argv) {
    if (argc != 5 || setenv("MOCK_IPA_MODULE", argv[3], 1)) return 1;
    StandaloneIPAWorker worker(argv[1], argv[2], argv[4][0] == 'G' ? RTLD_GLOBAL : RTLD_LOCAL);
    if (!worker.start()) return 2;
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    sockaddr_un address{}; address.sun_family = AF_UNIX;
    std::strcpy(address.sun_path, argv[1]);
    if (connect(fd, reinterpret_cast<sockaddr *>(&address), sizeof(address))) return 3;
    pollfd p{fd, POLLIN, 0};
    if (poll(&p, 1, 2000) != 1) return 4;
    char result;
    if (read(fd, &result, 1) != 1) return 5;
    close(fd);
    return result == argv[4][0] ? 0 : 6;
}
''')
            for name in ('module', 'worker'):
                subprocess.run(['c++', '-std=c++17', '-shared', '-fPIC', '-O2',
                                str(work / (name + '.cpp')), '-ldl', '-o', str(work / (name + '.so'))],
                               check=True, capture_output=True, text=True)
            subprocess.run(['c++', '-std=c++17', '-O2', '-pthread', '-I' + str(ROOT / 'scripts'),
                            str(work / 'host.cpp'), '-ldl', '-o', str(work / 'host')],
                           check=True, capture_output=True, text=True)
            for scope in ('L', 'G'):
                result = subprocess.run([str(work / 'host'), str(work / 'camera.sock'),
                                         str(work / 'worker.so'), str(work / 'module.so'), scope],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse((work / 'camera.sock').exists())
        host = (ROOT / 'scripts/ciri-libcamera-host.cpp').read_text()
        self.assertIn('"/usr/lib64/mtkisp7_ipa_proxy.so", RTLD_GLOBAL', host)
        diagnostic = (ROOT / 'scripts/ciri-camera-ipa-worker.h').read_text()
        self.assertIn('int libraryScope = RTLD_LOCAL', diagnostic)


if __name__ == '__main__':
    unittest.main()
