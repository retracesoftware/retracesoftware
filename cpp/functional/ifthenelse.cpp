#include "functional.h"
#include <structmember.h>

struct IfThenElse {
    PyObject_HEAD
    int from_arg;
    retracesoftware::FastCall test;
    retracesoftware::FastCall then;
    retracesoftware::FastCall otherwise;
    vectorcallfunc vectorcall;
};

static PyObject * vectorcall(IfThenElse * self, PyObject** args, size_t nargsf, PyObject* kwnames) {
    
    assert (!PyErr_Occurred());

    PyObject * test_res = self->test(args + self->from_arg, PyVectorcall_NARGS(nargsf) - self->from_arg, kwnames);

    if (!test_res) return nullptr;
    int is_true = PyObject_IsTrue(test_res);
    Py_DECREF(test_res);

    int nargs = PyVectorcall_NARGS(nargsf);

    switch (is_true) {
        case 1:
            return self->then.callable 
                ? self->then(args, nargsf, kwnames)
                : Py_NewRef(nargs == 1 ? args[0] : Py_None);
        case 0:
            return self->otherwise.callable
                ? self->otherwise(args, nargsf, kwnames) 
                : Py_NewRef(nargs == 1 ? args[0] : Py_None);
        default:
            return nullptr;
    }
}

static int traverse(IfThenElse* self, visitproc visit, void* arg) {
    Py_VISIT(self->test.callable);
    Py_VISIT(self->then.callable);
    Py_VISIT(self->otherwise.callable);

    return 0;
}

static int clear(IfThenElse* self) {
    Py_CLEAR(self->test.callable);
    Py_CLEAR(self->then.callable);
    Py_CLEAR(self->otherwise.callable);
    return 0;
}

static void dealloc(IfThenElse *self) {
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static int init(IfThenElse *self, PyObject *args, PyObject *kwds) {

    PyObject * test = NULL;
    PyObject * then = NULL;
    PyObject * otherwise = NULL;
    int from_arg = 0;

    static const char *kwlist[] = {
        "test",
        "then",
        "otherwise",
        "from_arg",
        NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OOO|I", 
        (char **)kwlist,
        &test,
        &then,
        &otherwise,
        &from_arg))
    {
        return -1; // Return NULL on failure
    }

    CHECK_CALLABLE(test);
    CHECK_CALLABLE(then);
    CHECK_CALLABLE(otherwise);
    
    self->test = retracesoftware::FastCall(test);
    Py_INCREF(test);

    if (then) {
        self->then = retracesoftware::FastCall(then);
        Py_INCREF(then);
    }
    if (otherwise) {
        self->otherwise = retracesoftware::FastCall(otherwise);
        Py_INCREF(otherwise);
    }
    self->vectorcall = (vectorcallfunc)vectorcall;
    self->from_arg = from_arg;

    return 0;
}

static PyMemberDef members[] = {
    // {"argument", T_OBJECT, OFFSET_OF_MEMBER(IfThenElse, argument), 0, "TODO"},
    // {"result", T_OBJECT, OFFSET_OF_MEMBER(IfThenElse, result), 0, "TODO"},
    // {"error", T_OBJECT, OFFSET_OF_MEMBER(IfThenElse, error), 0, "TODO"},
    // {"function", T_OBJECT, OFFSET_OF_MEMBER(IfThenElse, function), 0, "TODO"},
    {NULL}  /* Sentinel */
};

static PyObject* descr_get(PyObject *self, PyObject *obj, PyObject *type) {
    return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
}

PyTypeObject IfThenElse_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "if_then_else",
    .tp_basicsize = sizeof(IfThenElse),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(IfThenElse, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "if_then_else(test, then, otherwise, from_arg=0)\n--\n\n"
               "Conditional dispatch with optional argument slicing.\n\n"
               "Tests condition on args[from_arg:]; if truthy calls 'then',\n"
               "if falsy calls 'otherwise'. If then/otherwise is None, returns\n"
               "the first argument (or None if no args).\n\n"
               "Args:\n"
               "    test: Predicate callable.\n"
               "    then: Called when test is truthy (or None to return first arg).\n"
               "    otherwise: Called when test is falsy (or None to return first arg).\n"
               "    from_arg: Start index for args passed to test (default 0).\n\n"
               "Returns:\n"
               "    Result of then/otherwise, or first arg if branch is None.",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    .tp_members = members,
    .tp_descr_get = descr_get,
    .tp_init = (initproc)init,
    .tp_new = PyType_GenericNew,
};
