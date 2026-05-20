#include "functional.h"
#include <structmember.h>

struct TransformCall : PyVarObject {
    retracesoftware::FastCall function;
    retracesoftware::FastCall rest_transform;
    retracesoftware::FastCall result_transform;
    retracesoftware::FastCall on_error;
    retracesoftware::FastCall error_transform;
    vectorcallfunc vectorcall;
    retracesoftware::FastCall arg_transforms[1];
};

static inline Py_ssize_t prefix_count(TransformCall *self) {
    return Py_SIZE(self);
}

static void set_fastcall(retracesoftware::FastCall *slot, PyObject *callable) {
    slot->callable = callable && callable != Py_None ? callable : nullptr;
    slot->vectorcall = slot->callable ? retracesoftware::extract_vectorcall(callable) : nullptr;
    Py_XINCREF(slot->callable);
}

static bool is_callable_arg(const char *name, PyObject *value) {
    if (!value || PyCallable_Check(value)) {
        return true;
    }

    PyErr_Format(PyExc_TypeError, "Parameter '%s' must be callable, but was: %S", name, value);
    return false;
}

static bool is_optional_callable_arg(const char *name, PyObject *value) {
    if (!value || value == Py_None || PyCallable_Check(value)) {
        return true;
    }

    PyErr_Format(PyExc_TypeError, "Parameter '%s' must be callable or None, but was: %S", name, value);
    return false;
}

static bool is_allowed_keyword(PyObject *key) {
    if (!PyUnicode_Check(key)) {
        PyErr_SetString(PyExc_TypeError, "transform_call() keywords must be strings");
        return false;
    }

    return PyUnicode_CompareWithASCIIString(key, "rest_transform") == 0
        || PyUnicode_CompareWithASCIIString(key, "result_transform") == 0
        || PyUnicode_CompareWithASCIIString(key, "on_error") == 0
        || PyUnicode_CompareWithASCIIString(key, "error_transform") == 0;
}

static PyObject * handle_error(TransformCall *self) {
    PyObject *exc_type = nullptr;
    PyObject *exc_value = nullptr;
    PyObject *exc_traceback = nullptr;
    PyErr_Fetch(&exc_type, &exc_value, &exc_traceback);
    PyErr_NormalizeException(&exc_type, &exc_value, &exc_traceback);

    PyObject *type_arg = exc_type ? exc_type : Py_None;
    PyObject *value_arg = exc_value ? exc_value : Py_None;
    PyObject *traceback_arg = exc_traceback ? exc_traceback : Py_None;

    if (self->on_error.callable) {
        PyObject *side_effect = self->on_error(type_arg, value_arg, traceback_arg);
        if (!side_effect) {
            Py_XDECREF(exc_type);
            Py_XDECREF(exc_value);
            Py_XDECREF(exc_traceback);
            return nullptr;
        }
        Py_DECREF(side_effect);
    }

    if (!self->error_transform.callable) {
        PyErr_Restore(exc_type, exc_value, exc_traceback);
        return nullptr;
    }

    PyObject *transformed = self->error_transform(type_arg, value_arg, traceback_arg);
    if (!transformed) {
        Py_XDECREF(exc_type);
        Py_XDECREF(exc_value);
        Py_XDECREF(exc_traceback);
        return nullptr;
    }

    int is_exception = PyObject_IsInstance(transformed, PyExc_BaseException);
    if (is_exception <= 0) {
        Py_DECREF(transformed);
        Py_XDECREF(exc_type);
        Py_XDECREF(exc_value);
        Py_XDECREF(exc_traceback);
        if (is_exception == 0) {
            PyErr_SetString(PyExc_TypeError, "error_transform must return a BaseException instance");
        }
        return nullptr;
    }

    if (exc_traceback && PyException_SetTraceback(transformed, exc_traceback) < 0) {
        Py_DECREF(transformed);
        Py_XDECREF(exc_type);
        Py_XDECREF(exc_value);
        Py_XDECREF(exc_traceback);
        return nullptr;
    }

    PyErr_SetObject((PyObject *)Py_TYPE(transformed), transformed);
    Py_DECREF(transformed);
    Py_XDECREF(exc_type);
    Py_XDECREF(exc_value);
    Py_XDECREF(exc_traceback);
    return nullptr;
}

