#include "utils.h"

#include <structmember.h>
#include <atomic>
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

struct Dispatcher : public PyObject {

    PyObject* source;
    std::atomic<PyObject*> buffered;
    std::atomic<int> num_waiting_threads;

    // ------------------------------------------------------------------
    // Init
    // ------------------------------------------------------------------

    static int init(Dispatcher *self, PyObject *args, PyObject *kwds) {
        PyObject *source;

        static const char *kwlist[] = {"source", nullptr};

        if (!PyArg_ParseTupleAndKeywords(args, kwds, "O", (char **)kwlist,
                &source))
            return -1;

        self->source = Py_NewRef(source);
        self->buffered.store(nullptr, std::memory_order_relaxed);
        self->num_waiting_threads.store(0, std::memory_order_relaxed);

        return 0;
    }

    // ------------------------------------------------------------------
    // next(predicate)
    //
    // Block until predicate(buffered_item) is truthy, return the item.
    // ------------------------------------------------------------------

    PyObject *load_next() {
        while (true) {
            PyObject *next = buffered.load(std::memory_order_acquire);

            if (next && next != LOADING && !((uintptr_t)next & 1))
                return next;

            if ((uintptr_t)next & 1) {
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
                buffered.store(LOADING, std::memory_order_release);
                next = PyObject_CallNoArgs(source);
                if (!next) {
                    buffered.store(nullptr, std::memory_order_release);
                    atomic_notify_all_compat(buffered);
                    return nullptr;
                }
                buffered.store(next, std::memory_order_release);
                atomic_notify_all_compat(buffered);
                return next;
            }
        }
    }

    static PyObject *next_method(Dispatcher *self, PyObject *predicate) {

        while (true) {
            PyObject * next = self->load_next();

            if (!next) {
                return nullptr;
            }

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
                PyObject *taken = self->buffered.exchange(nullptr, std::memory_order_acq_rel);
                atomic_notify_all_compat(self->buffered);
                if (taken) return taken;
            } else if (self->num_waiting_threads.load(std::memory_order_acquire) < count_interpreter_tstates() - 1) {
                self->num_waiting_threads.fetch_add(1, std::memory_order_release);
                atomic_notify_all_compat(self->num_waiting_threads);
                Py_BEGIN_ALLOW_THREADS
                atomic_wait_compat(self->buffered, next);
                Py_END_ALLOW_THREADS
                self->num_waiting_threads.fetch_sub(1, std::memory_order_release);
                atomic_notify_all_compat(self->num_waiting_threads);
            } else {
                PyErr_SetString(PyExc_RuntimeError, "Dispatcher: too many threads waiting for item");
                return nullptr;
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
        self->buffered.store(tagged, std::memory_order_release);
        atomic_notify_all_compat(self->buffered);

        PyObject *result = PyObject_CallNoArgs(while_interrupted);

        self->buffered.store(saved, std::memory_order_release);
        atomic_notify_all_compat(self->buffered);

        if (!result) return nullptr;
        return result;
    }

    static PyObject *get_buffered(Dispatcher *self, void *) {
        PyObject *item = self->buffered.load(std::memory_order_acquire);
        if (!item || item == LOADING || ((uintptr_t)item & 1)) {
            PyErr_SetString(PyExc_RuntimeError, "Dispatcher: no item currently buffered");
            return nullptr;
        }
        return Py_NewRef(item);
    }

    static PyObject *get_waiting_thread_count(Dispatcher *self, void *) {
        return PyLong_FromLong(self->num_waiting_threads.load(std::memory_order_acquire));
    }

    static PyObject *get_source(Dispatcher *self, void *) {
        return Py_NewRef(self->source);
    }

    // ------------------------------------------------------------------
    // Standard Python type support
    // ------------------------------------------------------------------

    static int traverse(Dispatcher *self, visitproc visit, void *arg) {
        Py_VISIT(self->source);
        PyObject *buf = self->buffered.load(std::memory_order_relaxed);
        Py_VISIT(buf);
        return 0;
    }

    static int clear(Dispatcher *self) {
        Py_CLEAR(self->source);
        PyObject *buf = self->buffered.exchange(nullptr, std::memory_order_acq_rel);
        Py_XDECREF(buf);
        return 0;
    }

    static void dealloc(Dispatcher *self) {
        PyObject_GC_UnTrack(self);
        clear(self);
        Py_TYPE(self)->tp_free(self);
    }
};

static PyMethodDef dispatcher_methods[] = {
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
     "Currently buffered item.  Raises RuntimeError if nothing is buffered.", nullptr},
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
    .tp_doc = "Dispatcher(source)\n\n"
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
