#include "utils.h"

#include <structmember.h>
#include <atomic>
#include <chrono>
#include <thread>

namespace retracesoftware {

template <typename T>
static inline void atomic_wait_compat(std::atomic<T> &value, T expected) {
#if defined(__cpp_lib_atomic_wait) && __cpp_lib_atomic_wait >= 201907L
    value.wait(expected);
#else
    while (value.load(std::memory_order_acquire) == expected) {
        std::this_thread::yield();
    }
#endif
}

template <typename T>
static inline void atomic_notify_all_compat(std::atomic<T> &value) {
#if defined(__cpp_lib_atomic_wait) && __cpp_lib_atomic_wait >= 201907L
    value.notify_all();
#else
    (void)value;
#endif
}

static inline int count_interpreter_tstates() {
    PyInterpreterState *interp = PyInterpreterState_Get();
    int count = 0;
    for (PyThreadState *ts = PyInterpreterState_ThreadHead(interp);
         ts != NULL;
         ts = PyThreadState_Next(ts))
        count++;
    return count;
}

static PyObject * const LOADING = (PyObject *)0x2;

static inline bool is_tagged_callback(PyObject *value) {
    return ((uintptr_t)value & 1U) != 0;
}

static inline bool is_buffered_pyobject(PyObject *value) {
    return value != nullptr && value != LOADING && !is_tagged_callback(value);
}

static inline PyObject *get_raised_exception() {
    PyObject *type = nullptr, *value = nullptr, *tb = nullptr;
    PyErr_Fetch(&type, &value, &tb);
    PyErr_NormalizeException(&type, &value, &tb);
    Py_XDECREF(type);
    Py_XDECREF(tb);
    return value;
}

static inline void restore_raised_exception(PyObject *exc) {
    PyErr_Restore(Py_NewRef((PyObject *)Py_TYPE(exc)), Py_NewRef(exc), nullptr);
}

struct Dispatcher : public PyObject {

    PyObject* source;
    std::atomic<PyObject*> buffered;
    std::atomic<unsigned long long> buffered_generation;
    std::atomic<int> num_waiting_threads;
    // Deadlock detection only counts waiters parked on the current buffered item.
    std::atomic<unsigned long long> waiting_generation;
    std::atomic<int> num_waiting_generation_threads;
    long long deadlock_timeout_ns;

    // ------------------------------------------------------------------
    // Init
    // ------------------------------------------------------------------

    static int init(Dispatcher *self, PyObject *args, PyObject *kwds) {
        PyObject *source;
        PyObject *deadlock_timeout_obj = nullptr;
        PyObject *timeout_obj = nullptr;

        static const char *kwlist[] = {
            "source",
            "deadlock_timeout_seconds",
            "timeout_seconds",
            nullptr,
        };

        if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|OO", (char **)kwlist,
                &source, &deadlock_timeout_obj, &timeout_obj))
            return -1;

        if (deadlock_timeout_obj != nullptr && timeout_obj != nullptr) {
            PyErr_SetString(
                PyExc_TypeError,
                "Dispatcher accepts either deadlock_timeout_seconds or timeout_seconds, not both");
            return -1;
        }

        double deadlock_timeout_seconds = 1.0;
        PyObject *selected_timeout =
            deadlock_timeout_obj != nullptr ? deadlock_timeout_obj : timeout_obj;
        if (selected_timeout != nullptr && selected_timeout != Py_None) {
            deadlock_timeout_seconds = PyFloat_AsDouble(selected_timeout);
            if (PyErr_Occurred()) {
                return -1;
            }
        }
        if (deadlock_timeout_seconds < 0.0) {
            PyErr_SetString(PyExc_ValueError, "deadlock timeout must be non-negative");
            return -1;
        }

        self->source = Py_NewRef(source);
        self->buffered.store(nullptr, std::memory_order_relaxed);
        self->buffered_generation.store(0, std::memory_order_relaxed);
        self->num_waiting_threads.store(0, std::memory_order_relaxed);
        self->waiting_generation.store(0, std::memory_order_relaxed);
        self->num_waiting_generation_threads.store(0, std::memory_order_relaxed);
        self->deadlock_timeout_ns =
            static_cast<long long>(deadlock_timeout_seconds * 1000000000.0);

