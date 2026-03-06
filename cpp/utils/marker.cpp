#include "utils.h"
#include <exception>
#include <structmember.h>

namespace retracesoftware {
    
    PyTypeObject Marker_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "marker",
        .tp_basicsize = sizeof(PyObject),
        .tp_itemsize = 0,
        // .tp_getattro = tp_getattro,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
        .tp_doc = "Marker type",
        .tp_base = &PyBaseObject_Type,
    };
}