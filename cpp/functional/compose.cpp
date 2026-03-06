#include "functional.h"
#include "object.h"
#include <structmember.h>
#include <signal.h>
#include <functional>

struct Compose {
    PyObject_HEAD
    vectorcallfunc vectorcall;
    PyObject * functions;
};

static PyObject * vectorcall(Compose * self, PyObject** args, size_t nargsf, PyObject* kwnames) {

    if (PyTuple_CheckExact(self->functions)) {
        
        PyObject * first = PyTuple_GET_ITEM(self->functions, 0);

        // PyObject_Print((PyObject *)self, stdout, 0);
        // printf("\n");
        // printf("Foo: %p %p\n", self, first);

        PyObject * result = PyObject_Vectorcall(first, args, nargsf, kwnames);

        if (!result) return nullptr;

        for (Py_ssize_t i = 1; i < PyTuple_GET_SIZE(self->functions); i++) {

            PyObject * new_result = PyObject_CallOneArg(PyTuple_GET_ITEM(self->functions, i), result);

            Py_DECREF(result);

            if (!new_result) {
                return nullptr;
            }
            result = new_result;
        }
        return result;
    } else if (PyList_CheckExact(self->functions)) {

        PyObject * result = PyObject_Vectorcall(PyList_GET_ITEM(self->functions, 0), args, nargsf, kwnames);

        if (!result) return nullptr;

        for (Py_ssize_t i = 1; i < PyList_GET_SIZE(self->functions); i++) {

            PyObject * new_result = PyObject_CallOneArg(PyList_GET_ITEM(self->functions, i), result);

            Py_DECREF(result);

            if (!new_result) {
                return nullptr;
            }
            result = new_result;
        }
        return result;
    } else {

        PyObject *iter = PyObject_GetIter(self->functions);

        if (!iter) {
            return NULL;  // Not iterable or error occurred
        }

        PyObject *item = PyIter_Next(iter);

        if (item) {
            PyObject * result = PyObject_Vectorcall(item, args, nargsf, kwnames);

            Py_DECREF(item);
            
            while ((item = PyIter_Next(iter)) != NULL) {
                
                PyObject * new_result = PyObject_CallOneArg(item, result);
                Py_DECREF(item);
                Py_DECREF(result);

                if (!new_result) {
                    Py_DECREF(iter);
                    return nullptr;
                }
                result = new_result;
            }
            Py_DECREF(iter);

            if (PyErr_Occurred()) {
                Py_DECREF(result);
                return nullptr;
            }
            return result;
        }
        return nullptr;
    }
}

static int traverse(Compose* self, visitproc visit, void* arg) {
    Py_VISIT(self->functions);
    return 0;
}

static int clear(Compose* self) {
    Py_CLEAR(self->functions);
    return 0;
}

static void dealloc(Compose *self) {    
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static PyObject * repr(Compose *self) {
    return PyUnicode_FromFormat(MODULE "Compose%S", self->functions);
}

static PyMemberDef members[] = {
    {"functions", T_OBJECT, offsetof(Compose, functions), READONLY, "The sequence of functions to compose."},
    {NULL}  /* Sentinel */
};

static int init(Compose *self, PyObject *args, PyObject *kwds) {
    if (PyTuple_Size(args) == 0) {
        PyErr_SetString(PyExc_TypeError, "compose takes at least one argument");
        return -1;
    }
    if (PyTuple_Size(args) == 1) {
        self->functions = Py_NewRef(PyTuple_GetItem(args, 0));
    } else {
        self->functions = Py_NewRef(args);
    }

    self->vectorcall = (vectorcallfunc)vectorcall;
    return 0;
}

// static PyObject * create(PyTypeObject *type, PyObject *args, PyObject *kwds) {

//     PyObject * functions;
    
//     static const char *kwlist[] = {"functions", NULL};

//     if (!PyArg_ParseTupleAndKeywords(args, kwds, "O", (char **)kwlist, &functions))
//     {
//         return NULL; // Return NULL on failure
//     }
    
//     Compose * self = (Compose *)type->tp_alloc(type, 0);

//     if (!self) {
//         return NULL;
//     }

//     // PyObject_GC_Track(self);

//     self->functions = Py_NewRef(functions);
//     self->vectorcall = (vectorcallfunc)vectorcall;

//     return (PyObject *)self;
// }

PyTypeObject Compose_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "composeN",
    .tp_basicsize = sizeof(Compose),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = offsetof(Compose, vectorcall),
    .tp_repr = (reprfunc)repr,
    .tp_call = PyVectorcall_Call,
    .tp_str = (reprfunc)repr,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "composeN(*functions)\n--\n\n"
               "Compose multiple functions into a single callable.\n\n"
               "Calls the first function with all arguments, then passes its result\n"
               "to the second function, and so on. Optimized for tuple and list\n"
               "containers with fallback to generic iteration.\n\n"
               "Args:\n"
               "    *functions: One or more callables, or an iterable of callables.\n\n"
               "Returns:\n"
               "    A callable that applies the composition: f_n(...(f_2(f_1(*args)))).\n\n"
               "Example:\n"
               "    >>> c = composeN(str.upper, str.strip)\n"
               "    >>> c('  hello  ')\n"
               "    'HELLO'",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    .tp_members = members,
    .tp_init = (initproc)init,
    .tp_new = PyType_GenericNew,
};
