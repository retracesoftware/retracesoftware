#include "utils.h"
#include <structmember.h>
#include <vector>

namespace retracesoftware {
    struct Reference : public PyObject {
        void * handle;
    };

    static Py_hash_t tp_hash(PyObject *self) {
        return _Py_HashPointer(reinterpret_cast<void *>(Reference_GetPointer(self)));
    }

    static PyObject* tp_richcompare(PyObject *a, PyObject *b, int op) {
        if (op != Py_EQ && op != Py_NE) {  // Support only == and !=
            Py_RETURN_NOTIMPLEMENTED;
        }

        if (!PyObject_TypeCheck(b, &Reference_Type)) {
            Py_RETURN_NOTIMPLEMENTED;  // Not the same type
        }

        int is_equal = Reference_GetPointer(a) == Reference_GetPointer(b);

        return PyBool_FromLong(op == Py_EQ ? is_equal : !is_equal);
    }

    static thread_local std::vector<Reference *> cache = std::vector<Reference *>(0);

    static void dealloc(Reference *self) {
        cache.push_back(self);
    }

    static PyMethodDef methods[] = {
        {NULL}  /* Sentinel */
    };

    static PyMemberDef members[] = {
        {NULL}  /* Sentinel */
    };

    static PyObject * int_getter(Reference *self, void *closure) {
        return PyLong_FromUnsignedLongLong((uintptr_t)self->handle);
    }

    static PyGetSetDef getset[] = {
        {"int_value", (getter)int_getter, NULL, "TODO", NULL},
        {NULL}  // Sentinel
    };

    static PyObject* tp_str(PyObject *self) {
        return PyUnicode_FromFormat("%s(%p)", Py_TYPE(self)->tp_name, Reference_GetPointer(self));
    }

    // static PyObject * Reference_New(PyTypeObject * subtype, void * handle) {
    //     Reference* self;
    //     if (_cache.size() > 0) {
    //         self = _cache.back();
    //         _cache.pop_back();
    //         _Py_NewReference((PyObject *)self);
    //     } else {
    //         self = (Reference *)Reference_Type.tp_alloc(subtype, 0);
    //         if (!self) {
    //             return NULL;
    //         }
    //     }
    //     self->_handle = handle;
    //     return (PyObject *)self;
    // }

    static PyObject * alloc(PyTypeObject * type, void * handle) {

        Reference* self;

        if (cache.size() > 0) {
            self = cache.back();
            cache.pop_back();
            self->ob_type = type;
            _Py_NewReference((PyObject *)self);
        } else {
            self = (Reference *)type->tp_alloc(type, 0);
            if (!self) {
                return NULL;
            }
        }
        self->handle = handle;
        return (PyObject *)self;
    }

    PyObject * Reference_New(void * handle) {
        return alloc(&Reference_Type, handle);
    }

    static PyObject * create(PyTypeObject *type, PyObject *args, PyObject *kwds) {

        PyObject * pointer;

        static const char *kwlist[] = {"pointer", NULL};

        if (!PyArg_ParseTupleAndKeywords(args, kwds, "O", (char **)kwlist, &pointer))
        {
            return NULL; // Return NULL on failure
        }

        return alloc(type, pointer);
    }

    PyTypeObject Reference_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "reference",
        .tp_basicsize = sizeof(Reference),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)dealloc,
        .tp_repr = tp_str,
        .tp_hash = tp_hash,
        .tp_str = tp_str,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
        .tp_doc = "TODO",
        .tp_richcompare = tp_richcompare,
        .tp_methods = methods,
        .tp_members = members,
        .tp_getset = getset,
        .tp_new = (newfunc)create,
    };

    void * Reference_GetPointer(PyObject * reference) {
        assert(PyObject_TypeCheck(reference, &Reference_Type));
        
        return reinterpret_cast<Reference *>(reference)->handle;
    }
}