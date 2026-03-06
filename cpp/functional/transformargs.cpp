#include "functional.h"
#include <structmember.h>
#include <signal.h>


struct TransformArgs : public PyObject {
    int from;
    // PyObject * func;
    retracesoftware::FastCall func;
    retracesoftware::FastCall transform;
    vectorcallfunc vectorcall;
};


static PyObject * vectorcall_from_alloca(int from, TransformArgs * self, PyObject* const * args, size_t nargsf, PyObject* kwnames) {
    size_t nargs = PyVectorcall_NARGS(nargsf);

    assert (nargs >= from);
    int all = nargs + (kwnames ? PyTuple_Size(kwnames) : 0);
    
    // PyObject * vla[all + 1];
    PyObject ** mem = (PyObject **)alloca(sizeof(PyObject *) * (all + 1)) + 1;

    for (int i = 0; i < from; i++) {
        mem[i] = args[i];
    }
    for (size_t i = from; i < all; i++) {
        mem[i] = self->transform(args + i, 1, nullptr);
        
        // if (mem[i] && (Py_REFCNT(mem[i]) < 0 || Py_REFCNT(mem[i]) > 10000000000)) {
        //     raise(SIGTRAP);
        //     mem[i] = self->transform_vectorcall(self->transform, args + i, 1, nullptr);
        // }

        if (!mem[i]) {
            for (size_t j = from; j < i; j++) Py_DECREF(mem[j]);
            return nullptr;
        }
    }
    PyObject * result = self->func(mem, nargs | PY_VECTORCALL_ARGUMENTS_OFFSET, kwnames);

    for (int i = from; i < all; i++) Py_XDECREF(mem[i]);

    return result;
}

static inline PyObject * vectorcall_from(int from, TransformArgs * self, PyObject* const * args, size_t nargsf, PyObject* kwnames) {

    if (!kwnames) {
        size_t nargs = PyVectorcall_NARGS(nargsf);

        assert (nargs >= from);

        if (nargs == 0 || nargs == from) {
            return self->func(args, nargsf, nullptr);
        } else if (nargs == 1) {
            PyObject * transformed = self->transform(args[0]);
            if (!transformed) return nullptr;
            PyObject * result = self->func(transformed);
            Py_DECREF(transformed);
            return result;
        }
    }
    return vectorcall_from_alloca(from, self, args, nargsf, kwnames);
}

static PyObject * vectorcall0(TransformArgs * self, PyObject* const * args, size_t nargsf, PyObject* kwnames) {
    return vectorcall_from(0, self, args, nargsf, kwnames);
}

static PyObject * vectorcall1(TransformArgs * self, PyObject* const * args, size_t nargsf, PyObject* kwnames) {
    assert(PyVectorcall_NARGS(nargsf) > 0);
    
    return vectorcall_from(1, self, args, nargsf, kwnames);
}

static PyObject * vectorcallN(TransformArgs * self, PyObject* const * args, size_t nargsf, PyObject* kwnames) {
    return vectorcall_from(self->from, self, args, nargsf, kwnames);
}

// static PyObject * vectorcall(TransformArgs * self, PyObject* const * args, size_t nargsf, PyObject* kwnames) {

//     size_t nargs = PyVectorcall_NARGS(nargsf);
//     int all = nargs + (kwnames ? PyTuple_Size(kwnames) : 0);

//     PyObject * vla[all + 1];

//     PyObject * on_stack[SMALL_ARGS + 1];
//     PyObject ** buffer;
//     PyObject * result = nullptr;

//     if (all >= SMALL_ARGS) {
//         buffer = (PyObject **)PyMem_Malloc(sizeof(PyObject *) * (all + 1));
//         if (!buffer) {
//             return nullptr;
//         }
//     } else {
//         buffer = on_stack;
//     }

//     int from = std::min(self->from, (int)nargs);

