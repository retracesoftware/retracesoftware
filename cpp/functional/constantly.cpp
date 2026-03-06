#include "functional.h"
#include "object.h"
#include <structmember.h>
#include <signal.h>
#include <functional>

struct Constantly {
    PyObject_HEAD
    vectorcallfunc vectorcall;
    PyObject * result;
};

static PyObject * vectorcall(Constantly * self, PyObject** args, size_t nargsf, PyObject* kwnames) {
    return Py_NewRef(self->result);
}

static int traverse(Constantly* self, visitproc visit, void* arg) {
    Py_VISIT(self->result);
    return 0;
}

static int clear(Constantly* self) {
    Py_CLEAR(self->result);
    return 0;
}

static void dealloc(Constantly *self) {    
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static PyObject * repr(Constantly *self) {
    return PyUnicode_FromFormat(MODULE "constantly(%S)", self->result);
}

static PyMemberDef members[] = {
    {"value", T_OBJECT, offsetof(Constantly, result), READONLY, "The constant value returned on every call."},
    {NULL}  /* Sentinel */
};

static int init(Constantly *self, PyObject *args, PyObject *kwds) {

    static const char* kwlist[] = {"value", nullptr};
    PyObject* value = nullptr;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O", const_cast<char**>(kwlist), &value)) {
        return -1;
    }

    self->vectorcall = reinterpret_cast<vectorcallfunc>(vectorcall);
    self->result = Py_NewRef(value);
    return 0;
}

PyTypeObject Constantly_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "constantly",
    .tp_basicsize = sizeof(Constantly),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = offsetof(Constantly, vectorcall),
    .tp_repr = (reprfunc)repr,
    .tp_call = PyVectorcall_Call,
    .tp_str = (reprfunc)repr,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "constantly(value)\n--\n\n"
               "Create a callable that always returns the same value.\n\n"
               "Ignores all arguments and returns value unchanged.\n"
               "Unlike always(), does not call value even if it's callable.\n\n"
               "Args:\n"
               "    value: The constant value to return.\n\n"
               "Returns:\n"
               "    A callable that always returns value.\n\n"
               "Example:\n"
               "    >>> f = constantly(42)\n"
               "    >>> f()           # 42\n"
               "    >>> f(1, 2, x=3)  # 42",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    .tp_members = members,
    .tp_init = (initproc)init,
    .tp_new = PyType_GenericNew,
};
