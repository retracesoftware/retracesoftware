#include "utils.h"

namespace retracesoftware {

extern PyTypeObject ThreadLocal_Type;
extern PyTypeObject ThreadLocalContext_Type;

// ─── ThreadLocal ────────────────────────────────────────────────────

struct ThreadLocal : public PyObject {
    PyObject *dflt;
};

static int ThreadLocal_init(PyObject *self, PyObject *args, PyObject *kwargs) {
    static char *kwlist[] = {(char *)"default", nullptr};
    PyObject *dflt = Py_None;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|O", kwlist, &dflt))
        return -1;
    auto *tl = (ThreadLocal *)self;
    Py_XDECREF(tl->dflt);
    tl->dflt = Py_NewRef(dflt);
    return 0;
}

static void ThreadLocal_dealloc(PyObject *self) {
    Py_XDECREF(((ThreadLocal *)self)->dflt);
    Py_TYPE(self)->tp_free(self);
}

static PyObject *ThreadLocal_set(PyObject *self, PyObject *value) {
    PyObject *dict = PyThreadState_GetDict();
    if (!dict) {
        PyErr_SetString(PyExc_RuntimeError, "no current thread state");
        return nullptr;
    }
    if (PyDict_SetItem(dict, self, value) < 0)
        return nullptr;
    Py_RETURN_NONE;
}

static PyObject *ThreadLocal_get(PyObject *self, PyObject *args, PyObject *kwargs) {
    static char *kwlist[] = {(char *)"default", nullptr};
    auto *tl = (ThreadLocal *)self;
    PyObject *dflt = nullptr;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|O", kwlist, &dflt))
        return nullptr;
    if (!dflt) dflt = tl->dflt;

    PyObject *dict = PyThreadState_GetDict();
    if (!dict)
        return Py_NewRef(dflt);

    PyObject *value = PyDict_GetItemWithError(dict, self);
    if (value)
        return Py_NewRef(value);
    if (PyErr_Occurred())
        return nullptr;
    return Py_NewRef(dflt);
}

static PyObject *ThreadLocal_update(PyObject *self, PyObject *args, PyObject *kwargs) {
    Py_ssize_t nargs = PyTuple_GET_SIZE(args);
    if (nargs < 1) {
        PyErr_SetString(PyExc_TypeError, "update() requires at least one argument (update_fn)");
        return nullptr;
    }

    PyObject *dict = PyThreadState_GetDict();
    if (!dict) {
        PyErr_SetString(PyExc_RuntimeError, "no current thread state");
        return nullptr;
    }

    auto *tl = (ThreadLocal *)self;
    PyObject *old = PyDict_GetItemWithError(dict, self);  // borrowed
    if (!old) {
        if (PyErr_Occurred()) return nullptr;
        old = tl->dflt;
    }

    PyObject *fn = PyTuple_GET_ITEM(args, 0);
    PyObject *inner_args = PyTuple_GetSlice(args, 1, nargs);
    if (!inner_args) return nullptr;

    PyObject *call_args = PyTuple_New(PyTuple_GET_SIZE(inner_args) + 1);
    if (!call_args) { Py_DECREF(inner_args); return nullptr; }
    PyTuple_SET_ITEM(call_args, 0, Py_NewRef(old));
    for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(inner_args); i++)
        PyTuple_SET_ITEM(call_args, i + 1, Py_NewRef(PyTuple_GET_ITEM(inner_args, i)));
    Py_DECREF(inner_args);

    PyObject *new_val = PyObject_Call(fn, call_args, kwargs);
    Py_DECREF(call_args);
    if (!new_val) return nullptr;

    if (PyDict_SetItem(dict, self, new_val) < 0) {
        Py_DECREF(new_val);
        return nullptr;
    }
    Py_DECREF(new_val);

    return Py_NewRef(old);
}

// ─── ThreadLocalContext ─────────────────────────────────────────────

