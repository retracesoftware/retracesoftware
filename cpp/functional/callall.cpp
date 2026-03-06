#include "functional.h"
#include <structmember.h>

struct CallAll {
    PyObject_HEAD
    PyObject * _functions;
    vectorcallfunc vectorcall;
};

static PyObject * vectorcall(CallAll * self, PyObject** args, size_t nargsf, PyObject* kwnames) {

    if (Py_TYPE(self->_functions) == &PyTuple_Type) {
        size_t n = PyTuple_GET_SIZE(self->_functions);
        for (size_t i = 0; i < n - 1; i++) {
            PyObject * res = PyObject_Vectorcall(PyTuple_GET_ITEM(self->_functions, i), args, nargsf, kwnames);
            Py_XDECREF(res);
            if (!res) return NULL;
        }
        return PyObject_Vectorcall(PyTuple_GET_ITEM(self->_functions, n - 1), args, nargsf, kwnames);

    } else if (Py_TYPE(self->_functions) == &PyList_Type) {
        size_t n = PyList_GET_SIZE(self->_functions);
        for (size_t i = 0; i < n - 1; i++) {
            PyObject * res = PyObject_Vectorcall(PyList_GET_ITEM(self->_functions, i), args, nargsf, kwnames);
            Py_XDECREF(res);
            if (!res) return NULL;
        }
        return PyObject_Vectorcall(PyList_GET_ITEM(self->_functions, n - 1), args, nargsf, kwnames);
    }
    Py_RETURN_NONE;
}

static int traverse(CallAll* self, visitproc visit, void* arg) {
    Py_VISIT(self->_functions);
    return 0;
}

static int clear(CallAll* self) {
    Py_CLEAR(self->_functions);
    return 0;
}

static void dealloc(CallAll *self) {
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static PyObject * create(PyTypeObject *type, PyObject *args, PyObject *kwds) {

    PyObject * functions = NULL;

    static const char *kwlist[] = {"functions", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|O", (char **)kwlist, &functions)) {
        return NULL; // Return NULL on failure
    }
    
    CallAll * self = (CallAll *)type->tp_alloc(type, 0);

    if (!self) {
        return NULL;
    }

    self->_functions = functions ? Py_NewRef(functions) : PyList_New(0);

    self->vectorcall = (vectorcallfunc)vectorcall;

    return (PyObject *)self;
}

static PyMemberDef members[] = {
    {"functions", T_OBJECT, offsetof(CallAll, _functions), 0, "The list or tuple of functions to call."},
    {NULL}  /* Sentinel */
};

PyTypeObject CallAll_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "callall",
    .tp_basicsize = sizeof(CallAll),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = offsetof(CallAll, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "callall(functions)\n--\n\n"
               "Call all functions with the same arguments, return the last result.\n\n"
               "Executes each function in order. Results of all but the last\n"
               "are discarded. Useful for triggering multiple side effects.\n\n"
               "Args:\n"
               "    functions: A list or tuple of callables.\n\n"
               "Returns:\n"
               "    The result of the last function.\n\n"
               "Example:\n"
               "    >>> notify = callall([log, emit_event, update_stats])\n"
               "    >>> notify(data)  # calls all three, returns update_stats result",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    .tp_members = members,
    .tp_new = (newfunc)create,
};
