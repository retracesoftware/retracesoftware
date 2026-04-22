#include "functional.h"
#include <structmember.h>

struct Iterate : public PyObject {
    retracesoftware::FastCall function;
    PyObject * current;

    static int init(Iterate * self, PyObject * args, PyObject * kwds) {
        PyObject * function = nullptr;
        PyObject * seed = nullptr;

        if (kwds && PyDict_GET_SIZE(kwds) > 0) {
            PyErr_SetString(PyExc_TypeError, "iterate() does not accept keyword arguments");
            return -1;
        }

        if (!PyArg_ParseTuple(args, "OO", &function, &seed)) {
            return -1;
        }

        if (!PyCallable_Check(function)) {
            PyErr_Format(PyExc_TypeError, "iterate() expects a callable, got %S", function);
            return -1;
        }

        self->function = retracesoftware::FastCall(Py_NewRef(function));
        self->current = Py_NewRef(seed);
        return 0;
    }

    static int traverse(Iterate * self, visitproc visit, void * arg) {
        Py_VISIT(self->function.callable);
        Py_VISIT(self->current);
        return 0;
    }

    static int clear(Iterate * self) {
        Py_CLEAR(self->function.callable);
        Py_CLEAR(self->current);
        return 0;
    }

    static void dealloc(Iterate * self) {
        PyObject_GC_UnTrack(self);
        clear(self);
        Py_TYPE(self)->tp_free((PyObject *)self);
    }

    static PyObject * iter(PyObject * self) {
        return Py_NewRef(self);
    }

    static PyObject * iternext(Iterate * self) {
        if (!self->current) {
            PyErr_SetNone(PyExc_StopIteration);
            return nullptr;
        }

        PyObject * result = Py_NewRef(self->current);
        PyObject * args[1] = {self->current};
        PyObject * next = self->function(args, 1, nullptr);

        if (!next) {
            Py_DECREF(result);
            return nullptr;
        }

        Py_SETREF(self->current, next);
        return result;
    }

    static PyObject * get_function(Iterate * self, void *) {
        return self->function.callable ? Py_NewRef(self->function.callable) : Py_NewRef(Py_None);
    }

    static PyObject * get_current(Iterate * self, void *) {
        return self->current ? Py_NewRef(self->current) : Py_NewRef(Py_None);
    }
};

static PyGetSetDef getset[] = {
    {"function", (getter)Iterate::get_function, nullptr, "The iterated function.", nullptr},
    {"current", (getter)Iterate::get_current, nullptr, "The next value to be yielded.", nullptr},
    {nullptr}
};

PyTypeObject Iterate_Type = {
    .ob_base = PyVarObject_HEAD_INIT(nullptr, 0)
    .tp_name = MODULE "iterate",
    .tp_basicsize = sizeof(Iterate),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)Iterate::dealloc,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_BASETYPE,
    .tp_doc = "iterate(function, seed)\n--\n\n"
              "Create an iterator yielding seed, function(seed), function(function(seed)), ...\n\n"
              "Args:\n"
              "    function: Callable applied to the previous value.\n"
              "    seed: Initial yielded value.\n\n"
              "Returns:\n"
              "    An iterator over repeated application of function.",
    .tp_traverse = (traverseproc)Iterate::traverse,
    .tp_clear = (inquiry)Iterate::clear,
    .tp_iter = Iterate::iter,
    .tp_iternext = (iternextfunc)Iterate::iternext,
    .tp_getset = getset,
    .tp_init = (initproc)Iterate::init,
    .tp_new = PyType_GenericNew,
};
