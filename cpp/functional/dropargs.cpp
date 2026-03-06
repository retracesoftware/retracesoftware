#include "functional.h"
#include <structmember.h>

struct DropArgs : public PyObject {
    retracesoftware::FastCall f;
    unsigned int to_drop;
    vectorcallfunc vectorcall;
};

static PyObject * vectorcall(DropArgs * self, PyObject* const* args, size_t nargsf, PyObject* kwnames) {

    size_t nargs = PyVectorcall_NARGS(nargsf);

    if (self->to_drop > nargs) {
        PyErr_Format(PyExc_RuntimeError, "error calling dropargs %i, when only %i positional parameters", self->to_drop, nargs);
        return nullptr;
    }
    return self->f(args + self->to_drop, nargs - self->to_drop, kwnames);
}

static int traverse(DropArgs* self, visitproc visit, void* arg) {
    Py_VISIT(self->f.callable);
    return 0;
}

static int clear(DropArgs* self) {
    Py_CLEAR(self->f.callable);
    return 0;
}

static void dealloc(DropArgs *self) {
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static PyMemberDef members[] = {
    {"function", T_OBJECT, OFFSET_OF_MEMBER(DropArgs, f), READONLY, "The wrapped function to call after dropping args."},
    {"__vectorcalloffset__", 
        T_PYSSIZET,
        OFFSET_OF_MEMBER(DropArgs, vectorcall),
        READONLY,
        "Offset of vectorcall function pointer."},
    {NULL}  /* Sentinel */
};

static int init(DropArgs * self, PyObject *args, PyObject *kwds) {
    PyObject * function;
    unsigned int to_drop = 1;

    static const char *kwlist[] = {"function", "to_drop", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|I", (char **)kwlist, &function, &to_drop))
    {
        return -1; // Return NULL on failure
    }

    self->f = retracesoftware::FastCall(function);
    Py_INCREF(function);

    self->vectorcall = (vectorcallfunc)vectorcall;
    self->to_drop = to_drop;
    return 0;
}

static PyObject* descr_get(PyObject *self, PyObject *obj, PyObject *type) {
    return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
}

static PyType_Slot slots[] = {
    {Py_tp_dealloc, (void*)dealloc},
    {Py_tp_call, (void*)PyVectorcall_Call},
    {Py_tp_doc, (void*)"dropargs(function, to_drop=1)\n--\n\n"
               "Drop the first N positional arguments before calling function.\n\n"
               "Useful for adapting callbacks that receive extra context args.\n\n"
               "Args:\n"
               "    function: The target callable.\n"
               "    to_drop: Number of leading positional args to drop (default 1).\n\n"
               "Returns:\n"
               "    A callable: dropargs(f, 2)(a, b, c) == f(c)"},
    {Py_tp_traverse, (void*)traverse},
    {Py_tp_clear, (void*)clear},
    {Py_tp_members, (void*)members},
    {Py_tp_descr_get, (void*)descr_get},
    {Py_tp_init, (void*)init},
    {0, NULL}
};

PyType_Spec DropArgs_Spec = {
    .name = MODULE "dropargs",
    .basicsize = sizeof(DropArgs),
    .itemsize = 0,
    .flags = Py_TPFLAGS_DEFAULT | 
             Py_TPFLAGS_HAVE_GC | 
             Py_TPFLAGS_MANAGED_DICT | 
             Py_TPFLAGS_HAVE_VECTORCALL |
             Py_TPFLAGS_METHOD_DESCRIPTOR |
             Py_TPFLAGS_BASETYPE,
    .slots = slots
};
