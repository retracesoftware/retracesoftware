#include "stream.h"
#include "wireformat.h"

static PyTypeObject * hidden_types[] = {
    &retracesoftware_stream::StreamHandle_Type,
    &retracesoftware_stream::Deleter_Type,
    nullptr
};

static PyTypeObject * exposed_types[] = {
    &retracesoftware_stream::FramedWriter_Type,
    &retracesoftware_stream::ObjectWriter_Type,
    &retracesoftware_stream::ObjectStream_Type,
    &retracesoftware_stream::AsyncFilePersister_Type,
    nullptr
};

static PyObject * thread_id(PyObject * module, PyObject * unused) {
    PyObject * id = PyDict_GetItem(PyThreadState_GetDict(), module);

    return Py_NewRef(id ? id : Py_None);
}

static PyObject * set_thread_id(PyObject * module, PyObject * id) {

    if (PyDict_SetItem(PyThreadState_GetDict(), module, Py_NewRef(id)) == -1) {
        Py_DECREF(id);        
        return nullptr;
    }
    Py_RETURN_NONE;
}

static PyMethodDef module_methods[] = {
    {"thread_id", (PyCFunction)thread_id, METH_NOARGS, "TODO"},
    {"set_thread_id", (PyCFunction)set_thread_id, METH_O, "TODO"},
    // {"create_wrapping_proxy_type", (PyCFunction)create_wrapping_proxy_type, METH_VARARGS | METH_KEYWORDS, "TODO"},
    // {"unwrap_apply", (PyCFunction)unwrap_apply, METH_FASTCALL | METH_KEYWORDS, "Call the wrapped target with unproxied *args/**kwargs."},
    // {"thread_id", (PyCFunction)thread_id, METH_NOARGS, "TODO"},
    // {"set_thread_id", (PyCFunction)set_thread_id, METH_O, "TODO"},
    // {"proxy_test", (PyCFunction)proxy_test, METH_O, "TODO"},
    // {"unwrap", (PyCFunction)unwrap, METH_O, "TODO"},
    // {"yields_callable_instances", (PyCFunction)yields_callable_instances, METH_O, "TODO"},
    // {"yields_weakly_referenceable_instances", (PyCFunction)yields_weakly_referenceable_instances, METH_O, "TODO"},

    {NULL, NULL, 0, NULL}  // Sentinel
};

// Module name macros - allows building as _release or _debug
#ifndef MODULE_NAME
#define MODULE_NAME retracesoftware_stream
#endif

#define _STR(x) #x
#define STR(x) _STR(x)
#define _CONCAT(a, b) a##b
#define CONCAT(a, b) _CONCAT(a, b)

// Module definition
static PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    STR(MODULE_NAME),
    "TODO",
    0,
    module_methods
};

PyMODINIT_FUNC CONCAT(PyInit_, MODULE_NAME)(void) {
    PyObject* module = PyModule_Create(&moduledef);

    if (!module) {
        return NULL;
    }

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
        // Find the last dot in the string
        const char *last_dot = strrchr(exposed_types[i]->tp_name, '.');

        // If a dot is found, the substring starts after the dot
        const char *name = (last_dot != NULL) ? (last_dot + 1) : exposed_types[i]->tp_name;

        if (PyModule_AddObject(module, name, (PyObject *)exposed_types[i]) < 0) {
            Py_DECREF(module);
            return nullptr;
        }
    }
    return module;
}