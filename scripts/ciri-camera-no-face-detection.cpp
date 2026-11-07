// Standalone capture does not need AIE face detection. Keep the real camera,
// IPA and scheduler; skip only FaceDetector start/stop and FaceDetectTask::run.
// Matching vendor methods are exported and use interposable PLT/vtable entries.
// This shim intentionally uses the pinned vendor internal Task ABI.
#define LIBCAMERA_BASE_PRIVATE
#include <libcamera/internal/task_scheduler.h>

#include <cstdio>

// These ELF aliases implement the inspected Itanium C++ method ABI (implicit
// this argument). Opaque detector pointers avoid inventing a partial C++ class
// definition or depending on private MediaTek face-library headers.
extern "C" int ciriFaceDetectorStart(void *) asm("_ZN9libcamera12FaceDetector5startEv");
extern "C" int ciriFaceDetectorStop(void *) asm("_ZN9libcamera12FaceDetector4stopEv");
extern "C" void ciriFaceDetectTaskRun(libcamera::Task *) asm("_ZN9libcamera14FaceDetectTask3runEv");

extern "C" int ciriFaceDetectorStart(void *)
{
    std::fprintf(stderr, "Standalone client: AIE face detection disabled\n");
    return 0;
}

extern "C" int ciriFaceDetectorStop(void *)
{
    return 0;
}

extern "C" void ciriFaceDetectTaskRun(libcamera::Task *task)
{
    // FaceDetectTask has Task as its sole base. This is the same completion
    // path used by the vendor run() when shouldRun() is false; do not just
    // return, which would leave capture completion waiting on this task.
    task->libcamera::Task::notifyDone();
}
