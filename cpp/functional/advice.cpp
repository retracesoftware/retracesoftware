#include "functional.h"
#include "pyerrors.h"
#include <structmember.h>

struct Advice {
    PyObject_HEAD
    PyObject * func;
    PyObject * on_call;
    PyObject * on_result;
    PyObject * on_error;
    vectorcallfunc vectorcall;
};

static PyObject * vectorcall(Advice * self, PyObject** args, size_t nargsf, PyObject* kwnames) {

    if (self->on_call) {
        PyObject * status = PyObject_Vectorcall(self->on_call, args, nargsf, kwnames);
        if (!status) return nullptr;
        Py_DECREF(status);
    }

    PyObject * result = PyObject_Vectorcall(self->func, args, nargsf, kwnames);

    if (result) {
        if (self->on_result) {
            PyObject * status = PyObject_CallOneArg(self->on_result, result);
            if (!status) return nullptr;
            Py_DECREF(status);    
        }
    } else if (self->on_error) {
        assert(PyErr_Occurred());

        PyObject *exc_type, *exc_value, *exc_traceback;

        PyErr_Fetch(&exc_type, &exc_value, &exc_traceback);

        PyObject * status = PyObject_CallFunctionObjArgs(
            self->on_error,
            exc_type ? exc_type : Py_None,
            exc_value ? exc_value : Py_None,
            exc_traceback ? exc_traceback : Py_None,
            nullptr
        );
        if (!status) return nullptr;
        Py_DECREF(status);
        PyErr_Restore(exc_type, exc_value, exc_traceback);
    }

    return result;
}

static int traverse(Advice* self, visitproc visit, void* arg) {
    Py_VISIT(self->func);
    Py_VISIT(self->on_call);
    Py_VISIT(self->on_result);
    Py_VISIT(self->on_error);

    return 0;
}

static int clear(Advice* self) {
    Py_CLEAR(self->func);
    Py_CLEAR(self->on_call);
    Py_CLEAR(self->on_result);
    Py_CLEAR(self->on_error);
    return 0;
}

static void dealloc(Advice *self) {
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static PyMemberDef members[] = {
    {"function", T_OBJECT, offsetof(Advice, func), READONLY, "The wrapped function being advised."},
    {"on_call", T_OBJECT, offsetof(Advice, on_call), 0, "Callback invoked before the function with the same args."},
    {"on_result", T_OBJECT, offsetof(Advice, on_result), 0, "Callback invoked after success with the result."},
    {"on_error", T_OBJECT, offsetof(Advice, on_error), 0, "Callback invoked on exception with (type, value, traceback)."},
    {NULL}  /* Sentinel */
};

static PyObject * create(PyTypeObject *type, PyObject *args, PyObject *kwds) {

    PyObject * func;
    PyObject * on_call = nullptr;
    PyObject * on_result = nullptr;
    PyObject * on_error = nullptr;
    
    static const char *kwlist[] = {"function", "on_call","on_result", "on_error", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|OOO", (char **)kwlist, 
        &func, 
        &on_call,
        &on_result,
        &on_error))
    {
        return NULL; // Return NULL on failure
    }
    
    Advice * self = (Advice *)type->tp_alloc(type, 0);

    if (!self) {
        return NULL;
    }

    self->func = Py_NewRef(func);
    self->on_call = on_call != Py_None ? Py_XNewRef(on_call) : nullptr;
    self->on_result = on_result != Py_None ? Py_XNewRef(on_result) : nullptr;
    self->on_error = on_error != Py_None ? Py_XNewRef(on_error) : nullptr;

    self->vectorcall = (vectorcallfunc)vectorcall;

    return (PyObject *)self;
}

PyTypeObject Advice_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "advice",
    .tp_basicsize = sizeof(Advice),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = offsetof(Advice, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "advice(function, on_call=None, on_result=None, on_error=None)\n--\n\n"
               "Wrap a function with before/after/error hooks (AOP-style advice).\n\n"
               "Hooks are called for side effects; the wrapped function's result\n"
               "is returned. Exceptions propagate after on_error is called.\n\n"
               "Args:\n"
               "    function: The callable to wrap.\n"
               "    on_call: Called before function with the same arguments.\n"
               "    on_result: Called after success with the result value.\n"
               "    on_error: Called on exception with (exc_type, exc_value, exc_tb).\n\n"
               "Returns:\n"
               "    A wrapped callable that invokes hooks around the function.",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    .tp_members = members,
    .tp_new = (newfunc)create,
};
