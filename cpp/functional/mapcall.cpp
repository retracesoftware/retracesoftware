#include "functional.h"

struct MapCall : public PyVarObject {
    bool rest_is_identity;
    retracesoftware::FastCall rest_transform;
    retracesoftware::FastCall func;
    vectorcallfunc vectorcall;
    retracesoftware::FastCall prefix_transforms[];

    static int clear(MapCall* self) {
        if (!self->rest_is_identity) {
            Py_CLEAR(self->rest_transform.callable);
        }
        Py_CLEAR(self->func.callable);
        for (Py_ssize_t i = 0; i < self->ob_size; i++) {
            Py_CLEAR(self->prefix_transforms[i].callable);
        }
        return 0;
    }

    static int traverse(MapCall* self, visitproc visit, void* arg) {
        if (!self->rest_is_identity) {
            Py_VISIT(self->rest_transform.callable);
        }
        Py_VISIT(self->func.callable);
        for (Py_ssize_t i = 0; i < self->ob_size; i++) {
            Py_VISIT(self->prefix_transforms[i].callable);
        }
        return 0;
    }

    static void dealloc(MapCall* self) {
        PyObject_GC_UnTrack(self);
        clear(self);
        Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
    }

    static PyObject* getattro(MapCall* self, PyObject* name) {
        return PyObject_GetAttr(self->func.callable, name);
    }

    static int setattro(MapCall* self, PyObject* name, PyObject* value) {
        if (self->rest_is_identity) {
            return PyObject_SetAttr(self->func.callable, name, value);
        }
        PyObject* transformed = self->rest_transform(value);
        if (!transformed) return -1;
        int result = PyObject_SetAttr(self->func.callable, name, transformed);
        Py_DECREF(transformed);
        return result;
    }

    static PyObject* call(MapCall* self, PyObject* const* args, size_t nargsf, PyObject* kwnames) {
        const size_t nargs = PyVectorcall_NARGS(nargsf);
        const size_t kwcount = kwnames ? PyTuple_GET_SIZE(kwnames) : 0;

        if (self->rest_is_identity && self->ob_size == 0) {
            return self->func(args, nargsf, kwnames);
        }

        if (nargs == 0 && kwcount == 0) {
            return self->func(args, nargsf, nullptr);
        }

        const size_t all = nargs + kwcount;
        PyObject* on_stack[SMALL_ARGS + 1];
        PyObject** mem;

        if (all < SMALL_ARGS) {
            mem = on_stack + 1;
        } else {
            mem = (PyObject**)alloca(sizeof(PyObject*) * (all + 1)) + 1;
        }

        size_t i = 0;
        for (; i < nargs; i++) {
            PyObject* transformed = nullptr;
            if (i < (size_t)self->ob_size) {
                transformed = self->prefix_transforms[i](args[i]);
            } else if (self->rest_is_identity) {
                transformed = args[i];
            } else {
                transformed = self->rest_transform(args[i]);
            }
            if (!transformed) {
                for (size_t j = 0; j < i; j++) {
                    if (j < (size_t)self->ob_size || !self->rest_is_identity) {
                        Py_XDECREF(mem[j]);
                    }
                }
                return nullptr;
            }
            mem[i] = transformed;
        }

        for (size_t j = 0; j < kwcount; j++) {
            PyObject* transformed = self->rest_is_identity ? args[nargs + j] : self->rest_transform(args[nargs + j]);
            if (!transformed) {
                for (size_t k = 0; k < nargs + j; k++) {
                    if (k < (size_t)self->ob_size || !self->rest_is_identity) {
                        Py_XDECREF(mem[k]);
                    }
                }
                return nullptr;
            }
            mem[nargs + j] = transformed;
        }

        PyObject* result = self->func(mem, nargs | PY_VECTORCALL_ARGUMENTS_OFFSET, kwnames);

        for (size_t j = 0; j < all; j++) {
            if (j < (size_t)self->ob_size || !self->rest_is_identity) {
                Py_XDECREF(mem[j]);
            }
        }

        return result;
    }

