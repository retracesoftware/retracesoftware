#include "utils.h"
#include <new>

namespace retracesoftware {

extern PyTypeObject ThreadLocal_Type;
extern PyTypeObject ThreadLocalContext_Type;
extern PyTypeObject ThreadLocalIfThenElse_Type;
extern PyTypeObject ThreadLocalCond_Type;
extern PyTypeObject ThreadLocalApplyWith_Type;

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

// ─── ThreadLocal.if_then_else(expected, then, else_) ────────────────

struct ThreadLocalIfThenElse : public PyObject {
    vectorcallfunc vectorcall;
    PyObject *tl;
    PyObject *expected;
    retracesoftware::FastCall then_branch;
    retracesoftware::FastCall else_branch;
};

static int ThreadLocalIfThenElse_traverse(ThreadLocalIfThenElse *self, visitproc visit, void *arg) {
    Py_VISIT(self->tl);
    Py_VISIT(self->expected);
    Py_VISIT(self->then_branch.callable);
    Py_VISIT(self->else_branch.callable);
    return 0;
}

static int ThreadLocalIfThenElse_clear(ThreadLocalIfThenElse *self) {
    Py_CLEAR(self->tl);
    Py_CLEAR(self->expected);
    Py_CLEAR(self->then_branch.callable);
    Py_CLEAR(self->else_branch.callable);
    return 0;
}

static void ThreadLocalIfThenElse_dealloc(PyObject *self_obj) {
    auto *self = (ThreadLocalIfThenElse *)self_obj;
    PyObject_GC_UnTrack(self_obj);
    ThreadLocalIfThenElse_clear(self);
    Py_TYPE(self_obj)->tp_free(self_obj);
}

static PyObject *ThreadLocalIfThenElse_descr_get(PyObject *self, PyObject *obj, PyObject *type) {
    return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
}

static PyObject *ThreadLocalIfThenElse_on_then_get(ThreadLocalIfThenElse *self, void *) {
    return self->then_branch.callable ? Py_NewRef(self->then_branch.callable) : Py_NewRef(Py_None);
}

static int ThreadLocalIfThenElse_on_then_set(ThreadLocalIfThenElse *self, PyObject *value, void *) {
    if (value == nullptr || !PyCallable_Check(value)) {
        PyErr_SetString(PyExc_TypeError, "on_then must be callable");
        return -1;
    }

    Py_INCREF(value);
    PyObject *old = self->then_branch.callable;
    self->then_branch = retracesoftware::FastCall(value);
    Py_XDECREF(old);
    return 0;
}

static PyObject *ThreadLocalIfThenElse_on_else_get(ThreadLocalIfThenElse *self, void *) {
    return self->else_branch.callable ? Py_NewRef(self->else_branch.callable) : Py_NewRef(Py_None);
}

static int ThreadLocalIfThenElse_on_else_set(ThreadLocalIfThenElse *self, PyObject *value, void *) {
    if (value == nullptr || !PyCallable_Check(value)) {
        PyErr_SetString(PyExc_TypeError, "on_else must be callable");
        return -1;
    }

    Py_INCREF(value);
    PyObject *old = self->else_branch.callable;
    self->else_branch = retracesoftware::FastCall(value);
    Py_XDECREF(old);
    return 0;
}

static PyGetSetDef ThreadLocalIfThenElse_getset[] = {
    {"on_then", (getter)ThreadLocalIfThenElse_on_then_get, (setter)ThreadLocalIfThenElse_on_then_set, nullptr, nullptr},
    {"on_else", (getter)ThreadLocalIfThenElse_on_else_get, (setter)ThreadLocalIfThenElse_on_else_set, nullptr, nullptr},
    {nullptr}
};

static PyObject *ThreadLocalIfThenElse_call(PyObject *self_obj, PyObject *const *args, size_t nargsf, PyObject *kwnames) {
    auto *self = (ThreadLocalIfThenElse *)self_obj;
    auto *tl = (ThreadLocal *)self->tl;

    PyObject *current = tl->dflt;
    PyObject *dict = PyThreadState_GetDict();
    if (dict) {
        PyObject *value = PyDict_GetItemWithError(dict, self->tl);
        if (value) {
            current = value;
        } else if (PyErr_Occurred()) {
            return nullptr;
        }
    }

    int matches = current == self->expected ? 1 : PyObject_RichCompareBool(current, self->expected, Py_EQ);
    if (matches < 0) {
        return nullptr;
    }

    return matches
        ? self->then_branch(args, nargsf, kwnames)
        : self->else_branch(args, nargsf, kwnames);
}

PyTypeObject ThreadLocalIfThenElse_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "._ThreadLocalIfThenElse",
    .tp_basicsize = sizeof(ThreadLocalIfThenElse),
    .tp_dealloc = ThreadLocalIfThenElse_dealloc,
    .tp_vectorcall_offset = offsetof(ThreadLocalIfThenElse, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT |
                Py_TPFLAGS_HAVE_GC |
                Py_TPFLAGS_HAVE_VECTORCALL |
                Py_TPFLAGS_METHOD_DESCRIPTOR |
                Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .tp_traverse = (traverseproc)ThreadLocalIfThenElse_traverse,
    .tp_clear = (inquiry)ThreadLocalIfThenElse_clear,
    .tp_getset = ThreadLocalIfThenElse_getset,
    .tp_descr_get = ThreadLocalIfThenElse_descr_get,
};

static PyObject *ThreadLocal_if_then_else(PyObject *self, PyObject *args, PyObject *kwargs) {
    static char *kwlist[] = {(char *)"expected", (char *)"then_branch", (char *)"else_branch", nullptr};
    PyObject *expected = nullptr;
    PyObject *then_branch = nullptr;
    PyObject *else_branch = nullptr;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OOO", kwlist, &expected, &then_branch, &else_branch)) {
        return nullptr;
    }

