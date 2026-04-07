#include "functional.h"

struct ApplyList : public PyVarObject {
    retracesoftware::FastCall function;
    vectorcallfunc vectorcall;
    PyObject *prefix[];

    static int clear(ApplyList *self) {
        Py_CLEAR(self->function.callable);
        for (Py_ssize_t i = 0; i < Py_SIZE(self); i++) {
            Py_CLEAR(self->prefix[i]);
        }
        return 0;
    }

    static int traverse(ApplyList *self, visitproc visit, void *arg) {
        Py_VISIT(self->function.callable);
        for (Py_ssize_t i = 0; i < Py_SIZE(self); i++) {
            Py_VISIT(self->prefix[i]);
        }
        return 0;
    }

    static void dealloc(ApplyList *self) {
        PyObject_GC_UnTrack(self);
        clear(self);
        Py_TYPE(self)->tp_free(reinterpret_cast<PyObject *>(self));
    }

    static PyObject *getattro(ApplyList *self, PyObject *name) {
        return PyObject_GetAttr(self->function.callable, name);
    }

    static int setattro(ApplyList *self, PyObject *name, PyObject *value) {
        return PyObject_SetAttr(self->function.callable, name, value);
    }

    static PyObject *descr_get(PyObject *self, PyObject *obj, PyObject *type) {
        return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
    }

    static PyObject *call(ApplyList *self, PyObject *const *args, size_t nargsf, PyObject *kwnames) {
        const Py_ssize_t nargs = PyVectorcall_NARGS(nargsf);
        const Py_ssize_t kwcount = kwnames ? PyTuple_GET_SIZE(kwnames) : 0;

        if (nargs == 0) {
            PyErr_SetString(PyExc_TypeError, "apply_list() requires at least one positional argument");
            return nullptr;
        }

        PyObject *items = PySequence_Fast(
            args[0],
            "apply_list() expects the first positional argument to be a sequence or iterable");
        if (!items) {
            return nullptr;
        }

        const Py_ssize_t prefix_count = Py_SIZE(self);
        const Py_ssize_t item_count = PySequence_Fast_GET_SIZE(items);
        const Py_ssize_t suffix_count = nargs - 1;
        const Py_ssize_t positional_count = prefix_count + item_count + suffix_count;

        PyObject **mem =
            reinterpret_cast<PyObject **>(alloca(sizeof(PyObject *) * (positional_count + kwcount + 1))) + 1;

        Py_ssize_t index = 0;
        for (Py_ssize_t i = 0; i < prefix_count; i++) {
            mem[index++] = self->prefix[i];
        }

        for (Py_ssize_t i = 0; i < item_count; i++) {
            mem[index++] = PySequence_Fast_GET_ITEM(items, i);
        }

        for (Py_ssize_t i = 1; i < nargs; i++) {
            mem[index++] = args[i];
        }

        for (Py_ssize_t i = 0; i < kwcount; i++) {
            mem[positional_count + i] = args[nargs + i];
        }

        PyObject *result =
            self->function(mem, positional_count | PY_VECTORCALL_ARGUMENTS_OFFSET, kwnames);
        Py_DECREF(items);
        return result;
    }

    static PyObject *create(PyTypeObject *type, PyObject *args, PyObject *kwds) {
        if (kwds && PyDict_Size(kwds) > 0) {
            PyErr_SetString(PyExc_TypeError, "apply_list() does not accept keyword arguments");
            return nullptr;
        }

        const Py_ssize_t nargs = PyTuple_GET_SIZE(args);
        if (nargs == 0) {
            PyErr_SetString(PyExc_TypeError, "apply_list() requires at least one positional argument");
            return nullptr;
        }

        PyObject *function = PyTuple_GET_ITEM(args, 0);
        if (!PyCallable_Check(function)) {
            PyErr_Format(PyExc_TypeError, "apply_list() expects a callable, got %S", function);
            return nullptr;
        }

        ApplyList *self = reinterpret_cast<ApplyList *>(type->tp_alloc(type, nargs - 1));
        if (!self) {
            return nullptr;
        }

        self->function = retracesoftware::FastCall(function);
        Py_INCREF(function);
        self->vectorcall = reinterpret_cast<vectorcallfunc>(ApplyList::call);

        for (Py_ssize_t i = 1; i < nargs; i++) {
            self->prefix[i - 1] = Py_NewRef(PyTuple_GET_ITEM(args, i));
        }

        return reinterpret_cast<PyObject *>(self);
    }
};

PyTypeObject ApplyList_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "apply_list",
    .tp_basicsize = sizeof(ApplyList),
    .tp_itemsize = sizeof(PyObject *),
    .tp_dealloc = reinterpret_cast<destructor>(ApplyList::dealloc),
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(ApplyList, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_getattro = reinterpret_cast<getattrofunc>(ApplyList::getattro),
    .tp_setattro = reinterpret_cast<setattrofunc>(ApplyList::setattro),
    .tp_flags = Py_TPFLAGS_DEFAULT |
                Py_TPFLAGS_HAVE_GC |
                Py_TPFLAGS_HAVE_VECTORCALL |
                Py_TPFLAGS_METHOD_DESCRIPTOR |
                Py_TPFLAGS_BASETYPE,
    .tp_doc = "apply_list(function, *initial)\n--\n\n"
              "Create a callable that expands a sequence into positional arguments.\n\n"
              "apply_list(function, *initial)(items, *suffix, **kwargs)\n"
              "calls function(*initial, *items, *suffix, **kwargs).",
    .tp_traverse = reinterpret_cast<traverseproc>(ApplyList::traverse),
    .tp_clear = reinterpret_cast<inquiry>(ApplyList::clear),
    .tp_descr_get = ApplyList::descr_get,
    .tp_new = reinterpret_cast<newfunc>(ApplyList::create),
};
