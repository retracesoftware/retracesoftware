#include "functional.h"
#include <structmember.h>

struct Repeatedly : public PyObject {
    // PyObject_HEAD
    // PyObject * f;
    retracesoftware::FastCall f;
    vectorcallfunc vectorcall;
};

static PyObject * vectorcall(Repeatedly * self, PyObject** args, size_t nargsf, PyObject* kwnames) {
    return self->f();
}

static int traverse(Repeatedly* self, visitproc visit, void* arg) {
    Py_VISIT(self->f.callable);
    return 0;
}

static int clear(Repeatedly* self) {
    Py_CLEAR(self->f.callable);
    return 0;
}

static void dealloc(Repeatedly *self) {
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static PyMemberDef members[] = {
    {"function", T_OBJECT, OFFSET_OF_MEMBER(Repeatedly, f), READONLY, "The wrapped function called on each invocation."},
    {"__vectorcalloffset__", 
        T_PYSSIZET,
        OFFSET_OF_MEMBER(Repeatedly, vectorcall),
        READONLY,
        "Offset of vectorcall function pointer."},
    {NULL}  /* Sentinel */
};

static int init(Repeatedly * self, PyObject *args, PyObject *kwds) {
    PyObject * function;
    
    static const char *kwlist[] = {"function", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O", (char **)kwlist, &function))
    {
        return -1; // Return NULL on failure
    }

    self->f = retracesoftware::FastCall(function);
    Py_INCREF(function);

    self->vectorcall = (vectorcallfunc)vectorcall;

    return 0;
}

// static PyObject * create(PyTypeObject *type, PyObject *args, PyObject *kwds) {

//     PyObject * function;
    
//     static const char *kwlist[] = {"function", NULL};

//     if (!PyArg_ParseTupleAndKeywords(args, kwds, "O", (char **)kwlist, &function))
//     {
//         return NULL; // Return NULL on failure
//     }
    
//     Repeatedly * self = (Repeatedly *)type->tp_alloc(type, 0);

//     if (!self) {
//         return NULL;
//     }

//     self->f = Py_NewRef(function);

//     self->vectorcall = (vectorcallfunc)vectorcall;

//     return (PyObject *)self;
// }

static PyObject* descr_get(PyObject *self, PyObject *obj, PyObject *type) {
    return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
}

static PyType_Slot slots[] = {
    {Py_tp_dealloc, (void*)dealloc},
    {Py_tp_call, (void*)PyVectorcall_Call},
    {Py_tp_doc, (void*)"repeatedly(function)\n--\n\n"
               "Wrap a no-arg function to be called repeatedly, ignoring arguments.\n\n"
               "Each call invokes function() regardless of arguments passed.\n"
               "Useful for generators or stateful computations.\n\n"
               "Args:\n"
               "    function: A callable that takes no arguments.\n\n"
               "Returns:\n"
               "    A callable that always calls function()."},
    {Py_tp_traverse, (void*)traverse},
    {Py_tp_clear, (void*)clear},
    {Py_tp_members, (void*)members},
    {Py_tp_descr_get, (void*)descr_get},
    {Py_tp_init, (void*)init},
    {0, NULL}
};

PyType_Spec Repeatedly_Spec = {
    .name = MODULE "repeatedly",
    .basicsize = sizeof(Repeatedly),
    .itemsize = 0,
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
