// common-headers/include/vectorcall_helpers.h
// Helpers for Python vectorcall protocol
#pragma once

#include <Python.h>
#include <string.h>

// extract_vectorcall - get the vectorcall function pointer from a callable
// Returns PyObject_Vectorcall if the object doesn't support vectorcall directly
// This allows calling any callable efficiently while getting the fastest path
// when vectorcall is available
static inline vectorcallfunc extract_vectorcall(PyObject *callable) {
    PyTypeObject *tp = Py_TYPE(callable);
    if (!PyType_HasFeature(tp, Py_TPFLAGS_HAVE_VECTORCALL)) {
        return PyObject_Vectorcall;
    }
    Py_ssize_t offset = tp->tp_vectorcall_offset;

    vectorcallfunc ptr;
    memcpy(&ptr, (char *)callable + offset, sizeof(ptr));
    return ptr ? ptr : PyObject_Vectorcall;
}