static PyObject * vectorcall(TransformCall *self, PyObject *const *args, size_t nargsf, PyObject *kwnames) {
    size_t nargs = PyVectorcall_NARGS(nargsf);
    size_t kwcount = kwnames ? PyTuple_GET_SIZE(kwnames) : 0;
    size_t all = nargs + kwcount;
    Py_ssize_t prefix = prefix_count(self);

    PyObject *on_stack[SMALL_ARGS + 1];
    PyObject **raw = on_stack;
    if (all >= SMALL_ARGS) {
        raw = (PyObject **)alloca(sizeof(PyObject *) * (all + 1));
    }
    raw[0] = nullptr;
    PyObject **mem = raw + 1;

    for (size_t i = 0; i < nargs; i++) {
        retracesoftware::FastCall *transform =
            i < (size_t)prefix ? &self->arg_transforms[i] : &self->rest_transform;
        mem[i] = (*transform)(args[i]);
        if (!mem[i]) {
            for (size_t j = 0; j < i; j++) {
                Py_DECREF(mem[j]);
            }
            return nullptr;
        }
    }

    for (size_t i = nargs; i < all; i++) {
        mem[i] = self->rest_transform(args[i]);
        if (!mem[i]) {
            for (size_t j = 0; j < i; j++) {
                Py_DECREF(mem[j]);
            }
            return nullptr;
        }
    }

    PyObject *result = self->function(mem, nargs | PY_VECTORCALL_ARGUMENTS_OFFSET, kwnames);

    for (size_t i = 0; i < all; i++) {
        Py_DECREF(mem[i]);
    }

    if (!result) {
        return handle_error(self);
    }

    PyObject *transformed = self->result_transform(result);
    Py_DECREF(result);
    return transformed;
}

static int traverse(TransformCall *self, visitproc visit, void *arg) {
    Py_VISIT(self->function.callable);
    Py_VISIT(self->rest_transform.callable);
    Py_VISIT(self->result_transform.callable);
    Py_VISIT(self->on_error.callable);
    Py_VISIT(self->error_transform.callable);
    for (Py_ssize_t i = 0; i < prefix_count(self); i++) {
        Py_VISIT(self->arg_transforms[i].callable);
    }
    return 0;
}

static int clear(TransformCall *self) {
    Py_CLEAR(self->function.callable);
    Py_CLEAR(self->rest_transform.callable);
    Py_CLEAR(self->result_transform.callable);
    Py_CLEAR(self->on_error.callable);
    Py_CLEAR(self->error_transform.callable);
    for (Py_ssize_t i = 0; i < prefix_count(self); i++) {
        Py_CLEAR(self->arg_transforms[i].callable);
    }
    return 0;
}

