"""Test the authored IPA host against a native mock DSO, never vendor code."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("c++"), "native C++ compiler unavailable")
class CameraIPAWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="ciri-ipa-test.")
        cls.work = Path(cls.directory.name)
        mock = cls.work / "mock.cpp"
        mock.write_text(r'''
#include <cerrno>
#include <csignal>
#include <cstdlib>
#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>
extern "C" int ipaRun(int fd)
{
    if (!(fcntl(fd, F_GETFL) & O_NONBLOCK)) return 11;
    if (!(fcntl(fd, F_GETFD) & FD_CLOEXEC)) return 12;
    pollfd p{fd, POLLIN, 0};
    if (poll(&p, 1, 2000) != 1) return 13;
    char command;
    iovec io{&command, 1};
    alignas(cmsghdr) char ancillary[CMSG_SPACE(sizeof(int))]{};
    msghdr message{};
    message.msg_iov = &io;
    message.msg_iovlen = 1;
    message.msg_control = ancillary;
    message.msg_controllen = sizeof(ancillary);
    if (recvmsg(fd, &message, 0) != 1 || command != 'F') return 14;
    cmsghdr *header = CMSG_FIRSTHDR(&message);
    if (!header || header->cmsg_level != SOL_SOCKET || header->cmsg_type != SCM_RIGHTS)
        return 15;
    int transferred = *reinterpret_cast<int *>(CMSG_DATA(header));
    char byte;
    if (read(transferred, &byte, 1) != 1 || byte != 'Z') return 16;
    close(transferred);
    if (getenv("MOCK_IPA_HANG")) signal(SIGTERM, SIG_IGN);
    if (send(fd, "K", 1, MSG_NOSIGNAL) != 1) return 17;
    if (getenv("MOCK_IPA_HANG")) while (true) pause();
    if (poll(&p, 1, 2000) != 1 || read(fd, &byte, 1) != 0) return 18;
    close(fd);
    return 0;
}
''')
        harness = cls.work / "host.cpp"
        harness.write_text(r'''
#include "ciri-camera-ipa-worker.h"
int main(int argc, char **argv)
{
    if (argc != 4) return 2;
    {
        StandaloneIPAWorker worker(argv[1], argv[2]);
        if (!worker.start()) return 3;
        if (std::strcmp(argv[3], "idle") != 0) {
            int fd = socket(AF_UNIX, SOCK_STREAM, 0);
            sockaddr_un address{};
            address.sun_family = AF_UNIX;
            std::strcpy(address.sun_path, argv[1]);
            if (connect(fd, reinterpret_cast<sockaddr *>(&address), sizeof(address))) return 4;
            int pipefd[2];
            if (pipe(pipefd)) return 5;
            if (write(pipefd[1], "Z", 1) != 1) return 6;
            close(pipefd[1]);
            char command = 'F';
            iovec io{&command, 1};
            alignas(cmsghdr) char ancillary[CMSG_SPACE(sizeof(int))]{};
            msghdr message{};
            message.msg_iov = &io;
            message.msg_iovlen = 1;
            message.msg_control = ancillary;
            message.msg_controllen = sizeof(ancillary);
            cmsghdr *header = CMSG_FIRSTHDR(&message);
            header->cmsg_level = SOL_SOCKET;
            header->cmsg_type = SCM_RIGHTS;
            header->cmsg_len = CMSG_LEN(sizeof(int));
            std::memcpy(CMSG_DATA(header), &pipefd[0], sizeof(int));
            if (sendmsg(fd, &message, MSG_NOSIGNAL) != 1) return 7;
            close(pipefd[0]);
            pollfd p{fd, POLLIN, 0};
            if (poll(&p, 1, 2000) != 1 || read(fd, &command, 1) != 1 || command != 'K') return 8;
            close(fd);
        }
    }
    // Destruction must reap the owned child and remove only its own socket.
    if (waitpid(-1, nullptr, WNOHANG) != -1 || errno != ECHILD) return 9;
    if (access(argv[1], F_OK) == 0) return 10;
    return 0;
}
''')
        cls.library = cls.work / "mock.so"
        cls.host = cls.work / "host"
        for command in (
            ["c++", "-shared", "-fPIC", "-O2", "-Wall", "-Wextra", "-Werror",
             str(mock), "-o", str(cls.library)],
            ["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread",
             f"-I{ROOT / 'scripts'}", str(harness), "-ldl", "-o", str(cls.host)],
        ):
            subprocess.run(command, check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def run_host(self, library=None, mode="capture", hang=False):
        socket = self.work / "camera.sock"
        environment = dict(os.environ)
        environment.pop("MOCK_IPA_HANG", None)
        if hang:
            environment["MOCK_IPA_HANG"] = "1"
        result = subprocess.run(
            [str(self.host), str(socket), str(library or self.library), mode],
            env=environment, capture_output=True, text=True, timeout=5,
        )
        return result, socket

    def test_existing_worker_entrypoint_receives_stream_socket_and_dma_fd(self):
        result, socket = self.run_host()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("client connected", result.stderr)
        self.assertIn("ipaRun returned 0", result.stderr)
        self.assertFalse(socket.exists())

    def test_missing_worker_fails_readiness_and_cleans_socket(self):
        result, socket = self.run_host(library=self.work / "missing.so")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("IPA worker dlopen", result.stderr)
        self.assertFalse(socket.exists())

    def test_missing_entrypoint_fails_readiness(self):
        empty = self.work / "empty.cpp"
        empty.write_text('extern "C" int not_ipaRun() { return 0; }\n')
        library = self.work / "empty.so"
        subprocess.run(["c++", "-shared", "-fPIC", str(empty), "-o", str(library)], check=True)
        result, socket = self.run_host(library=library)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("IPA worker ipaRun", result.stderr)
        self.assertFalse(socket.exists())

    def test_existing_path_is_not_deleted_or_overwritten(self):
        socket = self.work / "camera.sock"
        socket.write_text("leave this alone\n")
        try:
            result, _ = self.run_host()
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertEqual(socket.read_text(), "leave this alone\n")
        finally:
            socket.unlink()

    def test_no_connection_worker_is_stopped_and_reaped(self):
        result, socket = self.run_host(mode="idle")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(socket.exists())

    def test_hung_worker_is_killed_and_reaped(self):
        result, socket = self.run_host(hang=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("client connected", result.stderr)
        self.assertFalse(socket.exists())

    def test_worker_is_started_only_for_capture_before_camera_manager(self):
        source = (ROOT / "scripts/ciri-camera-probe.cpp").read_text()
        self.assertIn("if (capture && !ipaWorker.start())", source)
        self.assertLess(source.index("if (capture && !ipaWorker.start())"),
                        source.index("    CameraManager manager;"))
        self.assertIn('"/run/camera/camera-algo-libcamera.sock"', source)
        self.assertIn('"/usr/lib64/mtkisp7_ipa_proxy.so"', source)


if __name__ == "__main__":
    unittest.main()
