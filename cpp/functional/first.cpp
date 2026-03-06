#include "functional.h"
#include "listobject.h"
#include "object.h"
#include "tupleobject.h"
#include <structmember.h>

struct First {
    PyObject_HEAD
    PyObject * elements;
    vectorcallfunc vectorcall;
};

static PyObject * vectorcall(First * self, PyObject** args, size_t nargsf, PyObject* kwnames) {
    if (PyTuple_Check(self->elements)) {
        for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(self->elements); i++) {
            PyObject * pred = PyTuple_GET_ITEM(self->elements, i);

            PyObject * res = PyObject_Vectorcall(pred, args, nargsf, kwnames);

            if (!res) return nullptr;
            else if (res != Py_None) return res;
            else Py_DECREF(res);
        }
    }
    else if (PyList_Check(self->elements)) {
        for (Py_ssize_t i = 0; i < PyList_GET_SIZE(self->elements); i++) {
            PyObject * pred = PyList_GET_ITEM(self->elements, i);

            PyObject * res = PyObject_Vectorcall(pred, args, nargsf, kwnames);

            if (!res) return nullptr;
            else if (res != Py_None) return res;
            else Py_DECREF(res);
        }
    } else {
        PyObject * iterator = PyObject_GetIter(self->elements);

        if (iterator == NULL) {
            PyErr_SetString(PyExc_TypeError, "Object is not iterable");
            return NULL;
        }

        PyObject * item;
        // Iterate through the elements
        while ((item = PyIter_Next(iterator))) {

            PyObject * res = PyObject_Vectorcall(item, args, nargsf, kwnames);

            if (!res) return nullptr;
            else if (res != Py_None) return res;
            else Py_DECREF(res);
        }
        Py_DECREF(iterator); 
        if (PyErr_Occurred()) {
           return NULL;  // Propagate iteration errors (e.g., inside a generator)
        }
    }
    Py_RETURN_NONE;
}

static int traverse(First* self, visitproc visit, void* arg) {
    Py_VISIT(self->elements);

    return 0;
}

static int clear(First* self) {
    Py_CLEAR(self->elements);
    return 0;
}

static void dealloc(First *self) {
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static PyMemberDef members[] = {
    {"elements", T_OBJECT, offsetof(First, elements), READONLY, "The sequence of functions to try."},
    {NULL}  /* Sentinel */
};

static PyObject * create(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    
    First * self = (First *)type->tp_alloc(type, 0);

    if (!self) {
        return NULL;
    }

    self->elements = Py_NewRef(args);

    self->vectorcall = (vectorcallfunc)vectorcall;

    return (PyObject *)self;
}

PyTypeObject First_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "first",
    .tp_basicsize = sizeof(First),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = offsetof(First, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "first(*functions)\n--\n\n"
               "Return the result of the first function that doesn't return None.\n\n"
               "Calls functions in order until one returns a non-None value.\n"
               "Returns None if all functions return None.\n\n"
               "Args:\n"
               "    *functions: Callables to try in order.\n\n"
               "Returns:\n"
               "    First non-None result, or None if all return None.\n\n"
               "Example:\n"
               "    >>> get_value = first(get_from_cache, get_from_db, get_default)\n"
               "    >>> get_value(key)  # tries each until non-None",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    .tp_members = members,
    .tp_new = (newfunc)create,
};
