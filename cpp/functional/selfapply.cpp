#include "functional.h"
#include <structmember.h>

struct SelfApply {
    PyObject_HEAD
    PyObject * target;
    vectorcallfunc target_vectorcall;
    vectorcallfunc vectorcall;
};

static PyObject * vectorcall(SelfApply * self, PyObject** args, size_t nargsf, PyObject* kwnames) {

    PyObject * result = self->target_vectorcall(self->target, args, nargsf, kwnames);
    
    if (!result) {
        return nullptr;
    }

    if (!PyCallable_Check(result)) {
        PyErr_Format(PyExc_TypeError, "selfapply of function: %S returned: %S which isnt callable", self->target, result);
        Py_DECREF(result);
        return nullptr;        
    }

    PyObject * final_result = PyObject_Vectorcall(result, args, nargsf, kwnames);

    Py_DECREF(result);

    return final_result;
}

static int traverse(SelfApply* self, visitproc visit, void* arg) {
    Py_VISIT(self->target);
    return 0;
}

static int clear(SelfApply* self) {
    Py_CLEAR(self->target);
    return 0;
}

static void dealloc(SelfApply *self) {
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static int init(SelfApply *self, PyObject *args, PyObject *kwds) {

    PyObject * target = NULL;

    static const char *kwlist[] = {"target", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O", (char **)kwlist, &target))
    {
        return -1; // Return NULL on failure
    }

    self->target = Py_XNewRef(target);
    self->target_vectorcall = extract_vectorcall(target);

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

PyTypeObject SelfApply_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "selfapply",
    .tp_basicsize = sizeof(SelfApply),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(SelfApply, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "selfapply(target)\n--\n\n"
               "Call target, then call its result with the same arguments.\n\n"
               "Equivalent to: lambda *args: target(*args)(*args)\n"
               "Useful for factory patterns where the factory returns a processor.\n\n"
               "Args:\n"
               "    target: A callable that returns another callable.\n\n"
               "Returns:\n"
               "    A callable that applies the result of target to the same args.\n\n"
               "Example:\n"
               "    >>> def pick_handler(x): return handler_for(type(x))\n"
               "    >>> process = selfapply(pick_handler)\n"
               "    >>> process(data)  # picks handler, then applies it to data",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    .tp_members = members,
    .tp_descr_get = descr_get,
    .tp_init = (initproc)init,
    .tp_new = PyType_GenericNew,
};
