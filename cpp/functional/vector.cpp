#include "functional.h"

struct Vector : public PyVarObject {
    vectorcallfunc vectorcall;
    // std::vector<std::pair<PyTypeObject *, PyObject *>> dispatch;
    PyObject *dict;
    // PyObject * function;        
    // vectorcallfunc function_vectorcall;
    PyObject * funcs[];

    static int clear(Vector* self) {
        for (int i = 0; i < self->ob_size; i++) {
            Py_CLEAR(self->funcs[i]);
        }
        return 0;
    }
    
    static int traverse(Vector* self, visitproc visit, void* arg) {
        for (int i = 0; i < self->ob_size; i++) {
            Py_VISIT(self->funcs[i]);
        }
        return 0;
    }
    
    static void dealloc(Vector *self) {
        PyObject_GC_UnTrack(self);          // Untrack from the GC
        clear(self);
        Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
    }

    static PyObject * call(Vector * self, PyObject*const * args, size_t nargsf, PyObject* kwnames) {

        PyObject * res = PyTuple_New(self->ob_size);

        if (!res) return nullptr;

        for (size_t i = 0; i < self->ob_size; i++) {

            PyObject * item = PyObject_Vectorcall(self->funcs[i], args, nargsf, kwnames);

            if (!item) {
                Py_DECREF(res);
                return nullptr;
            }
            PyTuple_SET_ITEM(res, i, item);
        }
        return res;
    }

    static PyObject* create(PyTypeObject* type, PyObject* args, PyObject* kwds) {

        if (PyTuple_Size(args) == 0) {
            PyErr_SetString(PyExc_TypeError, "Vector requires at least one positional argument");
            return nullptr;
        }

        // Use PyObject_NewVar to allocate memory for the object
        // Vector* self = (Vector *)Vector_Type.tp_alloc(&Vector_Type, PyTuple_Size(funcs) - 1);
        Vector* self = (Vector *)Vector_Type.tp_alloc(type, PyTuple_Size(args));
        
        // Check if the allocation was successful
        if (self == NULL) {
            return NULL; // Return NULL on error
        }

        for (Py_ssize_t i = 0; i < self->ob_size; i++) {
            self->funcs[i] = Py_NewRef(PyTuple_GetItem(args, i));
        }

        self->vectorcall = (vectorcallfunc)Vector::call;
        self->dict = NULL;

        return (PyObject*)self;
    }

    static PyObject* descr_get(PyObject *self, PyObject *obj, PyObject *type) {
        return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
    }
};

static PyObject * repr(Vector *self) {

    PyObject *result = PyObject_Repr(self->funcs[0]);

    if (!result) return nullptr;

    for (Py_ssize_t i = 1; i < Py_SIZE(self); ++i) {
        PyObject *item_repr = PyUnicode_FromFormat(", %S", self->funcs[i]);

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
    
    PyObject *final_repr = PyUnicode_FromFormat(MODULE "Vector(%S)", result);
    Py_DECREF(result);
    return final_repr;
}

PyTypeObject Vector_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "juxt",
    .tp_basicsize = sizeof(Vector),
    .tp_itemsize = sizeof(PyObject *),
    .tp_dealloc = (destructor)Vector::dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(Vector, vectorcall),
    .tp_repr = (reprfunc)repr,
    .tp_call = PyVectorcall_Call,
    .tp_str = (reprfunc)repr,
    .tp_flags = Py_TPFLAGS_DEFAULT | 
                Py_TPFLAGS_HAVE_GC | 
                Py_TPFLAGS_HAVE_VECTORCALL | 
                Py_TPFLAGS_METHOD_DESCRIPTOR |
                Py_TPFLAGS_BASETYPE,
    .tp_doc = "juxt(*functions)\n--\n\n"
               "Juxtapose functions: call each with the same args, return tuple of results.\n\n"
               "Inspired by Clojure's juxt. Useful for computing multiple values\n"
               "from the same input in parallel.\n\n"
               "Args:\n"
               "    *functions: Callables to apply to the arguments.\n\n"
               "Returns:\n"
               "    A callable: juxt(f, g, h)(x) == (f(x), g(x), h(x))\n\n"
               "Example:\n"
               "    >>> stats = juxt(min, max, sum)\n"
               "    >>> stats([1, 2, 3])  # (1, 3, 6)",
    .tp_traverse = (traverseproc)Vector::traverse,
    .tp_clear = (inquiry)Vector::clear,
    .tp_descr_get = Vector::descr_get,
    .tp_dictoffset = OFFSET_OF_MEMBER(Vector, dict), // Set the offset here

    // .tp_methods = methods,
    // .tp_members = members,
    .tp_new = (newfunc)Vector::create,
    // .tp_init = (initproc)Vector::init,
    // .tp_new = PyType_GenericNew,
};

// PyObject * Vector(PyObject * function, PyObject * const * funcs, size_t nfuncs) {

//     Vector * self = (Vector *)Vector_Type.tp_alloc(&Vector_Type, nfuncs);
    
//     if (!self) {
//         return NULL;
//     }

//     self->ob_size = nfuncs;

//     for (size_t i = 0; i < nfuncs; i++) {
//         self->funcs[i] = Py_NewRef(funcs[i]);
//     }
//     self->vectorcall = (vectorcallfunc)Vector::call;
//     self->function = Py_NewRef(function);

//     return (PyObject *)self;
// }

