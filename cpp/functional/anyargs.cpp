#include "functional.h"
#include <algorithm>
#include <structmember.h>
#include <signal.h>

struct AnyArgs {
    PyObject_HEAD
    PyObject * func;
    vectorcallfunc func_vectorcall;
    vectorcallfunc vectorcall;
};

static PyObject * vectorcall(AnyArgs * self, PyObject* const * args, size_t nargsf, PyObject* kwnames) {
    return self->func_vectorcall(self->func, nullptr, 0, nullptr);
}

static int traverse(AnyArgs* self, visitproc visit, void* arg) {
    Py_VISIT(self->func);
    return 0;
}

static int clear(AnyArgs* self) {
    Py_CLEAR(self->func);
    return 0;
}

static void dealloc(AnyArgs *self) {
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static PyMemberDef members[] = {
    {nullptr}  /* Sentinel */
};

static PyObject * create(PyTypeObject *type, PyObject *args, PyObject *kwds) {

    PyObject * function;
    
    static const char *kwlist[] = {"function", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O", (char **)kwlist, &function)) {
        return nullptr; // Return NULL on failure
    }
    
    AnyArgs * self = (AnyArgs *)type->tp_alloc(type, 0);

    if (!self) {
        return nullptr;
    }

    self->func = Py_NewRef(function);
    self->func_vectorcall = extract_vectorcall(function);

    self->vectorcall = (vectorcallfunc)vectorcall;

    return (PyObject *)self;
}

PyTypeObject AnyArgs_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "anyargs",
    .tp_basicsize = sizeof(AnyArgs),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = offsetof(AnyArgs, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "anyargs(function)\n--\n\n"
               "Wrap a no-argument function to accept (and ignore) any arguments.\n\n"
               "The wrapped function is always called with no arguments.\n\n"
               "Args:\n"
               "    function: A callable that takes no arguments.\n\n"
               "Returns:\n"
               "    A callable that ignores all arguments and calls function().\n\n"
               "Example:\n"
               "    >>> get_time = anyargs(time.time)\n"
               "    >>> get_time('ignored', x=1)  # same as time.time()",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    .tp_members = members,
    .tp_new = (newfunc)create,
};
