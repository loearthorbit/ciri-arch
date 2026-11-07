// Start the recovered IPA worker for one standalone capture session.
// This runs only inside the launcher's private vendor rootfs, before libcamera
// starts threads. The existing worker owns the accepted fd via ipaRun(int).
#pragma once

#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <dlfcn.h>
#include <fcntl.h>
#include <poll.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>

class StandaloneIPAWorker {
public:
    StandaloneIPAWorker(const char *socketPath, const char *libraryPath,
                        int libraryScope = RTLD_LOCAL)
        : socketPath_(socketPath), libraryPath_(libraryPath), libraryScope_(libraryScope) {}
    StandaloneIPAWorker(const StandaloneIPAWorker &) = delete;
    StandaloneIPAWorker &operator=(const StandaloneIPAWorker &) = delete;
    ~StandaloneIPAWorker()
    {
        if (pid_ > 0 && !waitForExit()) {
            // Stop only our own child; never signal unrelated camera services.
            kill(pid_, SIGTERM);
            if (!waitForExit()) {
                kill(pid_, SIGKILL);
                while (waitpid(pid_, nullptr, 0) < 0 && errno == EINTR) {}
            }
        }
        if (ownsSocket_)
            unlink(socketPath_);
    }

    bool start()
    {
        if (pid_ > 0 || ownsSocket_)
            return false;
        sockaddr_un address{};
        address.sun_family = AF_UNIX;
        if (std::strlen(socketPath_) >= sizeof(address.sun_path)) {
            errno = ENAMETOOLONG;
            return error("Socket path too long");
        }
        std::strcpy(address.sun_path, socketPath_);
        int listener = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        if (listener < 0)
            return error("Create listener");
        if (bind(listener, reinterpret_cast<sockaddr *>(&address), sizeof(address))) {
            int saved = errno;
            close(listener);
            errno = saved;
            return error("Bind listener (existing paths are not removed)");
        }
        ownsSocket_ = true;
        if (chmod(socketPath_, 0600) || listen(listener, 1)) {
            int saved = errno;
            close(listener);
            errno = saved;
            return error("Prepare listener");
        }
        int ready[2];
        if (pipe2(ready, O_CLOEXEC)) {
            int saved = errno;
            close(listener);
            errno = saved;
            return error("Create readiness pipe");
        }
        const pid_t parent = getpid();
        pid_ = fork();
        if (pid_ == 0) {
            close(ready[0]);
            // The worker moves to its own process group. Still terminate it if
            // the diagnostic dies, including when it crashes under GDB.
            if (prctl(PR_SET_PDEATHSIG, SIGKILL) || getppid() != parent)
                _exit(1);
            void *library = dlopen(libraryPath_, RTLD_NOW | libraryScope_);
            if (!library) {
                std::fprintf(stderr, "IPA worker dlopen: %s\n", dlerror());
                _exit(1);
            }
            auto run = reinterpret_cast<int (*)(int)>(dlsym(library, "ipaRun"));
            if (!run) {
                std::fprintf(stderr, "IPA worker ipaRun: %s\n", dlerror());
                _exit(1);
            }
            if (write(ready[1], "R", 1) != 1)
                _exit(1);
            close(ready[1]);
            int connection;
            do {
                connection = accept4(listener, nullptr, nullptr,
                                     SOCK_CLOEXEC | SOCK_NONBLOCK);
            } while (connection < 0 && errno == EINTR);
            close(listener);
            if (connection < 0) {
                error("Accept client");
                _exit(1);
            }
            std::fprintf(stderr, "Standalone IPA worker: client connected\n");
            // Do not close connection or dlclose here: ipaRun takes fd ownership
            // and vendor event threads may still refer to the loaded module.
            int result = run(connection);
            std::fprintf(stderr, "Standalone IPA worker: ipaRun returned %d\n", result);
            _exit(result == 0 ? 0 : 1);
        }
        int saved = errno;
        close(listener);
        close(ready[1]);
        if (pid_ < 0) {
            close(ready[0]);
            errno = saved;
            return error("Fork worker");
        }
        pollfd notify{ready[0], POLLIN, 0};
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(15);
        int result;
        do {
            auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(
                deadline - std::chrono::steady_clock::now()).count();
            result = poll(&notify, 1, remaining > 0 ? static_cast<int>(remaining) : 0);
        } while (result < 0 && errno == EINTR);
        char status = 0;
        bool readyToRun = result > 0 && read(ready[0], &status, 1) == 1 && status == 'R';
        close(ready[0]);
        if (!readyToRun) {
            std::fprintf(stderr, "Standalone IPA worker: failed to become ready (15s limit)\n");
            return false;
        }
        std::fprintf(stderr, "Standalone IPA worker: listening at %s (pid %d)\n",
                     socketPath_, static_cast<int>(pid_));
        return true;
    }

private:
    static bool error(const char *operation)
    {
        std::fprintf(stderr, "Standalone IPA worker: %s: %s\n", operation, std::strerror(errno));
        return false;
    }
    bool waitForExit()
    {
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(500);
        do {
            int status;
            pid_t result = waitpid(pid_, &status, WNOHANG);
            if (result == pid_ || (result < 0 && errno == ECHILD)) {
                pid_ = -1;
                return true;
            }
            if (result < 0 && errno != EINTR)
                return false;
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        } while (std::chrono::steady_clock::now() < deadline);
        return false;
    }
    const char *socketPath_;
    const char *libraryPath_;
    int libraryScope_;
    pid_t pid_ = -1;
    bool ownsSocket_ = false;
};
