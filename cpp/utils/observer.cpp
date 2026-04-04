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

    static int set_callable_member(
        PyObject ** slot,
        PyObject * value,
        const char * name,
        bool allow_none
    ) {
        if (value == nullptr) {
            PyErr_Format(PyExc_AttributeError, "cannot delete %s", name);
            return -1;
        }

        PyObject * normalized = value;
        if (normalized == Py_None) {
            if (!allow_none) {
                PyErr_Format(PyExc_TypeError, "%s must be callable, got None", name);
                return -1;
            }
            normalized = nullptr;
        } else if (!PyCallable_Check(normalized)) {
            PyErr_Format(PyExc_TypeError, "%s must be callable or None, got %S", name, value);
            return -1;
        }

        PyObject * next = normalized ? Py_NewRef(normalized) : nullptr;
        Py_XDECREF(*slot);
        *slot = next;
        return 0;
    }

    static PyObject * get_function(Observer * self, void * closure) {
        return Py_NewRef(self->func);
    }

    static int set_function(Observer * self, PyObject * value, void * closure) {
        if (set_callable_member(&self->func, value, "function", false) < 0) {
            return -1;
        }
        self->func_vectorcall = extract_vectorcall(self->func);
        return 0;
    }

    static PyObject * get_on_call(Observer * self, void * closure) {
        return self->on_call ? Py_NewRef(self->on_call) : Py_NewRef(Py_None);
    }

    static int set_on_call(Observer * self, PyObject * value, void * closure) {
        return set_callable_member(&self->on_call, value, "on_call", true);
    }

    static PyObject * get_on_result(Observer * self, void * closure) {
        return self->on_result ? Py_NewRef(self->on_result) : Py_NewRef(Py_None);
    }

    static int set_on_result(Observer * self, PyObject * value, void * closure) {
        if (set_callable_member(&self->on_result, value, "on_result", true) < 0) {
            return -1;
        }
        self->on_result_vectorcall = self->on_result ? extract_vectorcall(self->on_result) : nullptr;
        return 0;
    }

    static PyObject * get_on_error(Observer * self, void * closure) {
        return self->on_error ? Py_NewRef(self->on_error) : Py_NewRef(Py_None);
    }

    static int set_on_error(Observer * self, PyObject * value, void * closure) {
        return set_callable_member(&self->on_error, value, "on_error", true);
    }

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
        Py_VISIT(self->func);
        Py_VISIT(self->on_call);
        Py_VISIT(self->on_result);
        Py_VISIT(self->on_error);

        return 0;
    }

    static int clear(Observer* self) {
        Py_CLEAR(self->func);
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

    static PyGetSetDef getset[] = {
        {"function", (getter)get_function, (setter)set_function, "wrapped function", NULL},
        {"on_call", (getter)get_on_call, (setter)set_on_call, "call hook", NULL},
        {"on_result", (getter)get_on_result, (setter)set_on_result, "result hook", NULL},
        {"on_error", (getter)get_on_error, (setter)set_on_error, "error hook", NULL},
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
        .tp_getset = getset,
        .tp_descr_get = tp_descr_get,
        .tp_init = (initproc)init,
        .tp_new = PyType_GenericNew,
    };
}
