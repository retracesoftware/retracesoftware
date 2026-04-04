#include "functional.h"
#include <structmember.h>

struct Repeatedly : public PyVarObject {
    retracesoftware::FastCall f;
    vectorcallfunc vectorcall;
    PyObject * args[];

    static int normalize_function(PyObject * function) {
        if (!PyCallable_Check(function)) {
            PyErr_Format(PyExc_TypeError, "repeatedly() expects a callable, got %S", function);
            return -1;
        }
        return 0;
    }
};

static PyObject * vectorcall(Repeatedly * self, PyObject** args, size_t nargsf, PyObject* kwnames) {
    return self->f(self->args, Py_SIZE(self), nullptr);
}

static int traverse(Repeatedly* self, visitproc visit, void* arg) {
    Py_VISIT(self->f.callable);
    for (Py_ssize_t i = 0; i < Py_SIZE(self); i++) {
        Py_VISIT(self->args[i]);
    }
    return 0;
}

static int clear(Repeatedly* self) {
    Py_CLEAR(self->f.callable);
    for (Py_ssize_t i = 0; i < Py_SIZE(self); i++) {
        Py_CLEAR(self->args[i]);
    }
    return 0;
}

static void dealloc(Repeatedly *self) {
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static PyObject * get_function(Repeatedly * self, void * closure) {
    return self->f.callable ? Py_NewRef(self->f.callable) : Py_NewRef(Py_None);
}

static PyGetSetDef getset[] = {
    {"function", (getter)get_function, NULL, "The wrapped function called on each invocation.", NULL},
    {NULL}
};

static PyMemberDef members[] = {
    {"__vectorcalloffset__", 
        T_PYSSIZET,
        OFFSET_OF_MEMBER(Repeatedly, vectorcall),
        READONLY,
        "Offset of vectorcall function pointer."},
    {NULL}  /* Sentinel */
};

static PyObject * create(PyTypeObject * type, PyObject * args, PyObject * kwds) {
    PyObject * function = nullptr;
    Py_ssize_t bound_start = 1;
    Py_ssize_t nargs = PyTuple_GET_SIZE(args);

    if (kwds && PyDict_GET_SIZE(kwds) > 0) {
        if (PyDict_GET_SIZE(kwds) != 1 || !PyDict_GetItemString(kwds, "function")) {
            PyErr_SetString(PyExc_TypeError, "repeatedly() only accepts the 'function' keyword");
            return nullptr;
        }
        function = PyDict_GetItemString(kwds, "function");
        bound_start = 0;
    } else {
        if (nargs == 0) {
            PyErr_SetString(PyExc_TypeError, "repeatedly requires at least one positional argument");
            return nullptr;
        }
        function = PyTuple_GET_ITEM(args, 0);
    }

    if (Repeatedly::normalize_function(function) < 0) {
        return nullptr;
    }

    Repeatedly * self = (Repeatedly *)PyType_GenericAlloc(type, nargs - bound_start);
    if (!self) {
        return nullptr;
    }

    self->f = retracesoftware::FastCall(Py_NewRef(function));

    for (Py_ssize_t i = 0; i < Py_SIZE(self); i++) {
        self->args[i] = Py_NewRef(PyTuple_GET_ITEM(args, i + bound_start));
    }

    self->vectorcall = (vectorcallfunc)vectorcall;

    return (PyObject *)self;
}

static PyObject* descr_get(PyObject *self, PyObject *obj, PyObject *type) {
    return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
}

static PyType_Slot slots[] = {
    {Py_tp_dealloc, (void*)dealloc},
    {Py_tp_call, (void*)PyVectorcall_Call},
    {Py_tp_doc, (void*)"repeatedly(function, *args)\n--\n\n"
               "Wrap a function to be called repeatedly with fixed bound arguments.\n\n"
               "Each call invokes function(*args) regardless of arguments passed at call time.\n"
               "Useful for generators, stateful computations, or replacing lazy().\n\n"
               "Args:\n"
               "    function: A callable.\n"
               "    *args: Positional arguments bound once at construction.\n\n"
               "Returns:\n"
               "    A callable that always calls function(*args)."},
    {Py_tp_traverse, (void*)traverse},
    {Py_tp_clear, (void*)clear},
    {Py_tp_members, (void*)members},
    {Py_tp_getset, (void*)getset},
    {Py_tp_descr_get, (void*)descr_get},
    {Py_tp_new, (void*)create},
    {0, NULL}
};

PyType_Spec Repeatedly_Spec = {
    .name = MODULE "repeatedly",
    .basicsize = sizeof(Repeatedly),
    .itemsize = sizeof(PyObject *),
    .flags = Py_TPFLAGS_DEFAULT | 
             Py_TPFLAGS_HAVE_GC | 
             Py_TPFLAGS_MANAGED_DICT | 
             Py_TPFLAGS_HAVE_VECTORCALL |
             Py_TPFLAGS_METHOD_DESCRIPTOR |
             Py_TPFLAGS_BASETYPE,
    .slots = slots
};

// PyTypeObject Repeatedly_Type = {
//     .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
//     .tp_name = MODULE "repeatedly",
//     .tp_basicsize = sizeof(Repeatedly),
//     .tp_itemsize = 0,
//     .tp_dealloc = (destructor)dealloc,
//     .tp_vectorcall_offset = offsetof(Repeatedly, vectorcall),
//     .tp_call = PyVectorcall_Call,
//     // .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL | Py_TPFLAGS_MANAGED_DICT,
//     .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
//     .tp_doc = "TODO",
//     .tp_traverse = (traverseproc)traverse,
//     .tp_clear = (inquiry)clear,
//     // .tp_methods = methods,
//     .tp_members = members,
//     .tp_descr_get = descr_get,
//     .tp_init = (initproc)init,
// };
