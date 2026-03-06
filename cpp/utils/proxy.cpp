#include "utils.h"
#include <exception>
#include <structmember.h>

namespace retracesoftware {
    
    static PyObject* tp_getattro(PyObject * self, PyObject * name) {
        // PyObject * first_try = PyObject_GenericGetAttr(self, name);
    
        // if (first_try) return first_try;
    
        // PyErr_Clear();

        return Py_TYPE(Wrapped_Target(self))->tp_getattro(Wrapped_Target(self), name);
        // return PyObject_GetAttr(Wrapped_Target(self), name);
    }

    PyTypeObject Proxy_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "Proxy",
        .tp_basicsize = Wrapped_Type.tp_basicsize,
        .tp_itemsize = 0,
        .tp_getattro = tp_getattro,
        .tp_flags = Wrapped_Type.tp_flags,
        .tp_doc = "TODO",
        .tp_traverse = Wrapped_Type.tp_traverse,
        .tp_clear = Wrapped_Type.tp_clear,
        // .tp_members = Wrapped_members,
        .tp_base = &Wrapped_Type,
    };
}