#include "functional.h"
#include "object.h"
#include "pyerrors.h"
#include <structmember.h>

struct Either : public PyObject {
    vectorcallfunc vectorcall;
    retracesoftware::FastCall a;
    retracesoftware::FastCall b;
};

static PyObject * vectorcall(Either * self, PyObject** args, size_t nargsf, PyObject* kwnames) {

    PyObject * res_a = self->a(args, nargsf, kwnames);
    if (!res_a) return nullptr;

    if (res_a == Py_None) {
        Py_DECREF(res_a);
        return self->b(args, nargsf, kwnames);
    } else {
        return res_a;
    }
}

static int traverse(Either* self, visitproc visit, void* arg) {
    Py_VISIT(self->a.callable);
    Py_VISIT(self->b.callable);
    return 0;
}

static int clear(Either* self) {
    Py_CLEAR(self->a.callable);
    Py_CLEAR(self->b.callable);
    return 0;
}

static void dealloc(Either *self) {
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static int init(Either *self, PyObject *args, PyObject *kwds) {

    PyObject * a;
    PyObject * b;

    static const char *kwlist[] = {"a", "b", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OO", (char **)kwlist, &a, &b))
    {
        return -1; // Return NULL on failure
    }

    self->a = retracesoftware::FastCall(a);
    self->b = retracesoftware::FastCall(b);
    Py_INCREF(self->a.callable);
    Py_INCREF(self->b.callable);

    self->vectorcall = (vectorcallfunc)vectorcall;
    return 0;
}

static PyMemberDef members[] = {
    // {"elements", T_OBJECT, offsetof(CasePredicate, elements), READONLY, "TODO"},
    {NULL}  /* Sentinel */
};

PyTypeObject Either_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "either",
    .tp_basicsize = sizeof(Either),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(Either, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "either(a, b)\n--\n\n"
               "Try function 'a' first; if it returns None, try 'b' instead.\n\n"
               "Optimized two-function fallback with cached vectorcall.\n\n"
               "Args:\n"
               "    a: Primary function to try.\n"
               "    b: Fallback function if a returns None.\n\n"
               "Returns:\n"
               "    Result of a if non-None, otherwise result of b.",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    .tp_members = members,
    .tp_init = (initproc)init,
    .tp_new = PyType_GenericNew,
};
