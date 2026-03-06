#include "functional.h"
#include "object.h"
#include <structmember.h>

struct DeepWrap : public PyObject {
    retracesoftware::FastCall target;
    retracesoftware::FastCall wrapper;
    vectorcallfunc vectorcall;
};

static PyObject * vectorcall(DeepWrap * self, PyObject** args, size_t nargsf, PyObject* kwnames) {

    PyObject * result = self->target(args, nargsf, kwnames);

    if (!result) return nullptr;

    PyObject * new_result = self->wrapper(result);

    Py_DECREF(result);

    if (!new_result || !PyCallable_Check(new_result)) return new_result;

    DeepWrap * next = (DeepWrap *)DeepWrap_Type.tp_alloc(&DeepWrap_Type, 0);

    if (next) {
        next->target = new_result;
        Py_INCREF(self->wrapper.callable);
        next->wrapper = self->wrapper;
        next->vectorcall = self->vectorcall;
    } else {
        Py_DECREF(new_result);
    }
    return next;
}

static int traverse(DeepWrap* self, visitproc visit, void* arg) {
    Py_VISIT(self->target.callable);
    Py_VISIT(self->wrapper.callable);
    return 0;
}

static int clear(DeepWrap* self) {
    Py_CLEAR(self->target.callable);
    Py_CLEAR(self->wrapper.callable);
    return 0;
}

static void dealloc(DeepWrap *self) {

    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static PyObject * repr(DeepWrap *self) {
    return PyUnicode_FromFormat(MODULE "deepwrap(wrapper = %S, target = %S)", self->wrapper.callable, self->target.callable);
}

// static PyMemberDef members[] = {
//     {"functions", T_OBJECT, offsetof(Compose, functions), READONLY, "TODO"},
//     {NULL}  /* Sentinel */
// };

static PyObject * getattro(DeepWrap *self, PyObject *name) {
    return PyObject_GetAttr(self->target.callable, name);
}

static int setattro(DeepWrap *self, PyObject *name, PyObject * value) {
    return PyObject_SetAttr(self->target.callable, name, value);
}

static int init(DeepWrap *self, PyObject *args, PyObject *kwds) {

    PyObject * target;
    PyObject * wrapper;

    static const char *kwlist[] = {"wrapper", "target", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OO", (char **)kwlist, &wrapper, &target))
    {
        return -1; // Return NULL on failure
    }

    if (!PyCallable_Check(target)) {
        PyErr_Format(PyExc_TypeError, 
            "Error constructing: %s, parameter: target = %S, was not callable", DeepWrap_Type.tp_name, target);
        return -1;
    }
    if (!PyCallable_Check(wrapper)) {
        PyErr_Format(PyExc_TypeError, 
            "Error constructing: %s, parameter: wrapper = %S, was not callable", DeepWrap_Type.tp_name, wrapper);
        return -1;
    }

    self->target = retracesoftware::FastCall(target);
    self->wrapper = retracesoftware::FastCall(wrapper);
    
    Py_INCREF(target);
    Py_INCREF(wrapper);

    self->vectorcall = (vectorcallfunc)vectorcall;

    return 0;
}

static PyObject* descr_get(PyObject *self, PyObject *obj, PyObject *type) {
    return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
}

PyTypeObject DeepWrap_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "deepwrap",
    .tp_basicsize = sizeof(DeepWrap),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(DeepWrap, vectorcall),
    .tp_repr = (reprfunc)repr,
    .tp_call = PyVectorcall_Call,
    .tp_str = (reprfunc)repr,
    .tp_getattro = (getattrofunc)getattro,
    .tp_setattro = (setattrofunc)setattro,
    .tp_flags = Py_TPFLAGS_DEFAULT | 
                Py_TPFLAGS_HAVE_GC | 
                Py_TPFLAGS_HAVE_VECTORCALL | 
                Py_TPFLAGS_METHOD_DESCRIPTOR,
    .tp_doc = "deepwrap(wrapper, target)\n--\n\n"
               "Recursively wrap callable results with a transformer.\n\n"
               "Calls target, applies wrapper to the result. If the result is\n"
               "callable, returns a new deepwrap that will continue wrapping.\n"
               "Useful for wrapping APIs that return more callables.\n\n"
               "Args:\n"
               "    wrapper: Transform to apply to each result.\n"
               "    target: The initial callable to wrap.\n\n"
               "Returns:\n"
               "    A callable that wraps results recursively.",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    // .tp_members = members,
    .tp_descr_get = descr_get,
    .tp_init = (initproc)init,
    .tp_new = PyType_GenericNew,
};