    if (!PyCallable_Check(then_branch)) {
        PyErr_SetString(PyExc_TypeError, "if_then_else() then_branch must be callable");
        return nullptr;
    }
    if (!PyCallable_Check(else_branch)) {
        PyErr_SetString(PyExc_TypeError, "if_then_else() else_branch must be callable");
        return nullptr;
    }

    auto *result = PyObject_GC_New(ThreadLocalIfThenElse, &ThreadLocalIfThenElse_Type);
    if (!result) {
        return nullptr;
    }

    result->vectorcall = (vectorcallfunc)ThreadLocalIfThenElse_call;
    result->tl = Py_NewRef(self);
    result->expected = Py_NewRef(expected);
    new (&result->then_branch) retracesoftware::FastCall(Py_NewRef(then_branch));
    new (&result->else_branch) retracesoftware::FastCall(Py_NewRef(else_branch));
    PyObject_GC_Track((PyObject *)result);
    return (PyObject *)result;
}

// ─── ThreadLocal.cond(expected1, action1, ..., else_action) ──────────

struct ThreadLocalCond : public PyVarObject {
    vectorcallfunc vectorcall;
    PyObject *tl;
    PyObject *expecteds;
    retracesoftware::FastCall branches[];
};

static int ThreadLocalCond_traverse(ThreadLocalCond *self, visitproc visit, void *arg) {
    Py_VISIT(self->tl);
    Py_VISIT(self->expecteds);
    for (Py_ssize_t i = 0; i < Py_SIZE(self); i++) {
        Py_VISIT(self->branches[i].callable);
    }
    return 0;
}

static int ThreadLocalCond_clear(ThreadLocalCond *self) {
    Py_CLEAR(self->tl);
    Py_CLEAR(self->expecteds);
    for (Py_ssize_t i = 0; i < Py_SIZE(self); i++) {
        Py_CLEAR(self->branches[i].callable);
    }
    return 0;
}

static void ThreadLocalCond_dealloc(PyObject *self_obj) {
    auto *self = (ThreadLocalCond *)self_obj;
    PyObject_GC_UnTrack(self_obj);
    ThreadLocalCond_clear(self);
    Py_TYPE(self_obj)->tp_free(self_obj);
}

static PyObject *ThreadLocalCond_descr_get(PyObject *self, PyObject *obj, PyObject *type) {
    return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
}

static PyObject *ThreadLocalCond_call(PyObject *self_obj, PyObject *const *args, size_t nargsf, PyObject *kwnames) {
    auto *self = (ThreadLocalCond *)self_obj;
    auto *tl = (ThreadLocal *)self->tl;

    PyObject *current = tl->dflt;
    PyObject *dict = PyThreadState_GetDict();
    if (dict) {
        PyObject *value = PyDict_GetItemWithError(dict, self->tl);
        if (value) {
            current = value;
        } else if (PyErr_Occurred()) {
            return nullptr;
        }
    }

    Py_ssize_t branch_count = PyTuple_GET_SIZE(self->expecteds);
    for (Py_ssize_t i = 0; i < branch_count; i++) {
        PyObject *expected = PyTuple_GET_ITEM(self->expecteds, i);
        int matches = current == expected ? 1 : PyObject_RichCompareBool(current, expected, Py_EQ);
        if (matches < 0) {
            return nullptr;
        }
        if (matches) {
            return self->branches[i](args, nargsf, kwnames);
        }
    }

    return self->branches[branch_count](args, nargsf, kwnames);
}

