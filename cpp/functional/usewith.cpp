#include "functional.h"
#include "object.h"
#include <new>

struct UseWith : public PyVarObject {
    vectorcallfunc vectorcall;
    retracesoftware::FastCall target;
    // std::vector<std::pair<PyTypeObject *, PyObject *>> dispatch;
    PyObject *dict;
    // PyObject * function;        
    // vectorcallfunc function_vectorcall;
    retracesoftware::FastCall funcs[];

    static int clear(UseWith* self) {
        Py_CLEAR(self->target.callable);
        for (int i = 0; i < self->ob_size; i++) {
            Py_CLEAR(self->funcs[i].callable);
        }
        return 0;
    }
    
    static int traverse(UseWith* self, visitproc visit, void* arg) {
        Py_VISIT(self->target.callable);
        for (int i = 0; i < self->ob_size; i++) {
            Py_VISIT(self->funcs[i].callable);
        }
        return 0;
    }
    
    static void dealloc(UseWith *self) {
        PyObject_GC_UnTrack(self);          // Untrack from the GC
        clear(self);
        Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
    }
    
    PyObject * call_using(size_t count, PyObject ** buffer, PyObject*const * args, size_t nargsf, PyObject* kwnames) {
        for (size_t i = 0; i < count; i++) {
            buffer[i] = funcs[i](args, nargsf, kwnames);
            if (!buffer[i]) {
                for (size_t j = 0; j < i; j++) Py_DECREF(buffer[j]);
                return nullptr;
            }
        }

        PyObject * result = target(buffer, count | PY_VECTORCALL_ARGUMENTS_OFFSET, nullptr);

        for (size_t i = 0; i < count; i++) { 
            Py_DECREF(buffer[i]);
        }
        return result;
    }

    static PyObject * call1(UseWith * self, PyObject*const * args, size_t nargsf, PyObject* kwnames) {
        PyObject * transformed[2];
        return self->call_using(1, transformed + 1, args, nargsf, kwnames);
    }

    static PyObject * call2(UseWith * self, PyObject*const * args, size_t nargsf, PyObject* kwnames) {
        PyObject * transformed[3];
        return self->call_using(2, transformed + 1, args, nargsf, kwnames);
    }

    static PyObject * call3(UseWith * self, PyObject*const * args, size_t nargsf, PyObject* kwnames) {
        PyObject * transformed[4];
        return self->call_using(3, transformed + 1, args, nargsf, kwnames);
    }

    static PyObject * callN(UseWith * self, PyObject*const * args, size_t nargsf, PyObject* kwnames) {
        PyObject ** buffer = (PyObject **)malloc((sizeof(PyObject *) + 1) * self->ob_size);
        PyObject * result = self->call_using(self->ob_size, buffer + 1, args, nargsf, kwnames);
        free(buffer);
        return result;
    }

    static vectorcallfunc get_vectorcallfunc(size_t nargs) {
        switch (nargs) {
            case 1: return (vectorcallfunc)call1;
            case 2: return (vectorcallfunc)call2;
            case 3: return (vectorcallfunc)call3;
            default: return (vectorcallfunc)callN;
        }
    }

    static PyObject* create(PyTypeObject* type, PyObject* args, PyObject* kwds) {

        if (PyTuple_Size(args) < 2) {
            PyErr_SetString(PyExc_TypeError, "use_with requires at least two positional arguments");
            return nullptr;
        }

        size_t nargs = PyTuple_Size(args) - 1;

        // Use PyObject_NewVar to allocate memory for the object
        // Vector* self = (Vector *)Vector_Type.tp_alloc(&Vector_Type, PyTuple_Size(funcs) - 1);
        UseWith * self = (UseWith *)Vector_Type.tp_alloc(type, nargs);

        // Check if the allocation was successful
        if (self == NULL) {
            return NULL; // Return NULL on error
        }

        self->vectorcall = get_vectorcallfunc(nargs);
        
        // new (&self->bindings) map<PyObject *, int>();
        new (&self->target) retracesoftware::FastCall(Py_NewRef(PyTuple_GetItem(args, 0)));

        for (Py_ssize_t i = 0; i < self->ob_size; i++) {
            new (self->funcs + i) retracesoftware::FastCall(Py_NewRef(PyTuple_GetItem(args, i + 1)));
        }
        self->dict = NULL;

        return (PyObject*)self;
    }

    static PyObject* descr_get(PyObject *self, PyObject *obj, PyObject *type) {
        return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
    }
};

static PyObject * repr(UseWith *self) {

    PyObject *result = PyObject_Repr(self->target.callable);

    if (!result) return nullptr;

    for (Py_ssize_t i = 0; i < Py_SIZE(self); ++i) {
        PyObject *item_repr = PyUnicode_FromFormat(", %S", self->funcs[i].callable);

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
    
    PyObject *final_repr = PyUnicode_FromFormat(MODULE "use_with(%S)", result);
    Py_DECREF(result);
    return final_repr;
}

PyTypeObject UseWith_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "use_with",
    .tp_basicsize = sizeof(UseWith),
    .tp_itemsize = sizeof(retracesoftware::FastCall),
    .tp_dealloc = (destructor)UseWith::dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(UseWith, vectorcall),
    .tp_repr = (reprfunc)repr,
    .tp_call = PyVectorcall_Call,
    .tp_str = (reprfunc)repr,
    .tp_flags = Py_TPFLAGS_DEFAULT | 
                Py_TPFLAGS_HAVE_GC | 
                Py_TPFLAGS_HAVE_VECTORCALL | 
                Py_TPFLAGS_METHOD_DESCRIPTOR |
                Py_TPFLAGS_BASETYPE,
    .tp_doc = "use_with(target, *transforms)\n--\n\n"
               "Apply transforms to args, then pass transformed args to target.\n\n"
               "Each transform is called with all original args; the results\n"
               "become the arguments to target.\n\n"
               "Args:\n"
               "    target: The function to call with transformed arguments.\n"
               "    *transforms: Functions to compute each argument for target.\n\n"
               "Returns:\n"
               "    A callable: use_with(f, t1, t2)(x) == f(t1(x), t2(x))\n\n"
               "Example:\n"
               "    >>> add_len_and_sum = use_with(lambda a,b: a+b, len, sum)\n"
               "    >>> add_len_and_sum([1, 2, 3])  # 3 + 6 = 9",
    .tp_traverse = (traverseproc)UseWith::traverse,
    .tp_clear = (inquiry)UseWith::clear,
    .tp_descr_get = UseWith::descr_get,
    .tp_dictoffset = OFFSET_OF_MEMBER(UseWith, dict), // Set the offset here

    // .tp_methods = methods,
    // .tp_members = members,
    .tp_new = (newfunc)UseWith::create,
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

