#include "stream.h"

#include <structmember.h>

namespace retracesoftware_stream {

    struct NewMarker : public PyObject {
        uint64_t index;
        PyObject * cls;
    };

    static int new_marker_traverse(NewMarker * self, visitproc visit, void * arg) {
        Py_VISIT(self->cls);
        return 0;
    }

    static int new_marker_clear(NewMarker * self) {
        Py_CLEAR(self->cls);
        return 0;
    }

    static void new_marker_dealloc(NewMarker * self) {
        PyObject_GC_UnTrack(self);
        new_marker_clear(self);
        Py_TYPE(self)->tp_free(reinterpret_cast<PyObject *>(self));
    }

    static PyObject * new_marker_repr(NewMarker * self) {
        const char * name = Py_TYPE(self)->tp_name;
        const char * last_dot = strrchr(name, '.');
        if (last_dot) {
            name = last_dot + 1;
        }
        return PyUnicode_FromFormat(
            "%s(index=%llu, cls=%R)",
            name,
            (unsigned long long)self->index,
            self->cls ? self->cls : Py_None
        );
    }

    static PyMemberDef new_marker_members[] = {
        {"index", T_ULONGLONG, OFFSET_OF_MEMBER(NewMarker, index), READONLY, "Intern slot index"},
        {"cls", T_OBJECT_EX, OFFSET_OF_MEMBER(NewMarker, cls), READONLY, "Recorded type for the patched object"},
        {nullptr},
    };

    PyObject * new_marker_new(uint64_t index, PyObject * cls) {
        NewMarker * self = PyObject_GC_New(NewMarker, &NewMarker_Type);
        if (!self) {
            return nullptr;
        }
        self->index = index;
        self->cls = Py_NewRef(cls);
        PyObject_GC_Track(self);
        return reinterpret_cast<PyObject *>(self);
    }

    PyTypeObject NewMarker_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "NewMarker",
        .tp_basicsize = sizeof(NewMarker),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)new_marker_dealloc,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
        .tp_doc = "Tape marker for a NEW_PATCHED object slot.",
        .tp_traverse = (traverseproc)new_marker_traverse,
        .tp_clear = (inquiry)new_marker_clear,
        .tp_members = new_marker_members,
        .tp_repr = (reprfunc)new_marker_repr,
        .tp_new = PyType_GenericNew,
    };
}