static void dealloc(TransformCall *self) {
    PyObject_GC_UnTrack(self);
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject * getattro(TransformCall *self, PyObject *name) {
    return PyObject_GetAttr(self->function.callable, name);
}

static PyObject * descr_get(PyObject *self, PyObject *obj, PyObject *type) {
    return obj == nullptr || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
}

static PyObject * create(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    Py_ssize_t positional = PyTuple_GET_SIZE(args);
    if (positional < 1) {
        PyErr_SetString(PyExc_TypeError, "transform_call() requires a function");
        return nullptr;
    }

    if (!kwds || !PyDict_Check(kwds)) {
        PyErr_SetString(PyExc_TypeError, "transform_call() requires rest_transform and result_transform");
        return nullptr;
    }

    PyObject *function = PyTuple_GET_ITEM(args, 0);
    PyObject *rest_transform = PyDict_GetItemString(kwds, "rest_transform");
    PyObject *result_transform = PyDict_GetItemString(kwds, "result_transform");
    PyObject *on_error = PyDict_GetItemString(kwds, "on_error");
    PyObject *error_transform = PyDict_GetItemString(kwds, "error_transform");

    Py_ssize_t pos = 0;
    PyObject *key = nullptr;
    PyObject *value = nullptr;
    while (PyDict_Next(kwds, &pos, &key, &value)) {
        if (!is_allowed_keyword(key)) {
            return nullptr;
        }
    }

    if (!rest_transform) {
        PyErr_SetString(PyExc_TypeError, "transform_call() missing required keyword-only argument: rest_transform");
        return nullptr;
    }
    if (!result_transform) {
        PyErr_SetString(PyExc_TypeError, "transform_call() missing required keyword-only argument: result_transform");
        return nullptr;
    }

    if (!is_callable_arg("function", function)
        || !is_callable_arg("rest_transform", rest_transform)
        || !is_callable_arg("result_transform", result_transform)
        || !is_optional_callable_arg("on_error", on_error)
        || !is_optional_callable_arg("error_transform", error_transform)) {
        return nullptr;
    }

    Py_ssize_t prefix = positional - 1;
    for (Py_ssize_t i = 0; i < prefix; i++) {
        PyObject *transform = PyTuple_GET_ITEM(args, i + 1);
        if (!is_callable_arg("arg_transform", transform)) {
            return nullptr;
        }
    }

    TransformCall *self = (TransformCall *)type->tp_alloc(type, prefix);
    if (!self) {
        return nullptr;
    }

    set_fastcall(&self->function, function);
    set_fastcall(&self->rest_transform, rest_transform);
    set_fastcall(&self->result_transform, result_transform);
    set_fastcall(&self->on_error, on_error);
    set_fastcall(&self->error_transform, error_transform);
    for (Py_ssize_t i = 0; i < prefix; i++) {
        set_fastcall(&self->arg_transforms[i], PyTuple_GET_ITEM(args, i + 1));
    }
    self->vectorcall = (vectorcallfunc)vectorcall;
    return (PyObject *)self;
}

static PyMemberDef members[] = {
    {"function", T_OBJECT, OFFSET_OF_MEMBER(TransformCall, function.callable), READONLY, "The wrapped function."},
    {"rest_transform", T_OBJECT, OFFSET_OF_MEMBER(TransformCall, rest_transform.callable), READONLY, "Rest argument transform."},
    {"result_transform", T_OBJECT, OFFSET_OF_MEMBER(TransformCall, result_transform.callable), READONLY, "Result transform."},
    {"on_error", T_OBJECT, OFFSET_OF_MEMBER(TransformCall, on_error.callable), READONLY, "Error side-effect callback."},
    {"error_transform", T_OBJECT, OFFSET_OF_MEMBER(TransformCall, error_transform.callable), READONLY, "Error transform."},
    {nullptr}
};

PyTypeObject TransformCall_Type = {
    .ob_base = PyVarObject_HEAD_INIT(nullptr, 0)
    .tp_name = MODULE "transform_call",
    .tp_basicsize = OFFSET_OF_MEMBER(TransformCall, arg_transforms),
    .tp_itemsize = sizeof(retracesoftware::FastCall),
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(TransformCall, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_getattro = (getattrofunc)getattro,
    .tp_flags = Py_TPFLAGS_DEFAULT |
                Py_TPFLAGS_HAVE_GC |
                Py_TPFLAGS_HAVE_VECTORCALL |
                Py_TPFLAGS_METHOD_DESCRIPTOR,
    .tp_doc = "transform_call(function, *arg_transforms, rest_transform, result_transform, on_error=None, error_transform=None)\n--\n\n"
               "Transform prefix args, rest args/kwargs, the result, and optional exception handling.",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    .tp_members = members,
    .tp_descr_get = descr_get,
    .tp_new = create,
};
