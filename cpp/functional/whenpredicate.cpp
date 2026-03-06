#include "functional.h"
#include <structmember.h>
#include <signal.h>

struct WhenPredicate {
    PyObject_HEAD
    PyObject * predicate;
    PyObject * function;
    vectorcallfunc vectorcall;
};

static int run_predicate(PyObject * pred, PyObject** args, size_t nargsf, PyObject* kwnames) {
    PyObject * res = PyObject_Vectorcall(pred, args, nargsf, kwnames);

    if (!res) return -1;
    int status = PyObject_IsTrue(res);
    Py_DECREF(res);
    return status;
}

static PyObject * vectorcall(WhenPredicate * self, PyObject** args, size_t nargsf, PyObject* kwnames) {

    switch (run_predicate(self->predicate, args, nargsf, kwnames)) {
        case 0:
            Py_RETURN_NONE;
        case 1:
            return PyObject_Vectorcall(self->function, args, nargsf, kwnames);
        default:
            assert(PyErr_Occurred());
            return nullptr;
    }
}

static int traverse(WhenPredicate* self, visitproc visit, void* arg) {
    Py_VISIT(self->predicate);
    Py_VISIT(self->function);

    return 0;
}

static int clear(WhenPredicate* self) {
    Py_CLEAR(self->predicate);
    Py_CLEAR(self->function);
    return 0;
}

static void dealloc(WhenPredicate *self) {
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static PyMemberDef members[] = {
    {"predicate", T_OBJECT, offsetof(WhenPredicate, predicate), READONLY, "The predicate to test."},
    {"function", T_OBJECT, offsetof(WhenPredicate, function), READONLY, "The function to call when predicate is truthy."},
    {NULL}  /* Sentinel */
};

static PyObject * create(PyTypeObject *type, PyObject *args, PyObject *kwds) {

    PyObject * predicate;
    PyObject * function;
    
    static const char *kwlist[] = {"predicate", "function", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OO", (char **)kwlist, &predicate, &function))
    {
        return NULL; // Return NULL on failure
    }

    WhenPredicate * self = (WhenPredicate *)type->tp_alloc(type, 0);

    if (!self) {
        return NULL;
    }

    self->predicate = Py_NewRef(predicate);
    self->function = Py_NewRef(function);
    self->vectorcall = (vectorcallfunc)vectorcall;

    return (PyObject *)self;
}

PyTypeObject WhenPredicate_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "when_predicate",
    .tp_basicsize = sizeof(WhenPredicate),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = offsetof(WhenPredicate, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "when_predicate(predicate, function)\n--\n\n"
               "Conditional function application: call function only if predicate is truthy.\n\n"
               "If predicate(*args) is truthy, returns function(*args).\n"
               "Otherwise returns None.\n\n"
               "Args:\n"
               "    predicate: A callable that tests the condition.\n"
               "    function: A callable to invoke when predicate passes.\n\n"
               "Returns:\n"
               "    function(*args) if predicate is truthy, else None.",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    .tp_members = members,
    .tp_new = (newfunc)create,
};
