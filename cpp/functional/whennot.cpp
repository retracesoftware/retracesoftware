#include "functional.h"
#include <structmember.h>

struct WhenNot : public PyObject {
    retracesoftware::FastCall predicate;
    retracesoftware::FastCall action;
    vectorcallfunc vectorcall;
};

static PyObject * vectorcall(WhenNot * self, PyObject** args, size_t nargsf, PyObject* kwnames) {
    PyObject * result = self->predicate(args, nargsf, kwnames);
    if (!result) {
        return nullptr;
    }

    int is_true = PyObject_IsTrue(result);
    Py_DECREF(result);
    if (is_true < 0) {
        return nullptr;
    }

    if (is_true) {
        size_t nargs = PyVectorcall_NARGS(nargsf);
        return Py_NewRef(nargs > 0 ? args[0] : Py_None);
    }

    return self->action(args, nargsf, kwnames);
}

static int traverse(WhenNot* self, visitproc visit, void* arg) {
    Py_VISIT(self->predicate.callable);
    Py_VISIT(self->action.callable);
    return 0;
}

static int clear(WhenNot* self) {
    Py_CLEAR(self->predicate.callable);
    Py_CLEAR(self->action.callable);
    return 0;
}

static void dealloc(WhenNot *self) {
    PyObject_GC_UnTrack(self);
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static int init(WhenNot *self, PyObject *args, PyObject *kwds) {
    PyObject * predicate = nullptr;
    PyObject * action = nullptr;

    static const char *kwlist[] = {"predicate", "action", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OO", (char **)kwlist, &predicate, &action)) {
        return -1;
    }

    CHECK_CALLABLE(predicate);
    CHECK_CALLABLE(action);

    self->predicate = retracesoftware::FastCall(predicate);
    Py_INCREF(predicate);
    self->action = retracesoftware::FastCall(action);
    Py_INCREF(action);
    self->vectorcall = (vectorcallfunc)vectorcall;

    return 0;
}

static PyObject* descr_get(PyObject *self, PyObject *obj, PyObject *type) {
    return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
}

PyTypeObject WhenNot_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "when_not",
    .tp_basicsize = sizeof(WhenNot),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(WhenNot, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "when_not(predicate, action)\n--\n\n"
               "Return the first argument unchanged when predicate is truthy,\n"
               "otherwise call action with the original arguments.\n\n"
               "Args:\n"
               "    predicate: Predicate callable.\n"
               "    action: Callable used when predicate is falsy.\n\n"
               "Returns:\n"
               "    A callable that skips action when predicate matches.",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    .tp_descr_get = descr_get,
    .tp_init = (initproc)init,
    .tp_new = PyType_GenericNew,
};
