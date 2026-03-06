#include "functional.h"
#include "object.h"
#include <structmember.h>

struct WhenNotNone : public PyObject {
    retracesoftware::FastCall target;
    vectorcallfunc vectorcall;
};

static PyObject * vectorcall(WhenNotNone * self, PyObject** args, size_t nargsf, PyObject* kwnames) {

    size_t nargs = PyVectorcall_NARGS(nargsf) + (kwnames ? PyTuple_GET_SIZE(kwnames) : 0);

    for (size_t i = 0; i < nargs; i++) {
        if (args[i] == Py_None) {
            return Py_NewRef(Py_None);
        }
    }
    return self->target(args, nargsf, kwnames);
}

static int traverse(WhenNotNone* self, visitproc visit, void* arg) {
    Py_VISIT(self->target.callable);
    return 0;
}

static int clear(WhenNotNone* self) {
    Py_CLEAR(self->target.callable);
    return 0;
}

static void dealloc(WhenNotNone *self) {    
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static PyObject * repr(WhenNotNone *self) {
    return PyUnicode_FromFormat(MODULE "when_not_none(%S)", self->target.callable);
}

// static PyMemberDef members[] = {
//     {"functions", T_OBJECT, offsetof(Compose, functions), READONLY, "TODO"},
//     {NULL}  /* Sentinel */
// };

static PyObject * getattro(WhenNotNone *self, PyObject *name) {
    return PyObject_GetAttr(self->target.callable, name);
}

static int setattro(WhenNotNone *self, PyObject *name, PyObject * value) {
    return PyObject_SetAttr(self->target.callable, name, value);
}

static int init(WhenNotNone *self, PyObject *args, PyObject *kwds) {

    PyObject * target;

    static const char *kwlist[] = {"target", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O", (char **)kwlist,&target))
    {
        return -1; // Return NULL on failure
    }

    if (!PyCallable_Check(target)) {
        PyErr_Format(PyExc_TypeError, 
            "Error constructing: %s, parameter target: %S must be callable", WhenNotNone_Type.tp_name, target);
        return -1;
    }

    self->target = retracesoftware::FastCall(target);
    Py_INCREF(target);
    self->vectorcall = (vectorcallfunc)vectorcall;

    return 0;
}

static PyObject* descr_get(PyObject *self, PyObject *obj, PyObject *type) {
    return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
}

PyTypeObject WhenNotNone_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "when_not_none",
    .tp_basicsize = sizeof(WhenNotNone),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(WhenNotNone, vectorcall),
    .tp_repr = (reprfunc)repr,
    .tp_call = PyVectorcall_Call,
    .tp_str = (reprfunc)repr,
    .tp_getattro = (getattrofunc)getattro,
    .tp_setattro = (setattrofunc)setattro,
    .tp_flags = Py_TPFLAGS_DEFAULT | 
                Py_TPFLAGS_HAVE_GC | 
                Py_TPFLAGS_HAVE_VECTORCALL | 
                Py_TPFLAGS_METHOD_DESCRIPTOR,
    .tp_doc = "when_not_none(target)\n--\n\n"
               "Call target only if all arguments are not None.\n\n"
               "If any positional or keyword argument is None, returns None\n"
               "immediately without calling target.\n\n"
               "Args:\n"
               "    target: The callable to wrap.\n\n"
               "Returns:\n"
               "    A callable that short-circuits on None arguments.\n\n"
               "Example:\n"
               "    >>> safe_add = when_not_none(lambda a, b: a + b)\n"
               "    >>> safe_add(1, 2)     # 3\n"
               "    >>> safe_add(1, None)  # None",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    // .tp_members = members,
    .tp_descr_get = descr_get,
    .tp_init = (initproc)init,
    .tp_new = PyType_GenericNew,
};