//     for (int i = 0; i < from; i++) {
//         buffer[i + 1] = Py_NewRef(args[i]);
//     }

//     for (int i = from; i < all; i++) {
//         buffer[i + 1] = PyObject_CallOneArg(self->transform, args[i]);
//         if (!buffer[i + 1]) {
//             for (int j = i; j < all; j++) {
//                 buffer[j + 1] = nullptr;
//             }
//             goto error;
//         }
//     }    
//     result = PyObject_Vectorcall(self->func, 
//                                  buffer + 1, 
//                                  nargs | PY_VECTORCALL_ARGUMENTS_OFFSET,
//                                  kwnames);
// error:
//     for (int i = 0; i < all; i++) {
//         Py_XDECREF(buffer[i + 1]);
//     }
    
//     if (buffer != on_stack) {
//         PyMem_Free(buffer);
//     }

//     return result;
// }

static int traverse(TransformArgs* self, visitproc visit, void* arg) {
    Py_VISIT(self->transform.callable);
    Py_VISIT(self->func.callable);
    return 0;
}

static int clear(TransformArgs* self) {
    Py_CLEAR(self->transform.callable);
    Py_CLEAR(self->func.callable);
    return 0;
}

static void dealloc(TransformArgs *self) {
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static PyMemberDef members[] = {
    {nullptr}  /* Sentinel */
};

static vectorcallfunc select_vectorallfunc(int from) {
    switch (from) {
        case 0:  return (vectorcallfunc)vectorcall0;
        case 1:  return (vectorcallfunc)vectorcall1;
        default: return (vectorcallfunc)vectorcallN;
    }
}

static PyObject * getattro(TransformArgs *self, PyObject *name) {
    return PyObject_GetAttr(self->func.callable, name);
}

static int setattro(TransformArgs *self, PyObject *name, PyObject * value) {
    PyObject * transformed = self->transform(value);
    if (!transformed) return -1;
    int res = PyObject_SetAttr(self->func.callable, name, transformed);
    Py_DECREF(transformed);
    return res;
}

static int init(TransformArgs * self, PyObject *args, PyObject *kwds) {

    PyObject * function;
    PyObject * transform;
    int from = 0;
    
    static const char *kwlist[] = {"function", "transform", "starting", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OO|i", (char **)kwlist, &function, &transform, &from)) {
        return -1; // Return NULL on failure
    }
    
    self->transform = retracesoftware::FastCall(transform);
    self->func = retracesoftware::FastCall(function);

    Py_INCREF(function);
    Py_INCREF(transform);

    self->from = from;
    self->vectorcall = select_vectorallfunc(from);

    return 0;
}

static PyObject* descr_get(PyObject *self, PyObject *obj, PyObject *type) {
    return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
}

PyTypeObject TransformArgs_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "mapargs",
    .tp_basicsize = sizeof(TransformArgs),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(TransformArgs, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_getattro = (getattrofunc)getattro,
    .tp_setattro = (setattrofunc)setattro,
    .tp_flags = Py_TPFLAGS_DEFAULT |
                Py_TPFLAGS_HAVE_GC | 
                Py_TPFLAGS_HAVE_VECTORCALL | 
                Py_TPFLAGS_METHOD_DESCRIPTOR,
    .tp_doc = "mapargs(function, transform, starting=0)\n--\n\n"
               "Transform arguments before passing them to function.\n\n"
               "Applies 'transform' to each argument starting from index 'starting',\n"
               "then calls function with the transformed arguments.\n"
               "Also transforms values when setting attributes.\n\n"
               "Args:\n"
               "    function: The target callable.\n"
               "    transform: Applied to each argument (and kwarg value).\n"
               "    starting: Index from which to start transforming (default 0).\n\n"
               "Returns:\n"
               "    A callable that transforms args before calling function.",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    .tp_members = members,
    .tp_descr_get = descr_get,
    .tp_init = (initproc)init,
    .tp_new = PyType_GenericNew,
};
