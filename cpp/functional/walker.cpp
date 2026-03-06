#include "functional.h"
#include <structmember.h>

struct Walker : public PyObject {
    PyObject * func;
    vectorcallfunc func_vectorcall;
    vectorcallfunc vectorcall;
};

static PyObject * walk(Walker * self, PyObject * arg);

static PyObject * walk_tuple_from(Walker * self, PyObject * tuple, size_t from, PyObject * new_element) {
    size_t n = PyTuple_GET_SIZE(tuple);

    PyObject * new_tuple = PyTuple_New(n);
    if (!new_tuple) return nullptr;

    for (size_t j = 0; j < from; ++j) {
        PyTuple_SET_ITEM(new_tuple, j, Py_NewRef(PyTuple_GET_ITEM(tuple, j)));
    }
    PyTuple_SET_ITEM(new_tuple, from, new_element);
    
    for (size_t i = from + 1; i < n; ++i) {
        PyObject * elem = PyTuple_GET_ITEM(tuple, i);
        PyObject * new_elem = walk(self, elem);

        if (!new_elem) {
            Py_DECREF(new_tuple);
            return nullptr;
        }
        PyTuple_SET_ITEM(new_tuple, i, new_elem);
    }
    return new_tuple;
}

static PyObject * walk_tuple(Walker * self, PyObject * tuple) {
    
    size_t n = PyTuple_GET_SIZE(tuple);

    for (size_t i = 0; i < n; ++i) {
        PyObject * elem = PyTuple_GET_ITEM(tuple, i);
        PyObject * new_elem = walk(self, elem);
        
        if (elem == new_elem) {
            Py_DECREF(new_elem);
        } else if (!new_elem) {
            return nullptr;
        } else {
            return walk_tuple_from(self, tuple, i, new_elem);
        }
    }
    return Py_NewRef(tuple);
}

static PyObject * walk_list_from(Walker * self, PyObject * list, size_t from, PyObject * new_element) {
    size_t n = PyList_GET_SIZE(list);

    PyObject * new_list = PyList_New(n);
    if (!new_list) return nullptr;

    for (size_t j = 0; j < from; ++j) {
        PyList_SET_ITEM(new_list, j, Py_NewRef(PyList_GET_ITEM(list, j)));
    }
    PyList_SET_ITEM(new_list, from, new_element);
    
    for (size_t i = from + 1; i < n; ++i) {
        PyObject * elem = PyList_GET_ITEM(list, i);
        PyObject * new_elem = walk(self, elem);

        if (!new_elem) {
            Py_DECREF(new_list);
            return nullptr;
        }
        PyList_SET_ITEM(new_list, i, new_elem);
    }
    return new_list;
}

static PyObject * walk_list(Walker * self, PyObject * list) {
    size_t n = PyList_GET_SIZE(list);

    for (size_t i = 0; i < n; ++i) {
        PyObject * elem = PyList_GET_ITEM(list, i);
        PyObject * new_elem = walk(self, elem);
        
        if (elem == new_elem) {
            Py_DECREF(new_elem);
        } else if (!new_elem) {
            return nullptr;
        } else {
            return walk_list_from(self, list, i, new_elem);
        }
    }
    return Py_NewRef(list); 
}

static PyObject * walk_dict(Walker * self, PyObject * dict) {
    Py_ssize_t pos = 0;
    PyObject *key, *value;

    while (PyDict_Next(dict, &pos, &key, &value)) {
        PyObject * new_value = walk(self, value);

        if (new_value == value) {
            Py_DECREF(value);
        } else if (!new_value) {
            return nullptr;
        } else {
            PyObject * new_dict = PyDict_Copy(dict);

            PyDict_SetItem(new_dict, key, new_value);
            Py_DECREF(new_value);

            while (PyDict_Next(dict, &pos, &key, &value)) {
                new_value = walk(self, value);
                if (!new_value) {
                    Py_DECREF(new_dict);
                    return nullptr;
                }
                PyDict_SetItem(new_dict, key, new_value);
                Py_DECREF(new_value);
            }
            return new_dict;
        }
    }
    return Py_NewRef(dict);
}

