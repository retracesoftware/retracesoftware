#include "module.h"

#ifndef MODULE_NAME
#define MODULE_NAME _retracesoftware_cursor
#endif

#define _STR(x) #x
#define STR(x) _STR(x)
#define _CONCAT(a, b) a##b
#define CONCAT(a, b) _CONCAT(a, b)

static PyTypeObject *hidden_types[] = {
    &DisabledCallback_Type,
    nullptr
};

static PyTypeObject *exposed_types[] = {
    &CallCounter_Type,
    &ThreadCallCounts_Type,
    nullptr
};

static PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    STR(MODULE_NAME),
    "Call-count cursor tracking for retrace replay.",
    0,
    nullptr
};

PyMODINIT_FUNC CONCAT(PyInit_, MODULE_NAME)(void) {
    PyObject *module = PyModule_Create(&moduledef);
    if (!module) return nullptr;

    for (int i = 0; hidden_types[i]; i++) {
        if (PyType_Ready(hidden_types[i]) < 0) {
            Py_DECREF(module);
            return nullptr;
        }
    }

    for (int i = 0; exposed_types[i]; i++) {
        if (PyType_Ready(exposed_types[i]) < 0) {
            Py_DECREF(module);
            return nullptr;
        }
        const char *last_dot = strrchr(exposed_types[i]->tp_name, '.');
        const char *name = last_dot ? (last_dot + 1) : exposed_types[i]->tp_name;
        if (PyModule_AddObject(module, name, (PyObject *)exposed_types[i]) < 0) {
            Py_DECREF(module);
            return nullptr;
        }
    }

    return module;
}