        return 0;
    }

    void store_buffered(PyObject *value) {
        buffered.store(value, std::memory_order_release);
        buffered_generation.fetch_add(1, std::memory_order_release);
        atomic_notify_all_compat(buffered);
    }

    PyObject *exchange_buffered(PyObject *value) {
        PyObject *previous = buffered.exchange(value, std::memory_order_acq_rel);
        buffered_generation.fetch_add(1, std::memory_order_release);
        atomic_notify_all_compat(buffered);
        return previous;
    }

    bool wait_for_buffer_change(PyObject *expected) {
        if (deadlock_timeout_ns <= 0) {
            return buffered.load(std::memory_order_acquire) != expected;
        }

        auto deadline = std::chrono::steady_clock::now()
            + std::chrono::nanoseconds(deadlock_timeout_ns);

        while (buffered.load(std::memory_order_acquire) == expected) {
            auto now = std::chrono::steady_clock::now();
            if (now >= deadline) {
                return false;
            }

            auto remaining = deadline - now;
            if (remaining > std::chrono::milliseconds(1)) {
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
            } else {
                std::this_thread::sleep_for(remaining);
            }
        }

        return true;
    }

    // ------------------------------------------------------------------
    // next(predicate)
    //
    // Block until predicate(buffered_item) is truthy, return the item.
    // ------------------------------------------------------------------

    PyObject *peek_next() {
        while (true) {
            PyObject *next = buffered.load(std::memory_order_acquire);

            if (is_buffered_pyobject(next)) {
                if (source == nullptr) {
                    restore_raised_exception(next);
                    return nullptr;
                }
                return next;
            }

            if (is_tagged_callback(next)) {
                PyObject *cb = (PyObject *)((uintptr_t)next & ~1UL);
                PyObject *result = PyObject_CallNoArgs(cb);
                Py_XDECREF(result);
                if (!result) return nullptr;
                Py_BEGIN_ALLOW_THREADS
                atomic_wait_compat(buffered, next);
                Py_END_ALLOW_THREADS
            } else if (next == LOADING) {
                Py_BEGIN_ALLOW_THREADS
                atomic_wait_compat(buffered, LOADING);
                Py_END_ALLOW_THREADS
            } else {
                if (source == nullptr) {
                    PyErr_SetString(PyExc_RuntimeError, "Dispatcher: terminal state missing exception");
                    return nullptr;
                }
                PyObject *expected = nullptr;
                if (!buffered.compare_exchange_strong(
                        expected,
                        LOADING,
                        std::memory_order_acq_rel,
                        std::memory_order_acquire)) {
                    continue;
                }
                buffered_generation.fetch_add(1, std::memory_order_release);
                next = PyObject_CallNoArgs(source);
                if (!next) {
                    PyObject *exc = get_raised_exception();
                    Py_CLEAR(source);
                    store_buffered(exc);
                    restore_raised_exception(exc);
                    return nullptr;
                }
                store_buffered(next);
                return next;
            }
        }
    }

    static PyObject *peek_method(Dispatcher *self, PyObject *Py_UNUSED(ignored)) {
        PyObject *item = self->peek_next();
        if (!item) {
            return nullptr;
        }
        return Py_NewRef(item);
    }

    static PyObject *next_method(Dispatcher *self, PyObject *predicate) {

        while (true) {
            PyObject * next = self->peek_next();

            if (!next) {
                return nullptr;
            }

            unsigned long long generation =
                self->buffered_generation.load(std::memory_order_acquire);

            PyObject * should_take = PyObject_CallOneArg(predicate, next);

            if (!should_take) {
                return nullptr;
            }

            int truthy = PyObject_IsTrue(should_take);
            Py_DECREF(should_take);
            if (truthy < 0) {
                return nullptr;
            }

            if (truthy) {
                PyObject *taken = self->exchange_buffered(nullptr);
                if (taken) return taken;
            } else {
                unsigned long long active_waiting_generation =
                    self->waiting_generation.load(std::memory_order_acquire);
                int total_waiters =
                    self->num_waiting_threads.load(std::memory_order_acquire);
                int generation_waiters =
                    active_waiting_generation == generation
                        ? self->num_waiting_generation_threads.load(std::memory_order_acquire)
                        : 0;

                bool possible_deadlock = generation_waiters >= total_waiters;

                if (active_waiting_generation != generation) {
                    self->waiting_generation.store(generation, std::memory_order_release);
                    self->num_waiting_generation_threads.store(0, std::memory_order_release);
                }

                self->num_waiting_threads.fetch_add(1, std::memory_order_release);
                self->num_waiting_generation_threads.fetch_add(1, std::memory_order_release);
                atomic_notify_all_compat(self->num_waiting_threads);
                bool changed = false;
                Py_BEGIN_ALLOW_THREADS
                if (possible_deadlock) {
                    changed = self->wait_for_buffer_change(next);
                } else {
                    atomic_wait_compat(self->buffered, next);
                    changed = true;
                }
                self->num_waiting_threads.fetch_sub(1, std::memory_order_release);
                atomic_notify_all_compat(self->num_waiting_threads);
                Py_END_ALLOW_THREADS
                if (self->waiting_generation.load(std::memory_order_acquire) == generation) {
                    self->num_waiting_generation_threads.fetch_sub(1, std::memory_order_release);
                }
                if (
                    !changed
                    && self->buffered.load(std::memory_order_acquire) == next
                    && self->buffered_generation.load(std::memory_order_acquire) == generation
                ) {
                    PyErr_SetString(PyExc_RuntimeError, "Dispatcher: too many threads waiting for item");
                    return nullptr;
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // wait_for_all_pending()
    //
    // Block until every other interpreter thread is in a wait state
    // inside the dispatcher.
    // ------------------------------------------------------------------

    static PyObject *wait_for_all_pending_method(Dispatcher *self, PyObject *Py_UNUSED(ignored)) {
        int target = count_interpreter_tstates() - 1;
        while (self->num_waiting_threads.load(std::memory_order_acquire) < target) {
            int current = self->num_waiting_threads.load(std::memory_order_acquire);
            Py_BEGIN_ALLOW_THREADS
            atomic_wait_compat(self->num_waiting_threads, current);
            Py_END_ALLOW_THREADS
        }
        Py_RETURN_NONE;
    }

    // ------------------------------------------------------------------
    // interrupt(on_waiting_thread, while_interrupted)
    //
    // Inject a tagged callback into buffered so workers call
    // on_waiting_thread, then run while_interrupted on the main
    // thread.  Restore buffered unconditionally on return.
    // ------------------------------------------------------------------

    static PyObject *interrupt_method(Dispatcher *self, PyObject *args) {
        PyObject *on_waiting, *while_interrupted;
        if (!PyArg_ParseTuple(args, "OO", &on_waiting, &while_interrupted))
            return nullptr;

        while (self->buffered.load() == LOADING) {
            Py_BEGIN_ALLOW_THREADS
            atomic_wait_compat(self->buffered, LOADING);
            Py_END_ALLOW_THREADS
        }

        PyObject *saved = self->buffered.load(std::memory_order_acquire);
        PyObject *tagged = (PyObject *)((uintptr_t)on_waiting | 1);
        self->store_buffered(tagged);

        PyObject *result = PyObject_CallNoArgs(while_interrupted);

        self->store_buffered(saved);

        if (!result) return nullptr;
        return result;
    }

    static PyObject *get_buffered(Dispatcher *self, void *) {
        return peek_method(self, nullptr);
    }

    static PyObject *get_waiting_thread_count(Dispatcher *self, void *) {
        return PyLong_FromLong(self->num_waiting_threads.load(std::memory_order_acquire));
    }

    static PyObject *get_source(Dispatcher *self, void *) {
        return Py_NewRef(self->source ? self->source : Py_None);
    }

    // ------------------------------------------------------------------
    // Standard Python type support
    // ------------------------------------------------------------------

    static int traverse(Dispatcher *self, visitproc visit, void *arg) {
        Py_VISIT(self->source);
        PyObject *buf = self->buffered.load(std::memory_order_relaxed);
        if (is_buffered_pyobject(buf)) {
            Py_VISIT(buf);
        }
        return 0;
    }

    static int clear(Dispatcher *self) {
        Py_CLEAR(self->source);
        PyObject *buf = self->exchange_buffered(nullptr);
        if (is_buffered_pyobject(buf)) {
            Py_XDECREF(buf);
        }
        return 0;
    }

    static void dealloc(Dispatcher *self) {
        PyObject_GC_UnTrack(self);
        clear(self);
        Py_TYPE(self)->tp_free(self);
    }
};

static PyMethodDef dispatcher_methods[] = {
    {"peek", (PyCFunction)Dispatcher::peek_method, METH_NOARGS,
     "peek() -> item\n\n"
     "Return the current buffered item, loading from source if needed.\n"
     "Raises the stored terminal exception once source is exhausted."},
    {"next", (PyCFunction)Dispatcher::next_method, METH_O,
     "next(predicate) -> item\n\n"
     "Block until predicate(buffered_item) is truthy, then return\n"
     "the item.  Safepoint-aware: threads hit safepoints between items."},
    {"wait_for_all_pending", (PyCFunction)Dispatcher::wait_for_all_pending_method,
     METH_NOARGS,
     "wait_for_all_pending()\n\n"
     "Block until every other interpreter thread is waiting inside\n"
     "the dispatcher."},
    {"interrupt", (PyCFunction)Dispatcher::interrupt_method, METH_VARARGS,
     "interrupt(on_waiting_thread, while_interrupted) -> result\n\n"
     "Inject a callback for worker threads, run a coordinator callback,\n"
     "and restore state on return.  Returns while_interrupted's result."},
    {nullptr}
};

static PyGetSetDef dispatcher_getset[] = {
    {"buffered", (getter)Dispatcher::get_buffered, nullptr,
     "Current buffered item. Loads lazily and re-raises terminal exceptions.", nullptr},
    {"waiting_thread_count", (getter)Dispatcher::get_waiting_thread_count, nullptr,
     "Number of threads currently waiting inside the dispatcher.", nullptr},
    {"source", (getter)Dispatcher::get_source, nullptr,
     "The source callable.", nullptr},
    {nullptr}
};

PyTypeObject Dispatcher_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "Dispatcher",
    .tp_basicsize = sizeof(Dispatcher),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)Dispatcher::dealloc,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
    .tp_doc = "Dispatcher(source, deadlock_timeout_seconds=1.0)\n\n"
              "Replay stream dispatcher.\n\n"
              "Threads call next(predicate) to receive items.\n"
              "Coordinator calls interrupt() to inject callbacks.",
    .tp_traverse = (traverseproc)Dispatcher::traverse,
    .tp_clear = (inquiry)Dispatcher::clear,
    .tp_methods = dispatcher_methods,
    .tp_getset = dispatcher_getset,
    .tp_init = (initproc)Dispatcher::init,
    .tp_new = PyType_GenericNew,
};

}  // namespace retracesoftware