PyTypeObject ThreadLocalCond_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "._ThreadLocalCond",
    .tp_basicsize = sizeof(ThreadLocalCond),
    .tp_itemsize = sizeof(retracesoftware::FastCall),
    .tp_dealloc = ThreadLocalCond_dealloc,
    .tp_vectorcall_offset = offsetof(ThreadLocalCond, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT |
                Py_TPFLAGS_HAVE_GC |
                Py_TPFLAGS_HAVE_VECTORCALL |
                Py_TPFLAGS_METHOD_DESCRIPTOR |
                Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .tp_traverse = (traverseproc)ThreadLocalCond_traverse,
    .tp_clear = (inquiry)ThreadLocalCond_clear,
    .tp_descr_get = ThreadLocalCond_descr_get,
};

static PyObject *ThreadLocal_cond(PyObject *self, PyObject *args) {
    Py_ssize_t nargs = PyTuple_GET_SIZE(args);
    if (nargs < 3 || nargs % 2 == 0) {
        PyErr_SetString(PyExc_TypeError, "cond() requires expected/action pairs followed by a final else action");
        return nullptr;
    }

    Py_ssize_t branch_count = (nargs - 1) / 2;
    PyObject *else_branch = PyTuple_GET_ITEM(args, nargs - 1);
    if (!PyCallable_Check(else_branch)) {
        PyErr_SetString(PyExc_TypeError, "cond() else action must be callable");
        return nullptr;
    }

    auto *result = PyObject_GC_NewVar(ThreadLocalCond, &ThreadLocalCond_Type, branch_count + 1);
    if (!result) {
        return nullptr;
    }

    result->vectorcall = (vectorcallfunc)ThreadLocalCond_call;
    result->tl = nullptr;
    result->expecteds = nullptr;
    for (Py_ssize_t i = 0; i < branch_count + 1; i++) {
        result->branches[i] = retracesoftware::FastCall();
    }

    result->tl = Py_NewRef(self);
    result->expecteds = PyTuple_New(branch_count);
    if (!result->expecteds) {
        Py_DECREF((PyObject *)result);
        return nullptr;
    }

    for (Py_ssize_t i = 0; i < branch_count; i++) {
        PyObject *expected = PyTuple_GET_ITEM(args, i * 2);
        PyObject *branch = PyTuple_GET_ITEM(args, i * 2 + 1);
        if (!PyCallable_Check(branch)) {
            Py_DECREF((PyObject *)result);
            PyErr_SetString(PyExc_TypeError, "cond() branch actions must be callable");
            return nullptr;
        }

        PyTuple_SET_ITEM(result->expecteds, i, Py_NewRef(expected));
        new (&result->branches[i]) retracesoftware::FastCall(Py_NewRef(branch));
    }

    new (&result->branches[branch_count]) retracesoftware::FastCall(Py_NewRef(else_branch));
    PyObject_GC_Track((PyObject *)result);
    return (PyObject *)result;
}

// ─── ThreadLocal.apply_with(value, target) ───────────────────────────

struct ThreadLocalApplyWith : public PyObject {
    vectorcallfunc vectorcall;
    PyObject *tl;
    PyObject *value;
    retracesoftware::FastCall target;
};

static int ThreadLocalApplyWith_traverse(ThreadLocalApplyWith *self, visitproc visit, void *arg) {
    Py_VISIT(self->tl);
    Py_VISIT(self->value);
    Py_VISIT(self->target.callable);
    return 0;
}

static int ThreadLocalApplyWith_clear(ThreadLocalApplyWith *self) {
    Py_CLEAR(self->tl);
    Py_CLEAR(self->value);
    Py_CLEAR(self->target.callable);
    return 0;
}

static void ThreadLocalApplyWith_dealloc(PyObject *self_obj) {
    auto *self = (ThreadLocalApplyWith *)self_obj;
    PyObject_GC_UnTrack(self_obj);
    ThreadLocalApplyWith_clear(self);
    Py_TYPE(self_obj)->tp_free(self_obj);
}

static PyObject *ThreadLocalApplyWith_descr_get(PyObject *self, PyObject *obj, PyObject *type) {
    return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
}

static PyObject *ThreadLocalApplyWith_target_get(ThreadLocalApplyWith *self, void *) {
    return self->target.callable ? Py_NewRef(self->target.callable) : Py_NewRef(Py_None);
}

static int ThreadLocalApplyWith_target_set(ThreadLocalApplyWith *self, PyObject *value, void *) {
    if (value == nullptr || value == Py_None) {
        PyErr_SetString(PyExc_TypeError, "target must be callable");
        return -1;
    }
    if (!PyCallable_Check(value)) {
        PyErr_SetString(PyExc_TypeError, "target must be callable");
        return -1;
    }

    Py_INCREF(value);
    Py_XDECREF(self->target.callable);
    self->target = retracesoftware::FastCall(value);
    return 0;
}

