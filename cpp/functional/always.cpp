#include "functional.h"
#include <structmember.h>

struct Always {
    PyObject_HEAD
    PyObject * target;
    vectorcallfunc target_vectorcall;
    vectorcallfunc vectorcall;
};

static PyObject * vectorcall(Always * self, PyObject** args, size_t nargsf, PyObject* kwnames) {
    return self->target_vectorcall
        ? self->target_vectorcall(self->target, nullptr, 0, nullptr)
        : Py_NewRef(self->target);
}

static int traverse(Always* self, visitproc visit, void* arg) {
    Py_VISIT(self->target);
    return 0;
}

static int clear(Always* self) {
    Py_CLEAR(self->target);
    return 0;
}

static void dealloc(Always *self) {
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static int init(Always *self, PyObject *args, PyObject *kwds) {

    PyObject * target = NULL;

    static const char *kwlist[] = {"target", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O", (char **)kwlist, &target))
    {
        return -1; // Return NULL on failure
    }

    self->target = Py_XNewRef(target);
    self->target_vectorcall = PyCallable_Check(target)
        ? extract_vectorcall(target) : nullptr;

    self->vectorcall = (vectorcallfunc)vectorcall;

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

PyTypeObject Always_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "always",
    .tp_basicsize = sizeof(Always),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(Always, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "always(target)\n--\n\n"
               "Create a callable that always returns target (or calls it if callable).\n\n"
               "If target is callable, it's called with no arguments each time.\n"
               "Otherwise, target is returned directly. Ignores all arguments.\n\n"
               "Args:\n"
               "    target: Value to return, or callable to invoke.\n\n"
               "Returns:\n"
               "    A callable that ignores its arguments and returns target.\n\n"
               "Example:\n"
               "    >>> f = always(42)\n"
               "    >>> f('ignored', 'args')  # 42\n"
               "    >>> g = always(random.random)\n"
               "    >>> g()  # new random value each call",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    .tp_members = members,
    .tp_descr_get = descr_get,
    .tp_init = (initproc)init,
    .tp_new = PyType_GenericNew,
};
