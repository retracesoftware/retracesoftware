#include "utils.h"
#include <exception>
#include <structmember.h>

namespace retracesoftware {
    
    static int tp_traverse(Wrapped* self, visitproc visit, void* arg) {
        Py_VISIT(self->target);
        return 0;
    }
    
    static int tp_clear(Wrapped * self) {
        Py_CLEAR(self->target);
        return 0;
    }

    static void dealloc(Wrapped *self) {
            
        PyObject_GC_UnTrack(self);          // Untrack from the GC
        if (self->weakreflist) {
            PyObject_ClearWeakRefs(self);
        }
        Py_TYPE(self)->tp_clear(self);
        Py_TYPE(self)->tp_free(self);  // Free the object
    }

    //     static PyObject * create(PyTypeObject *type, PyObject *args, PyObject *kwds) {

    //     if (PyTuple_GET_SIZE(args) == 0) {
    //         PyErr_Format(PyExc_TypeError, "%S requires a positional argument to wrap", &Wrapped_Type);
    //         return nullptr;
    //     }
    //     Wrapped * self = reinterpret_cast<Wrapped *>(type->tp_alloc(type, 0));

    //     if (!self) return nullptr;

    //     self->target = Py_NewRef(PyTuple_GET_ITEM(args, 0));

    //     return (PyObject *)self;
    // }

    static PyObject * create(PyTypeObject* cls, PyObject *args, PyObject *kwds) {

        Wrapped * self = (Wrapped *)cls->tp_alloc(cls, 0);
        if (!self) return nullptr;

        if (PyTuple_GET_SIZE(args) == 0) {
            PyErr_Format(PyExc_TypeError, "%S requires a positional argument to wrap", &Wrapped_Type);
            return nullptr;
        }
        self->target = Py_NewRef(PyTuple_GET_ITEM(args, 0));
        return self;
    }

    PyTypeObject Wrapped_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "Wrapped",
        .tp_basicsize = sizeof(Wrapped),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)dealloc,
        .tp_flags = Py_TPFLAGS_DEFAULT |
                    Py_TPFLAGS_HAVE_GC |
                    Py_TPFLAGS_BASETYPE,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)tp_traverse,
        .tp_clear = (inquiry)tp_clear,
        .tp_weaklistoffset = OFFSET_OF_MEMBER(Wrapped, weakreflist),
        // .tp_members = Wrapped_members,
        // .tp_base = &Proxy_Type,
        // .tp_new = create,
        // .tp_init = (initproc)init,
        // .tp_new = PyType_GenericNew,
        .tp_new = (newfunc)create,
    };

    bool Wrapped_Check(PyObject * obj) {
        return PyObject_TypeCheck(obj, &Wrapped_Type);
    }

    PyObject * Wrapped_Target(PyObject * proxy) {
        assert(Wrapped_Check(proxy));
        return reinterpret_cast<Wrapped *>(proxy)->target;
    }

    PyObject * create_wrapped(PyTypeObject * cls, PyObject * target) {
        
        if (!PyType_IsSubtype(cls, &Wrapped_Type)) {
            PyErr_Format(PyExc_TypeError, "first parameter of create_wrapped: %S must be a subtype of %S", cls, &Wrapped_Type);
            return nullptr;
        }
        // if (cls->tp_new != Wrapped_Type.tp_new) {
        //     PyErr_Format(PyExc_TypeError, "%S has different initializer to: %S", cls, &Wrapped_Type);
        //     return nullptr;
        // }
        // if (cls->tp_init != Wrapped_Type.tp_init) {
        //     PyErr_Format(PyExc_TypeError, "%S has different initializer to: %S", cls, &Wrapped_Type);
        //     return nullptr;
        // }
        
        Wrapped * self = reinterpret_cast<Wrapped *>(cls->tp_alloc(cls, 0));

        self->target = Py_NewRef(target);

        return (PyObject *)self;
    }
}