static PyGetSetDef ThreadLocalApplyWith_getset[] = {
    {"target", (getter)ThreadLocalApplyWith_target_get, (setter)ThreadLocalApplyWith_target_set, nullptr, nullptr},
    {nullptr}
};

static PyObject *ThreadLocalApplyWith_call(PyObject *self_obj, PyObject *const *args, size_t nargsf, PyObject *kwnames) {
    auto *self = (ThreadLocalApplyWith *)self_obj;
    PyObject *dict = PyThreadState_GetDict();
    if (!dict) {
        PyErr_SetString(PyExc_RuntimeError, "no current thread state");
        return nullptr;
    }

    PyObject *prev = PyDict_GetItemWithError(dict, self->tl);
    bool had_value = prev != nullptr;
    if (had_value) {
        Py_INCREF(prev);
    } else if (PyErr_Occurred()) {
        return nullptr;
    }

    if (PyDict_SetItem(dict, self->tl, self->value) < 0) {
        Py_XDECREF(prev);
        return nullptr;
    }

    PyObject *result = self->target(args, nargsf, kwnames);
    PyObject *exc_type = nullptr;
    PyObject *exc_value = nullptr;
    PyObject *exc_tb = nullptr;
    if (!result) {
        PyErr_Fetch(&exc_type, &exc_value, &exc_tb);
    }

    int restore_status = 0;
    if (had_value) {
        restore_status = PyDict_SetItem(dict, self->tl, prev);
    } else {
        restore_status = PyDict_DelItem(dict, self->tl);
        if (restore_status < 0 && PyErr_ExceptionMatches(PyExc_KeyError)) {
            PyErr_Clear();
            restore_status = 0;
        }
    }
    Py_XDECREF(prev);

    if (!result) {
        if (restore_status == 0) {
            PyErr_Restore(exc_type, exc_value, exc_tb);
        } else {
            Py_XDECREF(exc_type);
            Py_XDECREF(exc_value);
            Py_XDECREF(exc_tb);
        }
        return nullptr;
    }

    if (restore_status < 0) {
        Py_DECREF(result);
        return nullptr;
    }

    return result;
}

PyTypeObject ThreadLocalApplyWith_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "._ThreadLocalApplyWith",
    .tp_basicsize = sizeof(ThreadLocalApplyWith),
    .tp_dealloc = ThreadLocalApplyWith_dealloc,
    .tp_vectorcall_offset = offsetof(ThreadLocalApplyWith, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT |
                Py_TPFLAGS_HAVE_GC |
                Py_TPFLAGS_HAVE_VECTORCALL |
                Py_TPFLAGS_METHOD_DESCRIPTOR |
                Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .tp_traverse = (traverseproc)ThreadLocalApplyWith_traverse,
    .tp_clear = (inquiry)ThreadLocalApplyWith_clear,
    .tp_descr_get = ThreadLocalApplyWith_descr_get,
    .tp_getset = ThreadLocalApplyWith_getset,
};

static PyObject *ThreadLocal_apply_with(PyObject *self, PyObject *args, PyObject *kwargs) {
    static char *kwlist[] = {(char *)"value", (char *)"target", nullptr};
    PyObject *value = nullptr;
    PyObject *target = nullptr;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OO", kwlist, &value, &target)) {
        return nullptr;
    }

    if (!PyCallable_Check(target)) {
        PyErr_SetString(PyExc_TypeError, "apply_with() target must be callable");
        return nullptr;
    }

    auto *result = PyObject_GC_New(ThreadLocalApplyWith, &ThreadLocalApplyWith_Type);
    if (!result) {
        return nullptr;
    }

    result->vectorcall = (vectorcallfunc)ThreadLocalApplyWith_call;
    result->tl = Py_NewRef(self);
    result->value = Py_NewRef(value);
    new (&result->target) retracesoftware::FastCall(Py_NewRef(target));
    PyObject_GC_Track((PyObject *)result);
    return (PyObject *)result;
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
    {"apply_with", (PyCFunction)ThreadLocal_apply_with, METH_VARARGS | METH_KEYWORDS, nullptr},
    {"cond", (PyCFunction)ThreadLocal_cond, METH_VARARGS, nullptr},
    {"context", (PyCFunction)ThreadLocal_context, METH_O,                        nullptr},
    {"if_then_else", (PyCFunction)ThreadLocal_if_then_else, METH_VARARGS | METH_KEYWORDS, nullptr},
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
