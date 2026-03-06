#include "functional.h"

// ============================================================================
// ArityDispatch — fast dispatch based on number of positional arguments.
//
// Constructed with N callable handlers. Handler[i] handles calls with exactly
// i positional args. The last handler is the generic fallback for nargs >= N-1.
//
// Hot path: one integer comparison, one array index, one indirect call.
// All handlers are stored as FastCall for direct vectorcall invocation.
//
// Example:
//   d = arity_dispatch(handle_zero, handle_one, handle_generic)
//   d()          → handle_zero()
//   d(x)         → handle_one(x)
//   d(x, y)      → handle_generic(x, y)
//   d(x, y, z)   → handle_generic(x, y, z)
// ============================================================================

struct ArityDispatch : public PyVarObject {
    vectorcallfunc vectorcall;
    retracesoftware::FastCall handlers[];

    static PyObject * call(ArityDispatch * self, PyObject ** args, size_t nargsf, PyObject * kwnames) {
        Py_ssize_t nargs = PyVectorcall_NARGS(nargsf);
        Py_ssize_t count = Py_SIZE(self);
        Py_ssize_t idx = nargs < count ? nargs : count - 1;
        return self->handlers[idx](args, nargsf, kwnames);
    }

    static PyObject * create(PyTypeObject * type, PyObject * args, PyObject * kwds) {
        Py_ssize_t n = PyTuple_Size(args);

        if (n < 2) {
            PyErr_SetString(PyExc_TypeError,
                "arity_dispatch requires at least 2 handlers "
                "(one or more specific + a generic fallback)");
            return nullptr;
        }

        // Validate all handlers are callable
        for (Py_ssize_t i = 0; i < n; i++) {
            PyObject * handler = PyTuple_GetItem(args, i);
            if (!PyCallable_Check(handler)) {
                PyErr_Format(PyExc_TypeError,
                    "arity_dispatch handler %zd must be callable, got %S",
                    i, handler);
                return nullptr;
            }
        }

        ArityDispatch * self = (ArityDispatch *)ArityDispatch_Type.tp_alloc(
            &ArityDispatch_Type, n);
        if (!self) return nullptr;

        for (Py_ssize_t i = 0; i < n; i++) {
            self->handlers[i] = retracesoftware::FastCall(
                Py_NewRef(PyTuple_GetItem(args, i)));
        }

        self->vectorcall = (vectorcallfunc)ArityDispatch::call;

        return (PyObject *)self;
    }

    static void dealloc(ArityDispatch * self) {
        PyObject_GC_UnTrack(self);
        for (Py_ssize_t i = 0; i < Py_SIZE(self); i++) {
            Py_XDECREF(self->handlers[i].callable);
        }
        Py_TYPE(self)->tp_free((PyObject *)self);
    }

    static int traverse(ArityDispatch * self, visitproc visit, void * arg) {
        for (Py_ssize_t i = 0; i < Py_SIZE(self); i++) {
            Py_VISIT(self->handlers[i].callable);
        }
        return 0;
    }

    static int clear(ArityDispatch * self) {
        for (Py_ssize_t i = 0; i < Py_SIZE(self); i++) {
            Py_CLEAR(self->handlers[i].callable);
        }
        return 0;
    }

    static PyObject * getattro(ArityDispatch * self, PyObject * name) {
        // Forward attribute access to the fallback handler for introspection
        PyObject * result = PyObject_GenericGetAttr((PyObject *)self, name);
        if (result) return result;

        PyErr_Clear();
        return PyObject_GetAttr(self->handlers[Py_SIZE(self) - 1].callable, name);
    }

    static PyObject * descr_get(PyObject * self, PyObject * obj, PyObject * type) {
        return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
    }
};

static PyObject * repr(ArityDispatch * self) {
    Py_ssize_t n = Py_SIZE(self);

    PyObject * parts = PyUnicode_FromString(MODULE "arity_dispatch(");
    if (!parts) return nullptr;

    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject * sep = i > 0
            ? PyUnicode_FromFormat(", %R", self->handlers[i].callable)
            : PyUnicode_FromFormat("%R", self->handlers[i].callable);
        if (!sep) { Py_DECREF(parts); return nullptr; }

        PyObject * joined = PyUnicode_Concat(parts, sep);
        Py_DECREF(parts);
        Py_DECREF(sep);
        if (!joined) return nullptr;
        parts = joined;
    }

    PyObject * close = PyUnicode_FromString(")");
    if (!close) { Py_DECREF(parts); return nullptr; }

    PyObject * result = PyUnicode_Concat(parts, close);
    Py_DECREF(parts);
    Py_DECREF(close);
    return result;
}

PyTypeObject ArityDispatch_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "arity_dispatch",
    .tp_basicsize = sizeof(ArityDispatch),
    .tp_itemsize = sizeof(retracesoftware::FastCall),
    .tp_dealloc = (destructor)ArityDispatch::dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(ArityDispatch, vectorcall),
    .tp_repr = (reprfunc)repr,
    .tp_call = PyVectorcall_Call,
    .tp_str = (reprfunc)repr,
    .tp_getattro = (getattrofunc)ArityDispatch::getattro,
    .tp_flags = Py_TPFLAGS_DEFAULT |
                Py_TPFLAGS_HAVE_GC |
                Py_TPFLAGS_HAVE_VECTORCALL |
                Py_TPFLAGS_METHOD_DESCRIPTOR,
    .tp_doc = "arity_dispatch(handler_0, handler_1, ..., handler_n)\n--\n\n"
              "Fast dispatch based on the number of positional arguments.\n\n"
              "handler_i is called when nargs == i. The last handler is the\n"
              "generic fallback for any nargs >= (n - 1).\n\n"
              "Example:\n"
              "    >>> d = arity_dispatch(handle_zero, handle_one, handle_generic)\n"
              "    >>> d()          # → handle_zero()\n"
              "    >>> d(x)         # → handle_one(x)\n"
              "    >>> d(x, y)      # → handle_generic(x, y)\n"
              "    >>> d(x, y, z)   # → handle_generic(x, y, z)\n",
    .tp_traverse = (traverseproc)ArityDispatch::traverse,
    .tp_clear = (inquiry)ArityDispatch::clear,
    .tp_descr_get = ArityDispatch::descr_get,
    .tp_new = (newfunc)ArityDispatch::create,
};
