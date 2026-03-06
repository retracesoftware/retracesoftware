#include "functional.h"
#include "tupleobject.h"
#include <structmember.h>

static int run_predicate(PyObject * pred, PyObject** args, size_t nargsf, PyObject* kwnames) {
    PyObject * res = PyObject_Vectorcall(pred, args, nargsf, kwnames);

    if (!res) return -1;
    int status = PyObject_IsTrue(res);
    Py_DECREF(res);
    return status;
}

static PyObject * vectorcall(ManyPredicate * self, PyObject** args, size_t nargsf, PyObject* kwnames) {
    if (PyTuple_Check(self->elements)) {
        for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(self->elements); i++) {
            PyObject * pred = PyTuple_GET_ITEM(self->elements, i);
            int status = run_predicate(pred, args, nargsf, kwnames);

            if (status == 1) {
                Py_RETURN_TRUE;
            } else if (status == -1) {
                return NULL;
            }
        }
    }
    else if (PyList_Check(self->elements)) {
        for (Py_ssize_t i = 0; i < PyList_GET_SIZE(self->elements); i++) {
            PyObject * pred = PyList_GET_ITEM(self->elements, i);
            int status = run_predicate(pred, args, nargsf, kwnames);

            if (status == 1) {
                Py_RETURN_TRUE;        
            } else if (status == -1) {
                return NULL;
            }
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

            int status = run_predicate(item, args, nargsf, kwnames);
            Py_DECREF(item);

            if (status == 1) {
                Py_DECREF(iterator);
                Py_RETURN_TRUE;        
            } else if (status == -1) {
                Py_DECREF(iterator);
                return NULL;
            }
        }
        Py_DECREF(iterator); 
        if (PyErr_Occurred()) {
           return NULL;  // Propagate iteration errors (e.g., inside a generator)
        }
    }
    Py_RETURN_FALSE;
}

static PyObject * create(PyTypeObject *type, PyObject *args, PyObject *kwds) {

    for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(args); i++) {
        if (!PyCallable_Check(PyTuple_GET_ITEM(args, i))) {
            PyErr_Format(PyExc_TypeError, "Argument %i to %S is not callable", i, type);
            return NULL;
        }
    }

    ManyPredicate * self = (ManyPredicate *)type->tp_alloc(type, 0);

    if (!self) {
        return NULL;
    }

    assert(PyTuple_CheckExact(args));

    self->elements = Py_NewRef(args);

    self->vectorcall = (vectorcallfunc)vectorcall;

    return (PyObject *)self;
}

PyTypeObject OrPredicate_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "or_predicate",
    .tp_basicsize = ManyPredicate_Type.tp_basicsize,
    .tp_itemsize = 0,
    .tp_vectorcall_offset = ManyPredicate_Type.tp_vectorcall_offset,
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "or_predicate(*predicates)\n--\n\n"
               "Combine predicates with logical OR (short-circuit evaluation).\n\n"
               "Returns True if any predicate returns a truthy value.\n"
               "Short-circuits on the first truthy result.\n\n"
               "Args:\n"
               "    *predicates: Callable predicates to combine.\n\n"
               "Returns:\n"
               "    A predicate that returns True if any predicate is truthy.\n\n"
               "Example:\n"
               "    >>> is_special = or_predicate(is_zero, is_negative)\n"
               "    >>> is_special(0)   # True (short-circuits)\n"
               "    >>> is_special(5)   # False",
    .tp_traverse = ManyPredicate_Type.tp_traverse,
    .tp_clear = ManyPredicate_Type.tp_clear,
    .tp_base = &ManyPredicate_Type,
    .tp_new = (newfunc)create,
};