static PyObject * walk(Walker * self, PyObject * arg) {

    assert (!PyErr_Occurred());

    if (arg == Py_None) return Py_NewRef(Py_None);

    PyTypeObject * cls = Py_TYPE(arg);

    if (cls == &PyTuple_Type) {
        return walk_tuple(self, arg);
    } else if (cls == &PyList_Type) {
        return walk_list(self, arg);
    } else if (cls == &PyDict_Type) {
        return walk_dict(self, arg);
    } else {
        PyObject * res = self->func_vectorcall(self->func, &arg, 1, nullptr);
        assert ((res && !PyErr_Occurred()) || (!res && PyErr_Occurred()));
        return res;
        // return self->func_vectorcall(self->func, &arg, 1, nullptr);
    }
}

static PyObject * call(Walker * self, PyObject* const * args, size_t nargsf, PyObject* kwnames) {

    assert (!PyErr_Occurred());

    int nargs = PyVectorcall_NARGS(nargsf);

    if (nargs != 1 || kwnames) {
        if (nargs != 1) {
            raise(SIGTRAP);
            PyErr_Format(PyExc_TypeError, "%S currently only takes single positional parameter, %i passed", Py_TYPE(self), nargs);
            return nullptr;
        } else {
            PyErr_Format(PyExc_TypeError, "%S currently only takes single positional parameter, keywords: %S passed", Py_TYPE(self), kwnames);
            return nullptr;
        }
    }
    return walk(self, args[0]);
}

static int traverse(Walker* self, visitproc visit, void* arg) {
    Py_VISIT(self->func);

    return 0;
}

static int clear(Walker* self) {
    Py_CLEAR(self->func);
    return 0;
}

static void dealloc(Walker *self) {
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static int init(Walker *self, PyObject *args, PyObject *kwds) {

    PyObject * function = NULL;

    static const char *kwlist[] = { "function", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O", (char **)kwlist, &function))
    {
        return -1; // Return NULL on failure
    }

    CHECK_CALLABLE(function);
    
    self->func = Py_XNewRef(function);
    self->func_vectorcall = extract_vectorcall(function);
    self->vectorcall = (vectorcallfunc)call;

    return 0;
}

static PyMemberDef members[] = {
    // {"on_call", T_OBJECT, OFFSET_OF_MEMBER(Observer, on_call), READONLY, "TODO"},
    // {"on_result", T_OBJECT, OFFSET_OF_MEMBER(Observer, on_result), READONLY, "TODO"},
    // {"on_error", T_OBJECT, OFFSET_OF_MEMBER(Observer, on_error), READONLY, "TODO"},
    // {"function", T_OBJECT, OFFSET_OF_MEMBER(Observer, func), READONLY, "TODO"},
    {NULL}  /* Sentinel */
};

PyTypeObject Walker_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "walker",
    .tp_basicsize = sizeof(Walker),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(Walker, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "walker(function)\n--\n\n"
               "Recursively walk and transform nested data structures.\n\n"
               "Traverses tuples, lists, and dicts, applying function to\n"
               "leaf values (non-container types). Preserves structure and\n"
               "uses copy-on-write for efficiency.\n\n"
               "Args:\n"
               "    function: Transform to apply to leaf values.\n\n"
               "Returns:\n"
               "    A callable that walks and transforms nested structures.\n\n"
               "Example:\n"
               "    >>> double = walker(lambda x: x * 2 if isinstance(x, int) else x)\n"
               "    >>> double({'a': [1, 2], 'b': 3})  # {'a': [2, 4], 'b': 6}",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    .tp_members = members,
    .tp_init = (initproc)init,
    .tp_new = PyType_GenericNew,
};
