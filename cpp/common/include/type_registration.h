// common-headers/include/type_registration.h
// Helpers for registering PyTypeObjects with modules
#pragma once

#include <Python.h>
#include <string.h>
#include "py_helpers.h"

// ready_types - call PyType_Ready on an array of types
// Returns -1 on failure (with exception set), 0 on success
static inline int ready_types(PyTypeObject** types) {
    for (int i = 0; types[i]; i++) {
        if (PyType_Ready(types[i]) < 0) {
            return -1;
        }
    }
    return 0;
}

// register_types - ready types and add them to module with short names
// Extracts "MyClass" from "mymodule.MyClass" automatically
// Returns -1 on failure (with exception set), 0 on success
static inline int register_types(PyObject* module, PyTypeObject** types) {
    for (int i = 0; types[i]; i++) {
        if (PyType_Ready(types[i]) < 0) {
            return -1;
        }
        const char* name = py_type_short_name(types[i]);
        if (PyModule_AddObject(module, name, (PyObject*)types[i]) < 0) {
            return -1;
        }
    }
    return 0;
}

// register_types_from_specs - create heap types from specs and add to module
// Returns -1 on failure, 0 on success
static inline int register_types_from_specs(PyObject* module, PyType_Spec** specs) {
    for (int i = 0; specs[i]; i++) {
        PyTypeObject* cls = (PyTypeObject*)PyType_FromSpec(specs[i]);
        if (!cls) {
            return -1;
        }
        const char* name = py_type_short_name(cls);
        if (PyModule_AddObject(module, name, (PyObject*)cls) < 0) {
            Py_DECREF(cls);
            return -1;
        }
    }
    return 0;
}
