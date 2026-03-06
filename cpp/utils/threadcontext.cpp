#include "utils.h"

namespace retracesoftware {

// ─── ThreadContextWrapper: wraps fn with collected context managers ──

struct ThreadContextWrapper : public PyObject {
    PyObject *fn;
    PyObject *cms;  // Python list of context managers
};

extern PyTypeObject ThreadContextWrapper_Type;

static void ThreadContextWrapper_dealloc(PyObject *self) {
    auto *w = (ThreadContextWrapper *)self;
    Py_XDECREF(w->fn);
    Py_XDECREF(w->cms);
    Py_TYPE(self)->tp_free(self);
}

static PyObject *ThreadContextWrapper_call(PyObject *self, PyObject *args, PyObject *kw) {
    auto *w = (ThreadContextWrapper *)self;
    Py_ssize_t n = PyList_GET_SIZE(w->cms);
    Py_ssize_t entered = 0;
    PyObject *result = nullptr;

    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *cm = PyList_GET_ITEM(w->cms, i);
        PyObject *enter = PyObject_GetAttrString(cm, "__enter__");
        if (!enter) goto exit_phase;
        PyObject *val = PyObject_CallNoArgs(enter);
        Py_DECREF(enter);
        if (!val) goto exit_phase;
        Py_DECREF(val);
        entered++;
    }

    result = PyObject_Call(w->fn, args, kw);

exit_phase:
    {
        PyObject *exc_type = nullptr, *exc_val = nullptr, *exc_tb = nullptr;
        bool has_error = (result == nullptr);

        if (has_error && PyErr_Occurred()) {
            PyErr_Fetch(&exc_type, &exc_val, &exc_tb);
            PyErr_NormalizeException(&exc_type, &exc_val, &exc_tb);
        }

        for (Py_ssize_t i = entered - 1; i >= 0; i--) {
            PyObject *cm = PyList_GET_ITEM(w->cms, i);
            PyObject *exit_fn = PyObject_GetAttrString(cm, "__exit__");
            if (!exit_fn) continue;

            PyObject *exit_args[] = {
                exc_type ? exc_type : Py_None,
                exc_val  ? exc_val  : Py_None,
                exc_tb   ? exc_tb   : Py_None
            };
            PyObject *exit_result = PyObject_Vectorcall(
                exit_fn, exit_args, 3, nullptr);
            Py_DECREF(exit_fn);

            if (exit_result) {
                if (has_error && PyObject_IsTrue(exit_result)) {
                    has_error = false;
                    Py_CLEAR(exc_type);
                    Py_CLEAR(exc_val);
                    Py_CLEAR(exc_tb);
                    result = Py_NewRef(Py_None);
                }
                Py_DECREF(exit_result);
            } else if (!has_error) {
                has_error = true;
                Py_CLEAR(result);
                PyErr_Fetch(&exc_type, &exc_val, &exc_tb);
                PyErr_NormalizeException(&exc_type, &exc_val, &exc_tb);
            }
        }

        if (has_error) {
            Py_XDECREF(result);
            if (exc_type) PyErr_Restore(exc_type, exc_val, exc_tb);
            return nullptr;
        }

        Py_XDECREF(exc_type);
        Py_XDECREF(exc_val);
        Py_XDECREF(exc_tb);
    }

    return result;
}

PyTypeObject ThreadContextWrapper_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "_ThreadContextWrapper",
    .tp_basicsize = sizeof(ThreadContextWrapper),
    .tp_dealloc = ThreadContextWrapper_dealloc,
    .tp_call = ThreadContextWrapper_call,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_DISALLOW_INSTANTIATION,
};

// ─── start_new_thread_wrapper(original, middleware, fn, args[, kwargs]) ─
//
// Called via functional.partial with original and middleware pre-bound.
// The partial makes this look like start_new_thread(fn, args[, kwargs])
// to callers.

PyObject *start_new_thread_wrapper(PyObject *module,
                                   PyObject *const *args,
                                   Py_ssize_t nargs) {
    if (nargs < 4 || nargs > 5) {
        PyErr_SetString(PyExc_TypeError,
            "start_new_thread_wrapper requires 4 or 5 arguments");
        return nullptr;
    }

    PyObject *original   = args[0];
    PyObject *middleware  = args[1];
    PyObject *fn         = args[2];
    PyObject *fn_args    = args[3];
    PyObject *fn_kwargs  = nargs > 4 ? args[4] : nullptr;

    Py_ssize_t nhooks = PyList_GET_SIZE(middleware);

    if (nhooks == 0) {
        if (fn_kwargs)
            return PyObject_CallFunctionObjArgs(
                original, fn, fn_args, fn_kwargs, nullptr);
        return PyObject_CallFunctionObjArgs(original, fn, fn_args, nullptr);
    }

    PyObject *cms = PyList_New(0);
    if (!cms) return nullptr;

    PyObject *factory_args[] = {
        fn,
        fn_args,
        fn_kwargs ? fn_kwargs : Py_None
    };

    for (Py_ssize_t i = 0; i < nhooks; i++) {
        PyObject *factory = PyList_GET_ITEM(middleware, i);
        PyObject *cm = PyObject_Vectorcall(factory, factory_args, 3, nullptr);
        if (!cm) { Py_DECREF(cms); return nullptr; }
        if (cm != Py_None)
            PyList_Append(cms, cm);
        Py_DECREF(cm);
    }

    if (PyList_GET_SIZE(cms) == 0) {
        Py_DECREF(cms);
        if (fn_kwargs)
            return PyObject_CallFunctionObjArgs(
                original, fn, fn_args, fn_kwargs, nullptr);
        return PyObject_CallFunctionObjArgs(original, fn, fn_args, nullptr);
    }

    auto *wrapper = PyObject_New(ThreadContextWrapper, &ThreadContextWrapper_Type);
    if (!wrapper) { Py_DECREF(cms); return nullptr; }
    wrapper->fn  = Py_NewRef(fn);
    wrapper->cms = cms;

    PyObject *result;
    if (fn_kwargs)
        result = PyObject_CallFunctionObjArgs(
            original, (PyObject *)wrapper, fn_args, fn_kwargs, nullptr);
    else
        result = PyObject_CallFunctionObjArgs(
            original, (PyObject *)wrapper, fn_args, nullptr);
    Py_DECREF(wrapper);
    return result;
}

// ─── Init ───────────────────────────────────────────────────────────

bool threadcontext_init(PyObject *module) {
    if (PyType_Ready(&ThreadContextWrapper_Type) < 0) return false;
    return true;
}

} // namespace retracesoftware
