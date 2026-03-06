#include "utils.h"
#include <structmember.h>

namespace retracesoftware {

    struct Visitor : public PyObject {
        PyObject * func;
        int from;
        vectorcallfunc func_vectorcall;
        vectorcallfunc vectorcall;
    };

    static void visit(Visitor * self, PyObject * arg);

    static void visit_tuple(Visitor * self, PyObject * tuple) {
        size_t n = PyTuple_GET_SIZE(tuple);

        for (size_t i = 0; i < n; ++i) {
            visit(self, PyTuple_GET_ITEM(tuple, i));
        }
    }

    static void visit_list(Visitor * self, PyObject * list) {
        size_t n = PyList_GET_SIZE(list);

        for (size_t i = 0; i < n; ++i) {
            visit(self, PyList_GET_ITEM(list, i));
        }
    }

    static void visit_dict(Visitor * self, PyObject * dict) {
        Py_ssize_t pos = 0;
        PyObject *key, *value;

        while (PyDict_Next(dict, &pos, &key, &value)) {
            visit(self, value);
        }
    }

    static void visit(Visitor * self, PyObject * arg) {

        if (arg != Py_None) {

            PyTypeObject * cls = Py_TYPE(arg);

            if (cls == &PyTuple_Type) {
                visit_tuple(self, arg);
            } else if (cls == &PyList_Type) {
                visit_list(self, arg);
            } else if (cls == &PyDict_Type) {
                visit_dict(self, arg);
            } else {
                PyObject * result = self->func_vectorcall(self->func, &arg, 1, nullptr);
                Py_XDECREF(result);
                if (!result) throw nullptr;
            }
        }
    }

    static PyObject * call(Visitor * self, PyObject* const * args, size_t nargsf, PyObject* kwnames) {

        int nargs = PyVectorcall_NARGS(nargsf) +  (kwnames ? PyTuple_Size(kwnames) : 0);
        
        try {
            for (int i = self->from; i < nargs; i++) {
                visit(self, args[i]);
            }
            Py_RETURN_NONE;
        } catch (...) {
            return nullptr;
        }
    }

    static int traverse(Visitor* self, visitproc visit, void* arg) {
        Py_VISIT(self->func);

        return 0;
    }

    static int clear(Visitor* self) {
        Py_CLEAR(self->func);
        return 0;
    }

    static void dealloc(Visitor *self) {
        PyObject_GC_UnTrack(self);          // Untrack from the GC
        clear(self);
        Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
    }

    static int init(Visitor *self, PyObject *args, PyObject *kwds) {

        PyObject * function = NULL;
        int from = 0;

        static const char *kwlist[] = { "function", "from_arg", NULL};

        if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|I", (char **)kwlist, &function, &from))
        {
            return -1; // Return NULL on failure
        }

        CHECK_CALLABLE(function);
        
        self->func = Py_XNewRef(function);
        self->func_vectorcall = extract_vectorcall(function);
        self->vectorcall = (vectorcallfunc)call;
        self->from = from;

        return 0;
    }

    static PyMemberDef members[] = {
        // {"on_call", T_OBJECT, OFFSET_OF_MEMBER(Observer, on_call), READONLY, "TODO"},
        // {"on_result", T_OBJECT, OFFSET_OF_MEMBER(Observer, on_result), READONLY, "TODO"},
        // {"on_error", T_OBJECT, OFFSET_OF_MEMBER(Observer, on_error), READONLY, "TODO"},
        // {"function", T_OBJECT, OFFSET_OF_MEMBER(Observer, func), READONLY, "TODO"},
        {NULL}  /* Sentinel */
    };

    PyTypeObject Visitor_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "visitor",
        .tp_basicsize = sizeof(Visitor),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(Visitor, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)traverse,
        .tp_clear = (inquiry)clear,
        // .tp_methods = methods,
        .tp_members = members,
        .tp_init = (initproc)init,
        .tp_new = PyType_GenericNew,
    };
}