#include "utils.h"
#include <structmember.h>
#include <mutex>
#include <condition_variable>

// #define MAGIC 0x8E78ACE50F73A8E

namespace retracesoftware {

    struct Counter : public PyObject { 
        uint64_t value;
        vectorcallfunc vectorcall;

        static void dealloc(Counter *self) {
            Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
        }

        static int init(Counter *self, PyObject *args, PyObject *kwds) {
            uint64_t initial = 0;

            static const char *kwlist[] = { "initial", NULL};

            if (!PyArg_ParseTupleAndKeywords(args, kwds, "|K", (char **)kwlist, &initial)) {
                return -1; // Return NULL on failure
            }

            self->value = initial;
            self->vectorcall = (vectorcallfunc)next_impl;
            return 0;
        }

        uint64_t next() {
            return value++;
        }

        static PyObject * next_impl(Counter * self, PyObject** args, size_t nargsf, PyObject* kwnames) {
            return PyLong_FromUnsignedLongLong(self->next());
        }
    };

    struct BlockingCounter : public PyObject {
        uint64_t counter;
        std::condition_variable cv;
        std::mutex mtx;

        static int init(BlockingCounter *self, PyObject *args, PyObject *kwds) {
            self->counter = 0;
            new (&self->mtx) std::mutex();
            new (&self->cv) std::condition_variable();
            return 0;
        }

        static void dealloc(BlockingCounter *self) {
            Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
        }

        uint64_t next(uint64_t seq) {
            // 1. Holds the GIL
            Py_BEGIN_ALLOW_THREADS

            std::unique_lock lk(mtx);

            if (seq < counter) {
                fprintf(stderr, "SIGTRAP(counter): seq=%llu < counter=%llu\n", (unsigned long long)seq, (unsigned long long)counter); fflush(stderr);
                raise(SIGTRAP);
            }

            if (counter != seq) {
                cv.wait(lk, [this, seq]{ return counter == seq; });
            }
            
            Py_END_ALLOW_THREADS
            counter++;
            cv.notify_all();
            return counter;

            // last_locking_thread = PyThreadState_Get();
        }

        static PyObject * next_impl(BlockingCounter * self, PyObject * seq) {
            return PyLong_FromUnsignedLongLong(self->next(PyLong_AsUnsignedLongLong(seq)));
        }

    };

    static PyMethodDef BlockingCounter_methods[] = {
        {"next", _PyCFunction_CAST(BlockingCounter::next_impl), METH_O, ""},
        {NULL,           NULL}              /* sentinel */
    };

    static PyMemberDef BlockingCounter_members[] = {
        {"value", T_ULONGLONG, OFFSET_OF_MEMBER(BlockingCounter, counter), READONLY, "TODO"},
        {NULL}  /* Sentinel */
    };

    PyTypeObject BlockingCounter_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "blocking_counter",
        .tp_basicsize = sizeof(BlockingCounter),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)BlockingCounter::dealloc,
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT,
        .tp_doc = "TODO",
        .tp_methods = BlockingCounter_methods,
        .tp_members = BlockingCounter_members,
        .tp_init = (initproc)BlockingCounter::init,
        .tp_new = PyType_GenericNew,
    };

    // static PyMethodDef methods[] = {
    //     {"next", _PyCFunction_CAST(Counter::next_impl), METH_NOARGS, ""},
    //     {NULL,           NULL}              /* sentinel */
    // };

    static PyMemberDef members[] = {
        {"value", T_ULONGLONG, OFFSET_OF_MEMBER(Counter, value), READONLY, "TODO"},
        {NULL}  /* Sentinel */
    };

    PyTypeObject Counter_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "counter",
        .tp_basicsize = sizeof(Counter),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)Counter::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(Counter, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_doc = "TODO",
        // .tp_methods = methods,
        .tp_members = members,
        .tp_init = (initproc)Counter::init,
        .tp_new = PyType_GenericNew,
    };

    uint64_t Counter_Next(PyObject * counter) {
        assert(Py_TYPE(counter) == &Counter_Type);
        return reinterpret_cast<Counter *>(counter)->next();
    }
}