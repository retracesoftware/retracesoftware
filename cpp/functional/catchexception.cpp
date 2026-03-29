#include "functional.h"
#include <structmember.h>

struct CatchException : public PyObject {
    retracesoftware::FastCall function;
    PyObject * exception_type;
    retracesoftware::FastCall handler;

    vectorcallfunc vectorcall;
};

static PyObject * vectorcall(CatchException * self, PyObject ** args, size_t nargsf, PyObject * kwnames) {
    PyObject * result = self->function(args, nargsf, kwnames);
    if (result) return result;

    if (!PyErr_ExceptionMatches(self->exception_type)) return nullptr;

    PyObject * ptype = nullptr;
    PyObject * pvalue = nullptr;
    PyObject * ptraceback = nullptr;
    PyErr_Fetch(&ptype, &pvalue, &ptraceback);

    PyObject * handled = self->handler(args, nargsf, kwnames);

    Py_XDECREF(ptype);
    Py_XDECREF(pvalue);
    Py_XDECREF(ptraceback);

    return handled;
}

static int traverse(CatchException * self, visitproc visit, void * arg) {
    Py_VISIT(self->function.callable);
    Py_VISIT(self->exception_type);
    Py_VISIT(self->handler.callable);
    return 0;
}

static int clear(CatchException * self) {
    Py_CLEAR(self->function.callable);
    Py_CLEAR(self->exception_type);
    Py_CLEAR(self->handler.callable);
    return 0;
}

static void dealloc(CatchException * self) {
    PyObject_GC_UnTrack(self);
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static int init(CatchException * self, PyObject * args, PyObject * kwds) {
    PyObject * function = nullptr;
    PyObject * exception_type = nullptr;
    PyObject * handler = nullptr;

    static const char * kwlist[] = {
        "function",
        "exception_type",
        "handler",
        nullptr,
    };

    if (!PyArg_ParseTupleAndKeywords(
            args,
            kwds,
            "OOO",
            (char **)kwlist,
            &function,
            &exception_type,
            &handler)) {
        return -1;
    }

    CHECK_CALLABLE(function);
    CHECK_CALLABLE(handler);

    self->function = retracesoftware::FastCall(function);
    Py_INCREF(function);

    self->exception_type = Py_NewRef(exception_type);

    self->handler = retracesoftware::FastCall(handler);
    Py_INCREF(handler);

    self->vectorcall = (vectorcallfunc)vectorcall;

    return 0;
}

static PyMemberDef members[] = {
    {"function", T_OBJECT, OFFSET_OF_MEMBER(CatchException, function.callable), 0, "Wrapped callable."},
    {"exception_type", T_OBJECT, OFFSET_OF_MEMBER(CatchException, exception_type), 0, "Caught exception type or tuple."},
    {"handler", T_OBJECT, OFFSET_OF_MEMBER(CatchException, handler.callable), 0, "Fallback callable invoked with the original arguments."},
    {NULL}
};

static PyObject * descr_get(PyObject * self, PyObject * obj, PyObject * type) {
    return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
}

static PyObject * repr(CatchException * self) {
    return PyUnicode_FromFormat(
        MODULE "catch_exception(function = %S, exception_type = %S, handler = %S)",
        self->function.callable ? self->function.callable : Py_None,
        self->exception_type ? self->exception_type : Py_None,
        self->handler.callable ? self->handler.callable : Py_None
    );
}

PyTypeObject CatchException_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "catch_exception",
    .tp_basicsize = sizeof(CatchException),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(CatchException, vectorcall),
    .tp_repr = (reprfunc)repr,
    .tp_call = PyVectorcall_Call,
    .tp_str = (reprfunc)repr,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL | Py_TPFLAGS_METHOD_DESCRIPTOR,
    .tp_doc = "catch_exception(function, exception_type, handler)\n--\n\n"
              "Call function(*args, **kwargs), and if it raises exception_type,\n"
              "call handler(*args, **kwargs) instead.\n\n"
              "Args:\n"
              "    function: The primary callable.\n"
              "    exception_type: Exception class or tuple to catch.\n"
              "    handler: Fallback callable invoked with the original arguments.\n\n"
              "Returns:\n"
              "    A wrapped callable with exception-catching fallback semantics.",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    .tp_members = members,
    .tp_descr_get = descr_get,
    .tp_init = (initproc)init,
    .tp_new = PyType_GenericNew,
};
