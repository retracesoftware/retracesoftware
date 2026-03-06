// common-headers/include/py_helpers.h
// Common Python C API helper macros and functions
#pragma once

#include <Python.h>

// CHECK_CALLABLE - validates a parameter is callable or None
// Sets a TypeError and returns -1 if invalid (for use in tp_init)
// Converts Py_None to nullptr for convenience
#define CHECK_CALLABLE(name) \
    if (name) { \
        if (name == Py_None) name = nullptr; \
        else if (!PyCallable_Check(name)) { \
            PyErr_Format(PyExc_TypeError, "Parameter '%s' must be callable, but was: %S", #name, name); \
            return -1; \
        } \
    }

// check_callable - PyArg_Parse converter for callable arguments
// Usage: PyArg_ParseTuple(args, "O&", check_callable, &my_func)
static inline int check_callable(PyObject *obj, void *out) {
    if (!PyCallable_Check(obj)) {
        PyErr_Format(PyExc_TypeError, "Expected a callable object, but received: %S", obj);
        return 0;
    }
    *((PyObject **)out) = obj;
    return 1;
}

// Safe type name extraction - gets short name from fully qualified tp_name
// "mymodule.MyClass" -> "MyClass"
static inline const char* py_type_short_name(PyTypeObject* type) {
    const char *last_dot = strrchr(type->tp_name, '.');
    return last_dot ? (last_dot + 1) : type->tp_name;
}
