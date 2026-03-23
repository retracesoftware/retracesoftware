#include "stream.h"

#include <structmember.h>

namespace retracesoftware_stream {

    struct BindingRef : public PyObject {
        uint64_t index;
    };

    static void binding_ref_dealloc(BindingRef* self) {
        Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
    }

    static PyObject* binding_ref_repr(BindingRef* self) {
        const char* name = Py_TYPE(self)->tp_name;
        const char* last_dot = strrchr(name, '.');
        if (last_dot) {
            name = last_dot + 1;
        }
        return PyUnicode_FromFormat("%s(index=%llu)", name, (unsigned long long)self->index);
    }

    static PyMemberDef binding_ref_members[] = {
        {"index", T_ULONGLONG, OFFSET_OF_MEMBER(BindingRef, index), READONLY, "Binding index"},
        {nullptr},
    };

    PyObject* binding_ref_new(PyTypeObject* type, uint64_t index) {
        BindingRef* self = reinterpret_cast<BindingRef*>(type->tp_alloc(type, 0));
        if (!self) {
            return nullptr;
        }
        self->index = index;
        return reinterpret_cast<PyObject*>(self);
    }

    PyTypeObject BindingRef_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "BindingRef",
        .tp_basicsize = sizeof(BindingRef),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)binding_ref_dealloc,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
        .tp_doc = "Base type for tape binding reference records.",
        .tp_members = binding_ref_members,
        .tp_repr = (reprfunc)binding_ref_repr,
        .tp_new = PyType_GenericNew,
    };

    PyTypeObject BindingRefCreate_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "BindingCreate",
        .tp_basicsize = sizeof(BindingRef),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)binding_ref_dealloc,
        .tp_flags = Py_TPFLAGS_DEFAULT,
        .tp_doc = "Tape record for creation of an externally bound object slot.",
        .tp_members = binding_ref_members,
        .tp_repr = (reprfunc)binding_ref_repr,
        .tp_base = &BindingRef_Type,
        .tp_new = PyType_GenericNew,
    };

    PyTypeObject BindingRefLookup_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "BindingLookup",
        .tp_basicsize = sizeof(BindingRef),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)binding_ref_dealloc,
        .tp_flags = Py_TPFLAGS_DEFAULT,
        .tp_doc = "Tape record for lookup of an externally bound object slot.",
        .tp_members = binding_ref_members,
        .tp_repr = (reprfunc)binding_ref_repr,
        .tp_base = &BindingRef_Type,
        .tp_new = PyType_GenericNew,
    };

    PyTypeObject BindingRefDelete_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "BindingDelete",
        .tp_basicsize = sizeof(BindingRef),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)binding_ref_dealloc,
        .tp_flags = Py_TPFLAGS_DEFAULT,
        .tp_doc = "Tape record for deletion of an externally bound object slot.",
        .tp_members = binding_ref_members,
        .tp_repr = (reprfunc)binding_ref_repr,
        .tp_base = &BindingRef_Type,
        .tp_new = PyType_GenericNew,
    };

}