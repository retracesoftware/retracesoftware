#pragma once
#include <Python.h>

namespace retracesoftware {

    static inline PyObject * fallback(PyObject *callable, PyObject *const *args, size_t nargsf, PyObject *kwnames) {
        Py_ssize_t nargs = PyVectorcall_NARGS(nargsf);
        return _PyObject_MakeTpCall(PyThreadState_Get(), callable, args, nargs, kwnames);
    }

    static inline vectorcallfunc extract_vectorcall(PyObject *callable)
    {
        PyTypeObject *tp = Py_TYPE(callable);
        if (!PyType_HasFeature(tp, Py_TPFLAGS_HAVE_VECTORCALL)) {
            return fallback;
        }
        Py_ssize_t offset = tp->tp_vectorcall_offset;

        vectorcallfunc ptr;
        memcpy(&ptr, (char *) callable + offset, sizeof(ptr));
        return ptr ? ptr : fallback;
    }

    struct FastCall {
        vectorcallfunc vectorcall;
        PyObject * callable;
    
        FastCall(PyObject * callable) :
            vectorcall(callable && callable != Py_None ? extract_vectorcall(callable) : nullptr),
            callable(callable && callable != Py_None ? callable : nullptr) {
            assert(!this->callable || PyCallable_Check(this->callable));
        }

        FastCall() : vectorcall(nullptr), callable(nullptr) {}
        // ~FastCall() { Py_DECREF(callable); }

        inline PyObject * handle_result(PyObject * result) {
            assert((!result && PyErr_Occurred()) || (result && !PyErr_Occurred()));
            return result;
        }

        inline PyObject * operator()() {
            return handle_result(vectorcall(callable, nullptr, 0, nullptr));
        }

        inline PyObject * operator()(PyObject * arg) {
            return handle_result(vectorcall(callable, &arg, 1, nullptr));
        }

        inline PyObject * operator()(PyObject * arg1, PyObject * arg2) {
            PyObject * args[] = {nullptr, arg1, arg2};

            return handle_result(vectorcall(callable, args + 1, 2 | PY_VECTORCALL_ARGUMENTS_OFFSET, nullptr));
        }

        inline PyObject * operator()(PyObject * arg1, PyObject * arg2, PyObject * arg3) {
            PyObject * args[] = {nullptr, arg1, arg2, arg3};

            return handle_result(vectorcall(callable, args + 1, 3 | PY_VECTORCALL_ARGUMENTS_OFFSET, nullptr));
        }

        inline PyObject * operator()(PyObject *const *args, size_t nargsf, PyObject *kwnames) {
            return handle_result(vectorcall(callable, args, nargsf, kwnames));
        }
    };
}