    static PyObject* create(PyTypeObject* type, PyObject* args, PyObject* kwds) {
        int rest_is_identity = 0;

        if (kwds) {
            PyObject* rest_is_identity_obj = PyDict_GetItemString(kwds, "rest_is_identity");
            if (rest_is_identity_obj) {
                rest_is_identity = PyObject_IsTrue(rest_is_identity_obj);
                if (rest_is_identity < 0) {
                    return nullptr;
                }
            }
            if (PyDict_Size(kwds) > (rest_is_identity_obj ? 1 : 0)) {
                PyErr_SetString(PyExc_TypeError, "mapcall() only accepts keyword argument rest_is_identity");
                return nullptr;
            }
        }

        const Py_ssize_t count = PyTuple_GET_SIZE(args);
        if (count < 1 || (!rest_is_identity && count < 2)) {
            PyErr_SetString(PyExc_TypeError, "mapcall() requires a function and either a rest transform or rest_is_identity=True");
            return nullptr;
        }

        PyObject* function = PyTuple_GET_ITEM(args, 0);
        PyObject* rest_transform = rest_is_identity ? nullptr : PyTuple_GET_ITEM(args, count - 1);
        const Py_ssize_t prefix_count = rest_is_identity ? count - 1 : count - 2;

        if (!PyCallable_Check(function)) {
            PyErr_Format(PyExc_TypeError, "mapcall() function must be callable, got %S", function);
            return nullptr;
        }
        if (rest_transform && !PyCallable_Check(rest_transform)) {
            PyErr_Format(PyExc_TypeError, "mapcall() rest transform must be callable, got %S", rest_transform);
            return nullptr;
        }

        MapCall* self = reinterpret_cast<MapCall*>(type->tp_alloc(type, prefix_count));
        if (!self) {
            return nullptr;
        }

        self->rest_is_identity = rest_is_identity;
        self->func = retracesoftware::FastCall(function);
        Py_INCREF(function);
        if (rest_transform) {
            self->rest_transform = retracesoftware::FastCall(rest_transform);
            Py_INCREF(rest_transform);
        } else {
            self->rest_transform = retracesoftware::FastCall();
        }

        for (Py_ssize_t i = 0; i < prefix_count; i++) {
            PyObject* transform = PyTuple_GET_ITEM(args, i + 1);
            if (!PyCallable_Check(transform)) {
                Py_DECREF(self);
                PyErr_Format(PyExc_TypeError, "mapcall() transforms must be callable, got %S", transform);
                return nullptr;
            }
            self->prefix_transforms[i] = retracesoftware::FastCall(transform);
            Py_INCREF(transform);
        }

        self->vectorcall = (vectorcallfunc)call;
        return reinterpret_cast<PyObject*>(self);
    }

    static PyObject* descr_get(PyObject* self, PyObject* obj, PyObject* type) {
        return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
    }
};


PyTypeObject MapCall_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "mapcall",
    .tp_basicsize = sizeof(MapCall),
    .tp_itemsize = sizeof(retracesoftware::FastCall),
    .tp_dealloc = (destructor)MapCall::dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(MapCall, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_getattro = (getattrofunc)MapCall::getattro,
    .tp_setattro = (setattrofunc)MapCall::setattro,
    .tp_flags = Py_TPFLAGS_DEFAULT |
                Py_TPFLAGS_HAVE_GC |
                Py_TPFLAGS_HAVE_VECTORCALL |
                Py_TPFLAGS_METHOD_DESCRIPTOR,
    .tp_doc = "mapcall(function, arg0tx, arg1tx, ..., resttx)\n--\n\n"
              "Apply per-position transforms to leading positional arguments and\n"
              "a rest transform to remaining positional args and all kwarg values\n"
              "before calling function.\n\n"
              "Examples:\n"
              "    >>> mapcall(f, str)(1, x=2)\n"
              "    f('1', x='2')\n"
              "    >>> mapcall(f, str, int, float)(a, b, c, d=1)\n"
              "    f(str(a), int(b), float(c), d=float(1))",
    .tp_traverse = (traverseproc)MapCall::traverse,
    .tp_clear = (inquiry)MapCall::clear,
    .tp_descr_get = MapCall::descr_get,
    .tp_new = (newfunc)MapCall::create,
};
