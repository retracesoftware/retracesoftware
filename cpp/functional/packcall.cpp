#include "functional.h"

struct PackCall : public PyObject {
    int loose;
    retracesoftware::FastCall function;
    vectorcallfunc vectorcall;

    static int clear(PackCall* self) {
        Py_CLEAR(self->function.callable);
        return 0;
    }

    static int traverse(PackCall* self, visitproc visit, void* arg) {
        Py_VISIT(self->function.callable);
        return 0;
    }

    static void dealloc(PackCall* self) {
        PyObject_GC_UnTrack(self);
        clear(self);
        Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
    }

    static PyObject* getattro(PackCall* self, PyObject* name) {
        return PyObject_GetAttr(self->function.callable, name);
    }

    static int setattro(PackCall* self, PyObject* name, PyObject* value) {
        return PyObject_SetAttr(self->function.callable, name, value);
    }

    static PyObject* descr_get(PyObject* self, PyObject* obj, PyObject* type) {
        return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
    }

    static PyObject* call(PackCall* self, PyObject* const* args, size_t nargsf, PyObject* kwnames) {
        const Py_ssize_t nargs = PyVectorcall_NARGS(nargsf);
        const Py_ssize_t kwcount = kwnames ? PyTuple_GET_SIZE(kwnames) : 0;
        const Py_ssize_t loose = nargs < self->loose ? nargs : self->loose;
        const Py_ssize_t packed_count = nargs - loose;

        PyObject* packed_args = PyTuple_New(packed_count);
        if (!packed_args) {
            return nullptr;
        }

        for (Py_ssize_t i = 0; i < packed_count; i++) {
            PyTuple_SET_ITEM(packed_args, i, Py_NewRef(args[loose + i]));
        }

        PyObject* kwargs_dict = PyDict_New();
        if (!kwargs_dict) {
            Py_DECREF(packed_args);
            return nullptr;
        }

        for (Py_ssize_t i = 0; i < kwcount; i++) {
            PyObject* key = PyTuple_GET_ITEM(kwnames, i);
            PyObject* value = args[nargs + i];
            if (PyDict_SetItem(kwargs_dict, key, value) < 0) {
                Py_DECREF(packed_args);
                Py_DECREF(kwargs_dict);
                return nullptr;
            }
        }

        PyObject** mem = (PyObject**)alloca(sizeof(PyObject*) * (loose + 3)) + 1;
        for (Py_ssize_t i = 0; i < loose; i++) {
            mem[i] = args[i];
        }
        mem[loose] = packed_args;
        mem[loose + 1] = kwargs_dict;

        PyObject* result = self->function(mem, (loose + 2) | PY_VECTORCALL_ARGUMENTS_OFFSET, nullptr);
        Py_DECREF(packed_args);
        Py_DECREF(kwargs_dict);
        return result;
    }

    static int init(PackCall* self, PyObject* args, PyObject* kwds) {
        int loose;
        PyObject* function;

        static const char* kwlist[] = {"loose", "function", NULL};

        if (!PyArg_ParseTupleAndKeywords(args, kwds, "iO", (char**)kwlist, &loose, &function)) {
            return -1;
        }

        if (loose < 0) {
            PyErr_SetString(PyExc_ValueError, "pack_call loose count must be a non-negative int");
            return -1;
        }

        if (!PyCallable_Check(function)) {
            PyErr_Format(PyExc_TypeError, "pack_call requires a callable function, got %S", function);
            return -1;
        }

        self->loose = loose;
        self->function = retracesoftware::FastCall(function);
        Py_INCREF(function);
        self->vectorcall = (vectorcallfunc)call;
        return 0;
    }
};

PyTypeObject PackCall_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "pack_call",
    .tp_basicsize = sizeof(PackCall),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)PackCall::dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(PackCall, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_getattro = (getattrofunc)PackCall::getattro,
    .tp_setattro = (setattrofunc)PackCall::setattro,
    .tp_flags = Py_TPFLAGS_DEFAULT |
                Py_TPFLAGS_HAVE_GC |
                Py_TPFLAGS_HAVE_VECTORCALL |
                Py_TPFLAGS_METHOD_DESCRIPTOR |
                Py_TPFLAGS_BASETYPE,
    .tp_doc = "pack_call(loose, function)\n--\n\n"
              "Pack trailing positional args and kwargs before calling function.\n\n"
              "Leaves the first 'loose' positional args unchanged, then appends\n"
              "a tuple of the remaining positional args and a kwargs dict.\n\n"
              "Example:\n"
              "    >>> target = lambda fn, args, kwargs: (fn, args, kwargs)\n"
              "    >>> pack_call(1, target)('f', 1, 2, x=3)\n"
              "    ('f', (1, 2), {'x': 3})",
    .tp_traverse = (traverseproc)PackCall::traverse,
    .tp_clear = (inquiry)PackCall::clear,
    .tp_descr_get = PackCall::descr_get,
    .tp_init = (initproc)PackCall::init,
    .tp_new = PyType_GenericNew,
};