struct ThreadLocalContext : public PyObject {
    PyObject *tl;
    PyObject *value;
    PyObject *saved;
    bool had_value;
};

static void ThreadLocalContext_dealloc(PyObject *self) {
    auto *ctx = (ThreadLocalContext *)self;
    Py_XDECREF(ctx->tl);
    Py_XDECREF(ctx->value);
    Py_XDECREF(ctx->saved);
    Py_TYPE(self)->tp_free(self);
}

static PyObject *ThreadLocalContext_enter(PyObject *self, PyObject *) {
    auto *ctx = (ThreadLocalContext *)self;

    PyObject *dict = PyThreadState_GetDict();
    if (!dict) {
        PyErr_SetString(PyExc_RuntimeError, "no current thread state");
        return nullptr;
    }

    PyObject *prev = PyDict_GetItemWithError(dict, ctx->tl);
    if (prev) {
        ctx->saved = Py_NewRef(prev);
        ctx->had_value = true;
    } else if (PyErr_Occurred()) {
        return nullptr;
    } else {
        ctx->saved = nullptr;
        ctx->had_value = false;
    }

    if (PyDict_SetItem(dict, ctx->tl, ctx->value) < 0)
        return nullptr;

    return Py_NewRef(self);
}

static PyObject *ThreadLocalContext_exit(PyObject *self, PyObject *args) {
    auto *ctx = (ThreadLocalContext *)self;

    PyObject *dict = PyThreadState_GetDict();
    if (!dict) {
        PyErr_SetString(PyExc_RuntimeError, "no current thread state");
        return nullptr;
    }

    if (ctx->had_value) {
        if (PyDict_SetItem(dict, ctx->tl, ctx->saved) < 0)
            return nullptr;
    } else {
        if (PyDict_DelItem(dict, ctx->tl) < 0) {
            if (PyErr_ExceptionMatches(PyExc_KeyError))
                PyErr_Clear();
            else
                return nullptr;
        }
    }

    Py_RETURN_FALSE;
}

static PyMethodDef ThreadLocalContext_methods[] = {
    {"__enter__", (PyCFunction)ThreadLocalContext_enter, METH_NOARGS, nullptr},
    {"__exit__",  (PyCFunction)ThreadLocalContext_exit,  METH_VARARGS, nullptr},
    {nullptr}
};

PyTypeObject ThreadLocalContext_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "._ThreadLocalContext",
    .tp_basicsize = sizeof(ThreadLocalContext),
    .tp_dealloc = ThreadLocalContext_dealloc,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .tp_methods = ThreadLocalContext_methods,
};

// ─── ThreadLocal.context(value) ─────────────────────────────────────

static PyObject *ThreadLocal_context(PyObject *self, PyObject *value) {
    auto *ctx = PyObject_New(ThreadLocalContext, &ThreadLocalContext_Type);
    if (!ctx) return nullptr;
    ctx->tl = Py_NewRef(self);
    ctx->value = Py_NewRef(value);
    ctx->saved = nullptr;
    ctx->had_value = false;
    return (PyObject *)ctx;
}

static PyMethodDef ThreadLocal_methods[] = {
    {"set",     (PyCFunction)ThreadLocal_set,     METH_O,                        nullptr},
    {"get",     (PyCFunction)ThreadLocal_get,     METH_VARARGS | METH_KEYWORDS,  nullptr},
    {"update",  (PyCFunction)ThreadLocal_update,  METH_VARARGS | METH_KEYWORDS,  nullptr},
    {"context", (PyCFunction)ThreadLocal_context, METH_O,                        nullptr},
    {nullptr}
};

PyTypeObject ThreadLocal_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE ".ThreadLocal",
    .tp_basicsize = sizeof(ThreadLocal),
    .tp_dealloc = ThreadLocal_dealloc,
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_methods = ThreadLocal_methods,
    .tp_init = ThreadLocal_init,
    .tp_new = PyType_GenericNew,
};

} // namespace retracesoftware
