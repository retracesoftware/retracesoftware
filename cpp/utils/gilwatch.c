/*
 * gilwatch — preload library for detecting GIL thread switches.
 *
 * Interposes pthread_mutex_lock. When the locked mutex matches a
 * watched address AND the acquiring thread differs from the last
 * holder, invokes a callback with both thread IDs.
 *
 * Before activation (watched address is NULL) the overhead per
 * mutex call is a single relaxed atomic load + branch (~1ns).
 *
 * Usage:
 *   macOS:  DYLD_INSERT_LIBRARIES=libgilwatch.dylib python …
 *   Linux:  LD_PRELOAD=libgilwatch.so python …
 *
 * A separate Python C extension later calls dlsym(RTLD_DEFAULT, …)
 * to locate gilwatch_mutex_address and gilwatch_callback, stores the
 * GIL mutex pointer and a recording callback, and the interposer
 * becomes active.
 */

#include <pthread.h>
#include <dlfcn.h>
#include <stdatomic.h>
#include <stdint.h>

/* ------------------------------------------------------------------ */
/* Public symbols — discovered via dlsym by the Python extension      */
/* ------------------------------------------------------------------ */

/* Address of the mutex to watch (initially NULL → dormant). */
atomic_uintptr_t gilwatch_mutex_address = 0;

/* Callback invoked on a thread switch.
 *   previous: pthread_t of the thread that last held the mutex
 *   current:  pthread_t of the thread that just acquired it
 */
void (*gilwatch_callback)(pthread_t previous, pthread_t current) = NULL;

/* ------------------------------------------------------------------ */
/* Internal state                                                     */
/* ------------------------------------------------------------------ */

static pthread_t last_holder = 0;

/* ------------------------------------------------------------------ */
/* Platform-specific interposition                                    */
/* ------------------------------------------------------------------ */

#if defined(__APPLE__)

/* macOS: use dyld interpose (reliable, doesn't need dlsym at init). */

static int gilwatch_pthread_mutex_lock(pthread_mutex_t *mutex) {
    int ret = pthread_mutex_lock(mutex);
    if ((uintptr_t)mutex == atomic_load_explicit(&gilwatch_mutex_address,
                                                  memory_order_relaxed)) {
        pthread_t self = pthread_self();
        pthread_t prev = last_holder;
        if (prev != self) {
            last_holder = self;
            void (*cb)(pthread_t, pthread_t) = gilwatch_callback;
            if (cb) cb(prev, self);
        }
    }
    return ret;
}

__attribute__((used, section("__DATA,__interpose")))
static struct {
    void *replacement;
    void *original;
} gilwatch_interpose_lock = {
    (void *)gilwatch_pthread_mutex_lock,
    (void *)pthread_mutex_lock
};

#else /* Linux / other POSIX */

/* Linux: classic LD_PRELOAD symbol override with dlsym(RTLD_NEXT). */

static int (*real_pthread_mutex_lock)(pthread_mutex_t *) = NULL;

__attribute__((constructor))
static void gilwatch_init(void) {
    real_pthread_mutex_lock = dlsym(RTLD_NEXT, "pthread_mutex_lock");
}

int pthread_mutex_lock(pthread_mutex_t *mutex) {
    int ret = real_pthread_mutex_lock(mutex);
    if ((uintptr_t)mutex == atomic_load_explicit(&gilwatch_mutex_address,
                                                  memory_order_relaxed)) {
        pthread_t self = pthread_self();
        pthread_t prev = last_holder;
        if (prev != self) {
            last_holder = self;
            void (*cb)(pthread_t, pthread_t) = gilwatch_callback;
            if (cb) cb(prev, self);
        }
    }
    return ret;
}

#endif
