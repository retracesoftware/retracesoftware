#include "utils.h"
#include <structmember.h>

namespace retracesoftware {

    struct Observer : public PyObject {
        
        PyObject * func;
        vectorcallfunc func_vectorcall;

        PyObject * on_call;
        PyObject * on_result;
        vectorcallfunc on_result_vectorcall;
        PyObject * on_error;
        vectorcallfunc vectorcall;
    };

    static inline bool call_void(vectorcallfunc vectorcall, PyObject * callable, PyObject* const * args, size_t nargsf, PyObject* kwnames) {
        assert (!PyErr_Occurred());

        PyObject * result = vectorcall(callable, args, nargsf, kwnames);

        if (result) {
            Py_DECREF(result);
            assert(!PyErr_Occurred());
            return true;
        } else {
            assert(PyErr_Occurred());
            return false;
        }
    }

    static PyObject * call(Observer * self, PyObject* const * args, size_t nargsf, PyObject* kwnames) {
        
        assert (!PyErr_Occurred());

        if (self->on_call) {
            if (!call_void(PyObject_Vectorcall, self->on_call, args, nargsf, kwnames)) {
                return nullptr;
            }
            assert (!PyErr_Occurred());
        }

        PyObject * result = self->func_vectorcall(self->func, args, nargsf, kwnames);

        if (result) {
            assert (!PyErr_Occurred());
            if (self->on_result) {
                if (!call_void(self->on_result_vectorcall, self->on_result, &result, 1, nullptr)) {
                    Py_DECREF(result);
                    return nullptr;
                }
            }
        } else if (self->on_error) {
            assert (PyErr_Occurred());

            PyObject * exc[] = {nullptr, nullptr, nullptr};

            // Fetch the current exception
            PyErr_Fetch(exc + 0, exc + 1, exc + 2);

            // Normalize the exception - ensures exc[1] is an instance, not a string
            PyErr_NormalizeException(&exc[0], &exc[1], &exc[2]);

            for (int i = 0; i < 3; i++) if (!exc[i]) exc[i] = Py_None;

            if (!call_void(PyObject_Vectorcall, self->on_error, exc, 3, nullptr)) {
                for (int i = 0; i < 3; i++) if (exc[i] != Py_None) Py_DECREF(exc[i]);
                return nullptr;
            }

            PyErr_Restore(exc[0] == Py_None ? nullptr : exc[0], 
                        exc[1] == Py_None ? nullptr : exc[1],
                        exc[2] == Py_None ? nullptr : exc[2]);
        }
        return result;
    }

    static int traverse(Observer* self, visitproc visit, void* arg) {
        Py_VISIT(self->on_call);
        Py_VISIT(self->on_result);
        Py_VISIT(self->on_error);

        return 0;
    }

    static int clear(Observer* self) {
        Py_CLEAR(self->on_call);
        Py_CLEAR(self->on_result);
        Py_CLEAR(self->on_error);
        return 0;
    }

    static void dealloc(Observer *self) {
        PyObject_GC_UnTrack(self);          // Untrack from the GC
        clear(self);
        Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
    }

    static int init(Observer *self, PyObject *args, PyObject *kwds) {

        PyObject * function = NULL;
        PyObject * on_call = NULL;
        PyObject * on_result = NULL;
        PyObject * on_error = NULL;

        static const char *kwlist[] = {
            "function",
            "on_call",
            "on_result",
            "on_error",
            NULL};

        if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|OOO", 
            (char **)kwlist,
            &function,
            &on_call,
            &on_result,
            &on_error))
        {
            return -1; // Return NULL on failure
        }

        CHECK_CALLABLE(function);
        CHECK_CALLABLE(on_call);
        CHECK_CALLABLE(on_result);
        CHECK_CALLABLE(on_error);
        
        self->func = Py_XNewRef(function);
        self->func_vectorcall = extract_vectorcall(function);
        self->on_call = Py_XNewRef(on_call);
        self->on_result = Py_XNewRef(on_result);

        if (self->on_result)
            self->on_result_vectorcall = extract_vectorcall(on_result);

        self->on_error = Py_XNewRef(on_error);
        self->vectorcall = (vectorcallfunc)call;

        return 0;
    }

    static PyMemberDef members[] = {
        {"on_call", T_OBJECT, OFFSET_OF_MEMBER(Observer, on_call), READONLY, "TODO"},
        {"on_result", T_OBJECT, OFFSET_OF_MEMBER(Observer, on_result), READONLY, "TODO"},
        {"on_error", T_OBJECT, OFFSET_OF_MEMBER(Observer, on_error), READONLY, "TODO"},
        {"function", T_OBJECT, OFFSET_OF_MEMBER(Observer, func), READONLY, "TODO"},
        {NULL}  /* Sentinel */
    };

    static PyObject* tp_descr_get(PyObject *self, PyObject *obj, PyObject *type) {
        return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
    }

    PyTypeObject Observer_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "observer",
        .tp_basicsize = sizeof(Observer),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(Observer, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | 
                    Py_TPFLAGS_HAVE_GC | 
                    Py_TPFLAGS_HAVE_VECTORCALL | 
                    Py_TPFLAGS_METHOD_DESCRIPTOR,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)traverse,
        .tp_clear = (inquiry)clear,
        // .tp_methods = methods,
        .tp_members = members,
        .tp_descr_get = tp_descr_get,
        .tp_init = (initproc)init,
        .tp_new = PyType_GenericNew,
    };
}