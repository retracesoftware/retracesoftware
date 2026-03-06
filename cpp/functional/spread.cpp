#include "functional.h"

struct Spread : public PyVarObject {
    vectorcallfunc vectorcall;
    // std::vector<std::pair<PyTypeObject *, PyObject *>> dispatch;
    retracesoftware::FastCall function;
    retracesoftware::FastCall transforms[];

    static int clear(Spread* self) {
        Py_CLEAR(self->function.callable);
        for (int i = 0; i < self->ob_size; i++) {
            Py_CLEAR(self->transforms[i].callable);
        }
        return 0;
    }
    
    static int traverse(Spread* self, visitproc visit, void* arg) {
        Py_VISIT(self->function.callable);
        for (int i = 0; i < self->ob_size; i++) {
            Py_VISIT(self->transforms[i].callable);
        }
        return 0;
    }
    
    static void dealloc(Spread *self) {

        PyObject_GC_UnTrack(self);          // Untrack from the GC

        clear(self);
        Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
    }

    PyObject * spread(PyObject * arg) {
        PyObject ** mem = (PyObject **)alloca(sizeof(PyObject *) * (ob_size + 1)) + 1;

        for (int i = 0; i < ob_size; i++) {
            if (transforms[i].callable) {
                mem[i] = transforms[i](arg);

                if (!mem[i]) {
                    for (int j = 0; j < i; j++) {
                        Py_DECREF(mem[j]);
                    }
                    return nullptr;
                }
            } else {
                mem[i] = Py_NewRef(arg);
            }
        }

        size_t nargsf = ob_size | PY_VECTORCALL_ARGUMENTS_OFFSET;

        PyObject * result = function(mem, nargsf, nullptr);

        for (int i = 0; i < ob_size; i++) {
            Py_DECREF(mem[i]);
        }
        return result;
    }

    static PyObject * call(Spread * self, PyObject* const* args, size_t nargsf, PyObject* kwnames) {

        if (kwnames) {
            PyErr_Format(PyExc_TypeError, "%S does not currently support keyword arguments", Py_TYPE(self));
            return nullptr;
        }

        Py_ssize_t nargs = PyVectorcall_NARGS(nargsf);

        if (nargs != 1) {
            PyErr_Format(PyExc_TypeError, "Spread take exactly one argument, was passed: %i", nargs);
            return nullptr;
        }

        return self->spread(args[0]);
    }

    static PyObject* create(PyTypeObject* type, PyObject* args, PyObject* kwds) {
        if (PyTuple_Size(args) == 0) {
            PyErr_SetString(PyExc_TypeError, "spread requires at least one positional argument");
            return nullptr;
        }

        // Use PyObject_NewVar to allocate memory for the object
        // Partial* self = (Partial *)Partial_Type.tp_alloc(&Partial_Type, PyTuple_Size(args) - 1);
        Spread* self = (Spread *)type->tp_alloc(type, PyTuple_Size(args) - 1);

        // Check if the allocation was successful
        if (self == NULL) {
            return NULL; // Return NULL on error
        }
        
        self->function = retracesoftware::FastCall(PyTuple_GetItem(args, 0));
        Py_INCREF(self->function.callable);

        for (Py_ssize_t i = 0; i < self->ob_size; i++) {
            PyObject * transform = PyTuple_GetItem(args, i + 1);
            
            if (transform == Py_None) {
                self->transforms[i] = retracesoftware::FastCall();
            } else {
                self->transforms[i] = retracesoftware::FastCall(transform);
                Py_INCREF(transform);
            }
        }
        self->vectorcall = (vectorcallfunc)call;

        return (PyObject*)self;
    }

    static PyObject* descr_get(PyObject *self, PyObject *obj, PyObject *type) {
        return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
    }
};

PyTypeObject Spread_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "spread",
    .tp_basicsize = sizeof(Spread),
    .tp_itemsize = sizeof(retracesoftware::FastCall),
    .tp_dealloc = (destructor)Spread::dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(Spread, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT | 
                Py_TPFLAGS_HAVE_GC | 
                Py_TPFLAGS_HAVE_VECTORCALL | 
                Py_TPFLAGS_METHOD_DESCRIPTOR |
                Py_TPFLAGS_BASETYPE,
    .tp_doc = "spread(function, *transforms)\n--\n\n"
               "Apply transforms to a single arg, then spread results to function.\n\n"
               "Takes one argument, applies each transform to it, then calls\n"
               "function with the transformed values as separate arguments.\n"
               "Use None in transforms to pass the original value unchanged.\n\n"
               "Args:\n"
               "    function: Callable to receive the spread arguments.\n"
               "    *transforms: Callables to apply (or None for identity).\n\n"
               "Returns:\n"
               "    A callable: spread(f, t1, t2)(x) == f(t1(x), t2(x))\n\n"
               "Example:\n"
               "    >>> minmax = spread(lambda a,b: (a,b), min, max)\n"
               "    >>> minmax([3, 1, 2])  # returns (1, 3)",
    .tp_traverse = (traverseproc)Spread::traverse,
    .tp_clear = (inquiry)Spread::clear,
    .tp_descr_get = Spread::descr_get,

    // .tp_methods = methods,
    // .tp_members = members,
    .tp_new = (newfunc)Spread::create,
    // .tp_init = (initproc)Partial::init,
    // .tp_new = PyType_GenericNew,
};
