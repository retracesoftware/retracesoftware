#include "functional.h"
#include <structmember.h>
#include <functional>

struct SideEffect {
    PyObject_HEAD
    PyObject * f;
    vectorcallfunc vectorcall;
};

static PyObject * call(SideEffect * self, PyObject* arg) {

    PyObject * result = PyObject_CallOneArg(self->f, arg);

    Py_XDECREF(result);
    if (!result) return NULL;

    return Py_NewRef(arg);
}

static PyObject * vectorcall(SideEffect * self, PyObject** args, size_t nargsf, PyObject* kwnames) {
    return call(self, args[0]);
}

static int traverse(SideEffect* self, visitproc visit, void* arg) {
    Py_VISIT(self->f);
    return 0;
}

static int clear(SideEffect* self) {
    Py_CLEAR(self->f);
    return 0;
}

static void dealloc(SideEffect *self) {
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static PyObject * create(PyTypeObject *type, PyObject *args, PyObject *kwds) {

    PyObject * f = NULL;

    static const char *kwlist[] = {"function", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O", (char **)kwlist, &f)) {
        return NULL; // Return NULL on failure
    }
    
    SideEffect * self = (SideEffect *)type->tp_alloc(type, 0);

    if (!self) {
        return NULL;
    }

    self->f = Py_NewRef(f);

    self->vectorcall = (vectorcallfunc)vectorcall;

    return (PyObject *)self;
}

PyTypeObject SideEffect_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "side_effect",
    .tp_basicsize = sizeof(SideEffect),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = offsetof(SideEffect, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "side_effect(function)\n--\n\n"
               "Execute a function for its side effect, returning the input unchanged.\n\n"
               "Calls function(arg), discards the result, and returns arg.\n"
               "Useful in pipelines where you want to log/observe without\n"
               "modifying the data flow.\n\n"
               "Args:\n"
               "    function: A callable to execute for side effects.\n\n"
               "Returns:\n"
               "    A callable that passes through its input after calling function.\n\n"
               "Example:\n"
               "    >>> pipeline = compose(transform, side_effect(print), validate)\n"
               "    >>> pipeline(data)  # prints intermediate value, returns validated",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    // .tp_members = members,
    .tp_new = (newfunc)create,
};
