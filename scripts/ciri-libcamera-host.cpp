// SPDX-License-Identifier: MIT
// Host the recovered IPA DSO, then run a standard cam or GStreamer client.
// No camera allocation, request loop or image processing belongs in this helper.
#include "ciri-camera-ipa-worker.h"
#include <vector>

namespace {
volatile sig_atomic_t clientPid = -1;
volatile sig_atomic_t stopped = 0;
void forwardSignal(int signal)
{
    stopped = signal;
    if (clientPid > 0)
        kill(static_cast<pid_t>(clientPid), signal);
}
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        std::fprintf(stderr, "Usage: ciri-libcamera-host CAM_ARGUMENTS...\n");
        return 2;
    }
    const char *program = "/usr/bin/cam";
    int firstArgument = 1;
    if (std::strcmp(argv[1], "--gst-launch") == 0) {
        program = "/usr/bin/gst-launch-1.0";
        firstArgument = 2;
        if (argc < 3)
            return 2;
    } else if (std::strcmp(argv[1], "--gst-inspect") == 0) {
        program = "/usr/bin/gst-inspect-1.0";
        firstArgument = 2;
    }
    if (chdir("/captures")) {
        std::perror("Open private capture directory");
        return 1;
    }
    struct sigaction action{};
    action.sa_handler = forwardSignal;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGINT, &action, nullptr) ||
        sigaction(SIGTERM, &action, nullptr))
        return 1;

    // Fork/load the IPA before starting the client's CameraManager threads.
    // Unlike the old diagnostic, this supervisor does not link libcamera at
    // startup. Export the worker's dependency scope so its separately dlopened
    // IPA resolves the same libcamera interface RTTI (libc++ uses pointer identity).
    StandaloneIPAWorker worker("/run/camera/camera-algo-libcamera.sock",
                               "/usr/lib64/mtkisp7_ipa_proxy.so", RTLD_GLOBAL);
    if (!worker.start() || stopped)
        return stopped ? 128 + stopped : 1;

    std::vector<char *> command{
        const_cast<char *>("/lib64/ld-linux-aarch64.so.1"),
        const_cast<char *>("--library-path"),
        const_cast<char *>("/usr/lib/ciri-libcamera:/usr/lib64:/lib64:/usr/share/cros-camera/libfs"),
        const_cast<char *>(program)
    };
    for (int i = firstArgument; i < argc; ++i)
        command.push_back(argv[i]);
    command.push_back(nullptr);

    const pid_t parent = getpid();
    pid_t child = fork();
    if (child < 0) {
        std::perror("Fork cam");
        return 1;
    }
    if (!child) {
        if (prctl(PR_SET_PDEATHSIG, SIGKILL) || getppid() != parent)
            _exit(1);
        execv(command[0], command.data());
        std::perror("Execute cam through recovered loader");
        _exit(127);
    }
    clientPid = child;
    if (stopped)
        kill(child, stopped);
    int status = 0;
    pid_t result;
    do {
        result = waitpid(child, &status, 0);
    } while (result < 0 && errno == EINTR);
    clientPid = -1;
    if (result < 0) {
        std::perror("Wait for cam");
        return 1;
    }
    // Worker RAII closes/reaps only our own IPA process and socket.
    return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
}
