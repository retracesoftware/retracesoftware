#include "functional.h"

struct Lazy : public PyVarObject {
    vectorcallfunc vectorcall;
    // std::vector<std::pair<PyTypeObject *, PyObject *>> dispatch;
    PyObject *dict;
    retracesoftware::FastCall function;
    // PyObject * function;        
    // vectorcallfunc function_vectorcall;
    PyObject * args[];

    static int clear(Lazy* self) {
        Py_CLEAR(self->function.callable);
        for (int i = 0; i < self->ob_size; i++) {
            Py_CLEAR(self->args[i]);
        }
        return 0;
    }
    
    static int traverse(Lazy* self, visitproc visit, void* arg) {
        Py_VISIT(self->function.callable);
        for (int i = 0; i < self->ob_size; i++) {
            Py_VISIT(self->args[i]);
        }
        return 0;
    }
    
    static void dealloc(Lazy *self) {

        PyObject_GC_UnTrack(self);          // Untrack from the GC

        clear(self);
        Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
    }

    static PyObject * getattro(Lazy *self, PyObject *name) {
        return PyObject_GetAttr(self->function.callable, name);
    }

    static PyObject * call(Lazy * self, PyObject** args, size_t nargsf, PyObject* kwnames) {
        return self->function(self->args, self->ob_size, nullptr);
    }

    static PyObject* create(PyTypeObject* type, PyObject* args, PyObject* kwds) {
        if (PyTuple_Size(args) == 0) {
            PyErr_SetString(PyExc_TypeError, "lazy requires at least one positional argument");
            return nullptr;
        }

        Lazy* self = (Lazy *)Lazy_Type.tp_alloc(type, PyTuple_Size(args) - 1);
        
        // Check if the allocation was successful
        if (self == NULL) {
            return NULL; // Return NULL on error
        }

        self->function = retracesoftware::FastCall(Py_NewRef(PyTuple_GetItem(args, 0)));

        for (Py_ssize_t i = 0; i < self->ob_size; i++) {
            self->args[i] = Py_NewRef(PyTuple_GetItem(args, i + 1));
        }

        self->vectorcall = (vectorcallfunc)Lazy::call;
        self->dict = NULL;

        return (PyObject*)self;
    }

    static PyObject* descr_get(PyObject *self, PyObject *obj, PyObject *type) {
        return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
    }
};

static PyObject * repr(Lazy *self) {

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
    
    PyObject *final_repr = PyUnicode_FromFormat(MODULE "lazy(%S)", result);
    Py_DECREF(result);
    return final_repr;
}

PyTypeObject Lazy_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "lazy",
    .tp_basicsize = sizeof(Lazy),
    .tp_itemsize = sizeof(PyObject *),
    .tp_dealloc = (destructor)Lazy::dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(Lazy, vectorcall),
    .tp_repr = (reprfunc)repr,
    .tp_call = PyVectorcall_Call,
    .tp_str = (reprfunc)repr,
    .tp_getattro = (getattrofunc)Lazy::getattro,
    .tp_flags = Py_TPFLAGS_DEFAULT | 
                Py_TPFLAGS_HAVE_GC | 
                Py_TPFLAGS_HAVE_VECTORCALL | 
                Py_TPFLAGS_METHOD_DESCRIPTOR |
                Py_TPFLAGS_BASETYPE,
    .tp_doc = "lazy(func, *args)\n--\n\n"
               "Defer a function call until invoked.\n\n"
               "Creates a callable that, when called (with any arguments, ignored),\n"
               "invokes func(*args). Similar to partial with required=0.\n\n"
               "Args:\n"
               "    func: The callable to defer.\n"
               "    *args: Arguments to pass when invoked.\n\n"
               "Returns:\n"
               "    A callable that executes func(*args) when called.\n\n"
               "Example:\n"
               "    >>> deferred = lazy(expensive_compute, data)\n"
               "    >>> result = deferred()  # computation happens here",
    .tp_traverse = (traverseproc)Lazy::traverse,
    .tp_clear = (inquiry)Lazy::clear,
    .tp_descr_get = Lazy::descr_get,
    .tp_dictoffset = OFFSET_OF_MEMBER(Lazy, dict), // Set the offset here

    // .tp_methods = methods,
    // .tp_members = members,
    .tp_new = (newfunc)Lazy::create,
    // .tp_new = PyType_GenericNew,
};

PyObject * lazy(PyObject * function, PyObject * const * args, size_t nargs) {

    Lazy * self = (Lazy *)Lazy_Type.tp_alloc(&Lazy_Type, nargs);
    
    if (!self) {
        return NULL;
    }

    self->ob_size = nargs;

    for (size_t i = 0; i < nargs; i++) {
        self->args[i] = Py_NewRef(args[i]);
    }
    self->vectorcall = (vectorcallfunc)Lazy::call;
    self->function = Py_NewRef(function);

    return (PyObject *)self;
}

