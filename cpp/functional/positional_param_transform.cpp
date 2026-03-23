#include "functional.h"
#include <structmember.h>

struct PositionalParamTransform : public PyObject {
    int index;
    retracesoftware::FastCall func;
    retracesoftware::FastCall transform;
    vectorcallfunc vectorcall;
};

static PyObject * vectorcall_impl(PositionalParamTransform * self, PyObject * const * args, size_t nargsf, PyObject * kwnames) {
    size_t nargs = PyVectorcall_NARGS(nargsf);
    if (nargs <= (size_t)self->index) {
        PyErr_Format(
            PyExc_IndexError,
            "positional_param_transform(%d): expected at least %d positional args, got %zd",
            self->index,
            self->index + 1,
            nargs);
        return nullptr;
    }

    size_t all = nargs + (kwnames ? (size_t)PyTuple_Size(kwnames) : 0);
    PyObject ** mem = (PyObject **)alloca(sizeof(PyObject *) * (all + 1)) + 1;

    for (size_t i = 0; i < all; i++) {
        mem[i] = args[i];
    }

    PyObject * transformed = self->transform(args[self->index]);
    if (!transformed) {
        return nullptr;
    }

    mem[self->index] = transformed;
    PyObject * result = self->func(mem, nargs | PY_VECTORCALL_ARGUMENTS_OFFSET, kwnames);
    Py_DECREF(transformed);
    return result;
}

static int traverse(PositionalParamTransform * self, visitproc visit, void * arg) {
    Py_VISIT(self->transform.callable);
    Py_VISIT(self->func.callable);
    return 0;
}

static int clear(PositionalParamTransform * self) {
    Py_CLEAR(self->transform.callable);
    Py_CLEAR(self->func.callable);
    return 0;
}

static void dealloc(PositionalParamTransform * self) {
    PyObject_GC_UnTrack(self);
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject * getattro(PositionalParamTransform * self, PyObject * name) {
    return PyObject_GetAttr(self->func.callable, name);
}

static int setattro(PositionalParamTransform * self, PyObject * name, PyObject * value) {
    return PyObject_SetAttr(self->func.callable, name, value);
}

static int init(PositionalParamTransform * self, PyObject * args, PyObject * kwds) {
    PyObject * function;
    PyObject * transform;
    int index;

    static const char * kwlist[] = {"function", "transform", "index", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OOi", (char **)kwlist, &function, &transform, &index)) {
        return -1;
    }
    if (index < 0) {
        PyErr_SetString(PyExc_ValueError, "positional_param_transform index must be a non-negative int");
        return -1;
    }

    self->func = retracesoftware::FastCall(function);
    self->transform = retracesoftware::FastCall(transform);

    Py_INCREF(function);
    Py_INCREF(transform);

    self->index = index;
    self->vectorcall = (vectorcallfunc)vectorcall_impl;
    return 0;
}

static PyObject * descr_get(PyObject * self, PyObject * obj, PyObject * type) {
    return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
}

PyTypeObject PositionalParamTransform_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "positional_param_transform",
    .tp_basicsize = sizeof(PositionalParamTransform),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(PositionalParamTransform, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_getattro = (getattrofunc)getattro,
    .tp_setattro = (setattrofunc)setattro,
    .tp_flags = Py_TPFLAGS_DEFAULT |
                Py_TPFLAGS_HAVE_GC |
                Py_TPFLAGS_HAVE_VECTORCALL |
                Py_TPFLAGS_METHOD_DESCRIPTOR,
    .tp_doc = "positional_param_transform(function, transform, index)\n--\n\n"
              "Transform one positional argument before calling function.\n\n"
              "Applies 'transform' to the positional argument at 'index' and leaves\n"
              "all other positional arguments and kwargs unchanged.\n\n"
              "Args:\n"
              "    function: The target callable.\n"
              "    transform: Applied to the selected positional argument.\n"
              "    index: Positional argument index to transform.\n\n"
              "Returns:\n"
              "    A callable that transforms one positional arg before calling function.",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    .tp_descr_get = descr_get,
    .tp_init = (initproc)init,
    .tp_new = PyType_GenericNew,
};
