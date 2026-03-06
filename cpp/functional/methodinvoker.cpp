#include "functional.h"
#include <structmember.h>

struct MethodInvoker {
    PyObject_HEAD
    vectorcallfunc vectorcall;
    PyObject * lookup_error;
    PyObject * obj;
    PyObject * methodname;
};

static PyObject * vectorcall(MethodInvoker * self, PyObject** args, size_t nargsf, PyObject* kwnames) {

    PyObject * bound = PyObject_GetAttr(self->obj, self->methodname);

    if (!bound) {
        if (PyErr_ExceptionMatches(PyExc_AttributeError) && self->lookup_error) {
            PyErr_Clear();
            PyErr_SetObject((PyObject *)Py_TYPE(self->lookup_error), self->lookup_error);
        }
        return nullptr;
    }

    PyObject * result = PyObject_Vectorcall(bound, args, nargsf, kwnames);

    Py_DECREF(bound);

    return result;
}

static int traverse(MethodInvoker* self, visitproc visit, void* arg) {
    Py_VISIT(self->obj);
    return 0;
}

static int clear(MethodInvoker* self) {
    Py_CLEAR(self->obj);
    Py_CLEAR(self->methodname);
    return 0;
}

static void dealloc(MethodInvoker *self) {    
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static PyObject * repr(MethodInvoker *self) {
    return PyUnicode_FromFormat(MODULE "methodinvoker(%S.%S)", self->obj, self->methodname);
}

static int init(MethodInvoker *self, PyObject *args, PyObject *kwds) {
    PyObject * obj;
    PyObject * methodname;
    PyObject * lookup_error = nullptr;

    static const char *kwlist[] = {"obj", "method_name", "lookup_error", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OO!|O", (char **)kwlist, &obj, &PyUnicode_Type, &methodname, &lookup_error))
    {
        return -1; // Return NULL on failure
    }

    self->obj = Py_NewRef(obj);
    self->methodname = Py_NewRef(methodname);
    self->vectorcall = (vectorcallfunc)vectorcall;
    self->lookup_error = Py_XNewRef(lookup_error);

    return 0;
}

static PyMemberDef members[] = {
    {"method_name", T_OBJECT, offsetof(MethodInvoker, methodname), READONLY, "The method name to look up on the object."},
    {"obj", T_OBJECT, offsetof(MethodInvoker, obj), READONLY, "The object on which to invoke the method."},
    {NULL}  /* Sentinel */
};

PyTypeObject MethodInvoker_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "method_invoker",
    .tp_basicsize = sizeof(MethodInvoker),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = offsetof(MethodInvoker, vectorcall),
    .tp_repr = (reprfunc)repr,
    .tp_call = PyVectorcall_Call,
    .tp_str = (reprfunc)repr,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "method_invoker(obj, method_name, lookup_error=None)\n--\n\n"
               "Create a callable that invokes a method on a fixed object.\n\n"
               "Looks up method_name on obj and calls it with provided arguments.\n"
               "If lookup fails and lookup_error is set, raises that instead.\n\n"
               "Args:\n"
               "    obj: The object on which to invoke the method.\n"
               "    method_name: String name of the method to call.\n"
               "    lookup_error: Exception to raise if method not found.\n\n"
               "Returns:\n"
               "    A callable: invoker(*args) == obj.method_name(*args)",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    .tp_members = members,
    .tp_init = (initproc)init,
    .tp_new = PyType_GenericNew,
};
