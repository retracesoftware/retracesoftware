#include "functional.h"

struct Partial : public PyVarObject {
    vectorcallfunc vectorcall;
    // std::vector<std::pair<PyTypeObject *, PyObject *>> dispatch;
    PyObject *dict;
    retracesoftware::FastCall function;
    // PyObject * function;        
    // vectorcallfunc function_vectorcall;
    int required;
    PyObject * args[];

    static int clear(Partial* self) {
        Py_CLEAR(self->function.callable);
        for (int i = 0; i < self->ob_size; i++) {
            Py_CLEAR(self->args[i]);
        }
        return 0;
    }
    
    static int traverse(Partial* self, visitproc visit, void* arg) {
        Py_VISIT(self->function.callable);
        for (int i = 0; i < self->ob_size; i++) {
            Py_VISIT(self->args[i]);
        }
        return 0;
    }
    
    static void dealloc(Partial *self) {
        PyObject_GC_UnTrack(self);          // Untrack from the GC

        clear(self);
        Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
    }

    static PyObject * getattro(Partial *self, PyObject *name) {
        return PyObject_GetAttr(self->function.callable, name);
    }

    static PyObject * call(Partial * self, PyObject** args, size_t nargsf, PyObject* kwnames) {

        size_t nargs = PyVectorcall_NARGS(nargsf) + (kwnames ? PyTuple_GET_SIZE(kwnames) : 0);

        if (nargs == 0 || self->required == 0) {
            return self->function(self->args, self->ob_size, nullptr);
        } else {
            size_t total_args = self->ob_size + nargs;

            assert(self->required == -1);

            if (self->required == -1 || self->required == total_args) { 
                PyObject ** mem = (PyObject **)alloca(sizeof(PyObject *) * (total_args + 1)) + 1;

                for (size_t i = 0; i < (size_t)self->ob_size; i++) {
                    mem[i] = self->args[i];
                }

                for (size_t i = 0; i < nargs; i++) {
                    mem[i + self->ob_size] = args[i];
                }

                nargsf = (self->ob_size + PyVectorcall_NARGS(nargsf)) | PY_VECTORCALL_ARGUMENTS_OFFSET;

                return self->function(mem, nargsf, kwnames);
            }
        }
    }

    static PyObject* create(PyTypeObject* type, PyObject* args, PyObject* kwds) {

        if (PyTuple_Size(args) == 0) {
            PyErr_SetString(PyExc_TypeError, "partial requires at least one positional argument");
            return nullptr;
        }

        int required = -1;

        if (kwds) {
            PyObject * obj = PyDict_GetItemString(kwds, "required");

            if (obj) {
                if (!PyLong_Check(obj)) {
                    PyErr_Format(PyExc_TypeError, "required parameter: %S wasn't int", obj);
                }
                required = PyLong_AsLong(obj);

                if (required < 0) {
                    PyErr_Format(PyExc_TypeError, "required parameter: %S must be >= 0", obj);
                }
            }
        }

        // Use PyObject_NewVar to allocate memory for the object
        // Partial* self = (Partial *)Partial_Type.tp_alloc(&Partial_Type, PyTuple_Size(args) - 1);
        Partial* self = (Partial *)Partial_Type.tp_alloc(type, PyTuple_Size(args) - 1);
        
        // Check if the allocation was successful
        if (self == NULL) {
            return NULL; // Return NULL on error
        }

        self->function = retracesoftware::FastCall(Py_NewRef(PyTuple_GetItem(args, 0)));

        for (Py_ssize_t i = 0; i < self->ob_size; i++) {
            self->args[i] = Py_NewRef(PyTuple_GetItem(args, i + 1));
        }

        self->vectorcall = (vectorcallfunc)Partial::call;
        self->dict = NULL;
        self->required = required;

        return (PyObject*)self;
    }

    static PyObject* descr_get(PyObject *self, PyObject *obj, PyObject *type) {
        return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
    }
};

static PyObject * repr(Partial *self) {

    PyObject *result = PyObject_Repr(self->function.callable);

    if (!result) return nullptr;

    for (Py_ssize_t i = 0; i < Py_SIZE(self); ++i) {
        PyObject *item_repr = PyUnicode_FromFormat(", %S", self->args[i]);

        if (item_repr == NULL) {
            Py_DECREF(result);
            return NULL;
        }
        PyObject * new_result = PyUnicode_Concat(result, item_repr);
        if (!new_result) {
            Py_DECREF(result);
            Py_DECREF(item_repr);
            return NULL;
        }
        Py_DECREF(result);
        Py_DECREF(item_repr);
        result = new_result;
    }
    
    PyObject *final_repr = PyUnicode_FromFormat(MODULE "partial(%S)", result);
    Py_DECREF(result);
    return final_repr;
}

PyTypeObject Partial_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "partial",
    .tp_basicsize = sizeof(Partial),
    .tp_itemsize = sizeof(PyObject *),
    .tp_dealloc = (destructor)Partial::dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(Partial, vectorcall),
    .tp_repr = (reprfunc)repr,
    .tp_call = PyVectorcall_Call,
    .tp_str = (reprfunc)repr,
    .tp_getattro = (getattrofunc)Partial::getattro,
    .tp_flags = Py_TPFLAGS_DEFAULT | 
                Py_TPFLAGS_HAVE_GC | 
                Py_TPFLAGS_HAVE_VECTORCALL | 
                Py_TPFLAGS_METHOD_DESCRIPTOR |
                Py_TPFLAGS_BASETYPE,
    .tp_doc = "partial(func, *args, required=None)\n--\n\n"
               "Create a partial application of a function with fixed arguments.\n\n"
               "Similar to functools.partial but optimized with vectorcall and\n"
               "stack-based argument concatenation for minimal overhead.\n\n"
               "Args:\n"
               "    func: The callable to partially apply.\n"
               "    *args: Positional arguments to prepend on each call.\n"
               "    required: If set to 0, call immediately with stored args only.\n"
               "              If -1 (default), concatenate additional args on call.\n\n"
               "Returns:\n"
               "    A callable that prepends the stored args to any new arguments.\n\n"
               "Example:\n"
               "    >>> add = lambda a, b: a + b\n"
               "    >>> add5 = partial(add, 5)\n"
               "    >>> add5(3)\n"
               "    8",
    .tp_traverse = (traverseproc)Partial::traverse,
    .tp_clear = (inquiry)Partial::clear,
    .tp_descr_get = Partial::descr_get,
    .tp_dictoffset = OFFSET_OF_MEMBER(Partial, dict), // Set the offset here

    // .tp_methods = methods,
    // .tp_members = members,
    .tp_new = (newfunc)Partial::create,
    // .tp_init = (initproc)Partial::init,
    // .tp_new = PyType_GenericNew,
};

PyObject * partial(PyObject * function, PyObject * const * args, size_t nargs) {

    Partial * self = (Partial *)Partial_Type.tp_alloc(&Partial_Type, nargs);
    
    if (!self) {
        return NULL;
    }

    self->ob_size = nargs;

    for (size_t i = 0; i < nargs; i++) {
        self->args[i] = Py_NewRef(args[i]);
    }
    self->vectorcall = (vectorcallfunc)Partial::call;
    self->function = Py_NewRef(function);

    return (PyObject *)self;
}

