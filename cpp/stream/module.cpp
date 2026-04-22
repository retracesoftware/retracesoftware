#include "stream.h"
#include "wireformat.h"

static PyTypeObject * hidden_types[] = {
    nullptr
};

static PyTypeObject * exposed_types[] = {
    &retracesoftware_stream::Queue_Type,
    &retracesoftware_stream::FramedWriter_Type,
    &retracesoftware_stream::ObjectWriter_Type,
    &retracesoftware_stream::ObjectStream_Type,
    &retracesoftware_stream::TapeReader_Type,
    &retracesoftware_stream::Persister_Type,
    &retracesoftware_stream::Binding_Type,
    &retracesoftware_stream::Binder_Type,
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

static PyObject * add_bind_support(PyObject * module, PyObject * cls) {
    if (!PyType_Check(cls)) {
        PyErr_SetString(PyExc_TypeError, "add_bind_support takes a type");
        return nullptr;
    }

    if (!retracesoftware_stream::AddBindSupport(reinterpret_cast<PyTypeObject *>(cls))) {
        return nullptr;
    }

    Py_RETURN_NONE;
}

static PyObject * remove_bind_support(PyObject * module, PyObject * cls) {
    if (!PyType_Check(cls)) {
        PyErr_SetString(PyExc_TypeError, "remove_bind_support takes a type");
        return nullptr;
    }

    if (!retracesoftware_stream::RemoveBindSupport(reinterpret_cast<PyTypeObject *>(cls))) {
        return nullptr;
    }

    Py_RETURN_NONE;
}

static PyObject * get_bind_support_original_dealloc(PyObject * module, PyObject * cls) {
    if (!PyType_Check(cls)) {
        PyErr_SetString(PyExc_TypeError, "_get_bind_support_original_dealloc takes a type");
        return nullptr;
    }

    destructor original = nullptr;
    if (!retracesoftware_stream::GetExactBindSupportOriginalDealloc(
            reinterpret_cast<PyTypeObject *>(cls), &original)) {
        Py_RETURN_NONE;
    }

    return PyLong_FromVoidPtr(reinterpret_cast<void *>(original));
}

static PyMethodDef module_methods[] = {
    {"thread_id", (PyCFunction)thread_id, METH_NOARGS, "TODO"},
    {"set_thread_id", (PyCFunction)set_thread_id, METH_O, "TODO"},
    {"add_bind_support", (PyCFunction)add_bind_support, METH_O, "Enable binder lifecycle support for instances of a type"},
    {"remove_bind_support", (PyCFunction)remove_bind_support, METH_O, "Disable binder lifecycle support for instances of a type"},
    {"set_bind_support", (PyCFunction)add_bind_support, METH_O, "Alias for add_bind_support"},
    {"_get_bind_support_original_dealloc", (PyCFunction)get_bind_support_original_dealloc, METH_O, "Internal helper for composing native dealloc wrappers"},
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
