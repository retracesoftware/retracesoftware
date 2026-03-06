#include "functional.h"
#include <structmember.h>

struct Intercept : public PyObject {
    
    PyObject * function;
    PyObject * on_call;
    PyObject * on_result;
    PyObject * on_error;

    vectorcallfunc vectorcall;
};

static PyObject * vectorcall(Intercept * self, PyObject** args, size_t nargsf, PyObject* kwnames) {

    if (self->on_call) {
        PyObject * res = PyObject_Vectorcall(self->on_call, args, nargsf, kwnames);
        if (!res) return nullptr;
        Py_DECREF(res);
    }

    PyObject * result = PyObject_Vectorcall(self->function, args, nargsf, kwnames);

    if (result) {
        if (self->on_result) {
            PyObject * res = PyObject_CallOneArg(self->on_result, result);
            if (!res) {
                Py_DECREF(result);
                return nullptr;
            }
            Py_DECREF(res);
        } 
    } else {
        if (self->on_error) {
            PyObject *ptype = NULL, *pvalue = NULL, *ptraceback = NULL;

            // Fetch the current exception
            PyErr_Fetch(&ptype, &pvalue, &ptraceback);

            PyObject * res = PyObject_CallFunctionObjArgs(self->on_error,
                ptype ? ptype : Py_None,
                pvalue ? pvalue : Py_None,
                ptraceback ? ptraceback : Py_None,
                nullptr);

            if (res) {
                Py_DECREF(res);
                PyErr_Restore(ptype, pvalue, ptraceback);
            } else {
                Py_XDECREF(ptype);
                Py_XDECREF(pvalue);
                Py_XDECREF(ptraceback);
                return nullptr;
            }
        }
    }
    return result;
}

static int traverse(Intercept* self, visitproc visit, void* arg) {
    Py_VISIT(self->function);
    Py_VISIT(self->on_call);
    Py_VISIT(self->on_result);
    Py_VISIT(self->on_error);

    return 0;
}

static int clear(Intercept* self) {
    Py_CLEAR(self->function);
    Py_CLEAR(self->on_call);
    Py_CLEAR(self->on_result);
    Py_CLEAR(self->on_error);
    return 0;
}

static void dealloc(Intercept *self) {
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static int init(Intercept *self, PyObject *args, PyObject *kwds) {

    PyObject * function = NULL;
    PyObject * on_call = NULL;
    PyObject * on_result = NULL;
    PyObject * on_error = NULL;

    static const char *kwlist[] = {
        "function",
        "on_call",
        "on_result",
        "on_error",
        NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|OOO", 
        (char **)kwlist,
        &function,
        &on_call,
        &on_result,
        &on_error))
    {
        return -1; // Return NULL on failure
    }

    CHECK_CALLABLE(function);
    CHECK_CALLABLE(on_call);
    CHECK_CALLABLE(on_result);
    CHECK_CALLABLE(on_error);
    
    self->function = Py_XNewRef(function);
    self->on_call = Py_XNewRef(on_call);
    self->on_result = Py_XNewRef(on_result);
    self->on_error = Py_XNewRef(on_error);
    self->vectorcall = (vectorcallfunc)vectorcall;

    return 0;
}

static PyMemberDef members[] = {
    {"on_call", T_OBJECT, OFFSET_OF_MEMBER(Intercept, on_call), 0, "Callback invoked before the function with the same args."},
    {"on_result", T_OBJECT, OFFSET_OF_MEMBER(Intercept, on_result), 0, "Callback invoked after success with the result."},
    {"on_error", T_OBJECT, OFFSET_OF_MEMBER(Intercept, on_error), 0, "Callback invoked on exception with (type, value, traceback)."},
    {"function", T_OBJECT, OFFSET_OF_MEMBER(Intercept, function), 0, "The wrapped function being intercepted."},
    {NULL}  /* Sentinel */
};

static PyObject* descr_get(PyObject *self, PyObject *obj, PyObject *type) {
    return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
}

static PyObject * repr(Intercept *self) {
    return PyUnicode_FromFormat(MODULE "Intercept(on_call = %S, on_result = %S, on_error = %S, function = %S)",
                                self->on_call ? self->on_call : Py_None,
                                self->on_result ? self->on_result : Py_None,
                                self->on_error ? self->on_error : Py_None,
                                self->function ? self->function : Py_None);
}

PyTypeObject Intercept_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "intercept",
    .tp_basicsize = sizeof(Intercept),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(Intercept, vectorcall),
    .tp_repr = (reprfunc)repr,
    .tp_call = PyVectorcall_Call,
    .tp_str = (reprfunc)repr,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL | Py_TPFLAGS_METHOD_DESCRIPTOR,
    .tp_doc = "intercept(function, on_call=None, on_result=None, on_error=None)\n--\n\n"
               "Intercept function calls with before/after/error hooks.\n\n"
               "Similar to advice() but can be used as a method descriptor.\n"
               "Hooks are for observation; the original result/exception propagates.\n\n"
               "Args:\n"
               "    function: The callable to intercept.\n"
               "    on_call: Called before function with the same arguments.\n"
               "    on_result: Called after success with the result value.\n"
               "    on_error: Called on exception with (exc_type, exc_value, exc_tb).\n\n"
               "Returns:\n"
               "    A wrapped callable that invokes hooks around the function.",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    .tp_members = members,
    .tp_descr_get = descr_get,
    .tp_init = (initproc)init,
    .tp_new = PyType_GenericNew,
};
