#include "module.h"
#include <algorithm>

thread_local ThreadCallCounts *tc = nullptr;

static struct {
    PyThreadState *tstate = nullptr;
    PyObject *owner = nullptr;
    ThreadCallCounts *tc = nullptr;
} thread_local tc_cache;

static ThreadCallCounts *create_thread_call_counts()
{
    auto *obj = PyObject_New(ThreadCallCounts, &ThreadCallCounts_Type);
    if (obj) {
        new (&obj->cursor_stack) std::vector<CursorEntry>();
        new (&obj->watches) std::vector<WatchState>();
        new (&obj->pending_watches) std::vector<WatchState>();
        obj->suspend_depth = 0;
        obj->check_watches_depth = 0;
        obj->root_parent_frame = nullptr;
        obj->root_parent_lasti = -1;
        obj->root_repeat_count = 0;
        obj->root_parent_valid = false;
        obj->suspended_frame = nullptr;
        obj->context_depth = -1;
    }
    return obj;
}

ThreadCallCounts *get_tc(PyObject *owner)
{
    PyThreadState *tstate = PyThreadState_Get();
    if (tc_cache.tstate == tstate && tc_cache.owner == owner) {
        tc = tc_cache.tc;
        return tc;
    }

    PyObject *dict = PyThreadState_GetDict();
    if (!dict) {
        tc = create_thread_call_counts();
        return tc;
    }

    PyObject *existing = PyDict_GetItem(dict, owner);
    if (existing) {
        tc = (ThreadCallCounts *)existing;
    } else {
        tc = create_thread_call_counts();
        if (tc) {
            PyDict_SetItem(dict, owner, (PyObject *)tc);
            Py_DECREF(tc);
        }
    }

    tc_cache.tstate = tstate;
    tc_cache.owner = owner;
    tc_cache.tc = tc;
    return tc;
}

void invalidate_tc_cache(PyObject *owner)
{
    if (tc_cache.owner == owner) {
        tc_cache = {};
        tc = nullptr;
    }
}

// ---------------------------------------------------------------------------
// ThreadCallCounts dealloc
// ---------------------------------------------------------------------------

static void ThreadCallCounts_dealloc(ThreadCallCounts *self)
{
    self->pending_watches.~vector();
    self->watches.~vector();
    self->cursor_stack.~vector();
    Py_TYPE(self)->tp_free((PyObject *)self);
}

// ---------------------------------------------------------------------------
// ThreadCallCounts methods
// ---------------------------------------------------------------------------

static PyObject *
tc_current(ThreadCallCounts *self, PyObject *Py_UNUSED(ignored))
{
    tc = self;
    return build_current_cursor();
}

static PyObject *
tc_frame_positions(ThreadCallCounts *self, PyObject *Py_UNUSED(ignored))
{
    tc = self;
    return build_frame_positions();
}

static PyObject *
tc_position(ThreadCallCounts *self, PyObject *Py_UNUSED(ignored))
{
    tc = self;
    PyObject *counts = build_current_cursor();
    if (!counts) return nullptr;
    PyObject *lastis = build_frame_positions();
    if (!lastis) { Py_DECREF(counts); return nullptr; }

    Py_ssize_t n = PyTuple_GET_SIZE(counts);
    PyObject *result = PyTuple_New(n);
    if (!result) { Py_DECREF(counts); Py_DECREF(lastis); return nullptr; }

    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *pair = PyTuple_Pack(2,
            PyTuple_GET_ITEM(counts, i),
            PyTuple_GET_ITEM(lastis, i));
        if (!pair) {
            Py_DECREF(result);
            Py_DECREF(counts);
            Py_DECREF(lastis);
            return nullptr;
        }
        PyTuple_SET_ITEM(result, i, pair);
    }

    Py_DECREF(counts);
    Py_DECREF(lastis);
    return result;
}

static PyObject *
tc_add_watch(ThreadCallCounts *self,
             PyObject *const *args, Py_ssize_t nargs,
             PyObject *kwnames)
{
    tc = self;
    if (nargs != 1) {
        PyErr_SetString(PyExc_TypeError,
            "add_watch() requires exactly 1 positional argument: call_counts");
        return nullptr;
    }

    PyObject *counts = args[0];
    if (!PyTuple_Check(counts)) {
        PyErr_SetString(PyExc_TypeError, "call_counts must be a tuple of ints");
        return nullptr;
    }

    PyObject *kw_start     = nullptr;
    PyObject *kw_return    = nullptr;
    PyObject *kw_unwind    = nullptr;
    PyObject *kw_backjump  = nullptr;
    PyObject *kw_overshoot = nullptr;

    if (kwnames) {
        Py_ssize_t nkw = PyTuple_GET_SIZE(kwnames);
        for (Py_ssize_t i = 0; i < nkw; i++) {
            PyObject *key = PyTuple_GET_ITEM(kwnames, i);
            PyObject *val = args[nargs + i];
            if (PyUnicode_CompareWithASCIIString(key, "on_start") == 0)
                kw_start = val;
            else if (PyUnicode_CompareWithASCIIString(key, "on_return") == 0)
                kw_return = val;
            else if (PyUnicode_CompareWithASCIIString(key, "on_unwind") == 0)
                kw_unwind = val;
            else if (PyUnicode_CompareWithASCIIString(key, "on_backjump") == 0)
                kw_backjump = val;
            else if (PyUnicode_CompareWithASCIIString(key, "on_overshoot") == 0)
                kw_overshoot = val;
            else if (PyUnicode_CompareWithASCIIString(key, "on_missed") == 0)
                kw_overshoot = val;
            else {
                PyErr_Format(PyExc_TypeError,
                    "add_watch() got unexpected keyword argument '%U'", key);
                return nullptr;
            }
        }
    }

    #define NORMALIZE_CB(var, name) \
        if (var && var == Py_None) { var = nullptr; } \
        if (var && !PyCallable_Check(var)) { \
            PyErr_SetString(PyExc_TypeError, name " must be callable"); \
            return nullptr; \
        }
    NORMALIZE_CB(kw_start,     "on_start")
    NORMALIZE_CB(kw_return,    "on_return")
    NORMALIZE_CB(kw_unwind,    "on_unwind")
    NORMALIZE_CB(kw_backjump,  "on_backjump")
    NORMALIZE_CB(kw_overshoot, "on_overshoot")
    #undef NORMALIZE_CB

    std::vector<int> target;
    target.reserve((size_t)PyTuple_GET_SIZE(counts));
    for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(counts); i++) {
        long value = PyLong_AsLong(PyTuple_GET_ITEM(counts, i));
        if (value == -1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_TypeError, "call_counts must be a tuple of ints");
            return nullptr;
        }
        target.push_back((int)value);
    }

    if (tc->check_watches_depth > 0) {
        tc->pending_watches.emplace_back(std::move(target),
                                kw_start, kw_return, kw_unwind,
                                kw_backjump, kw_overshoot);
    } else {
        tc->watches.emplace_back(std::move(target),
                                kw_start, kw_return, kw_unwind,
                                kw_backjump, kw_overshoot);
        if (kw_start) check_watches(WatchSlot::start);
    }
    Py_RETURN_NONE;
}

static PyObject *
tc_discard_watches(ThreadCallCounts *self, PyObject *Py_UNUSED(ignored))
{
    for (auto &w : self->watches) {
        w.detach();
    }
    self->watches.clear();
    for (auto &w : self->pending_watches) {
        w.detach();
    }
    self->pending_watches.clear();
    Py_RETURN_NONE;
}

static PyObject *
tc_reset_stack(ThreadCallCounts *self, PyObject *Py_UNUSED(ignored))
{
    self->cursor_stack.clear();
    self->root_parent_valid = false;
    self->root_parent_frame = nullptr;
    self->root_parent_lasti = -1;
    self->root_repeat_count = 0;
    Py_RETURN_NONE;
}

// ---------------------------------------------------------------------------
// Context manager: __enter__ / __exit__
// ---------------------------------------------------------------------------

static PyObject *
tc_enter(ThreadCallCounts *self, PyObject *Py_UNUSED(ignored))
{
    self->context_depth = (Py_ssize_t)self->cursor_stack.size();
    self->cursor_stack.clear();
    self->root_parent_valid = false;
    self->root_parent_frame = nullptr;
    self->root_parent_lasti = -1;
    self->root_repeat_count = 0;
    return Py_NewRef((PyObject *)self);
}

static PyObject *
tc_exit(ThreadCallCounts *self, PyObject *const *args, Py_ssize_t nargs)
{
    for (auto &w : self->watches) {
        w.fire_overshoot();
    }
    for (auto &w : self->pending_watches) {
        w.fire_overshoot();
    }
    self->cursor_stack.clear();
    self->watches.clear();
    self->pending_watches.clear();
    self->root_parent_valid = false;
    self->root_parent_frame = nullptr;
    self->root_parent_lasti = -1;
    self->root_repeat_count = 0;
    self->context_depth = -1;
    Py_RETURN_NONE;
}

// ---------------------------------------------------------------------------
// Method table
// ---------------------------------------------------------------------------

static PyMethodDef ThreadCallCounts_methods[] = {
    {"current",          (PyCFunction)tc_current,          METH_NOARGS,
     "Return the current call counts as a tuple of ints."},
    {"frame_positions",  (PyCFunction)tc_frame_positions,  METH_NOARGS,
     "Return a tuple of f_lasti ints aligned to the call-count stack."},
    {"position",         (PyCFunction)tc_position,         METH_NOARGS,
     "Return tuple of (call_count, f_lasti) pairs."},
    {"add_watch",        (PyCFunction)(void(*)(void))tc_add_watch,
                         METH_FASTCALL | METH_KEYWORDS,
                         "add_watch(call_counts, *, on_start=None, on_return=None, "
                         "on_unwind=None, on_backjump=None, on_overshoot=None)\n"
                         "Add a one-shot watch for a target call-counts position."},
    {"reset_stack",      (PyCFunction)tc_reset_stack,      METH_NOARGS,
     "Clear the cursor stack and reset root tracking."},
    {"discard_watches",  (PyCFunction)tc_discard_watches,  METH_NOARGS,
     "Discard all watches without firing callbacks (fork-safe)."},
    {"__enter__",        (PyCFunction)tc_enter,            METH_NOARGS,  nullptr},
    {"__exit__",         (PyCFunction)tc_exit,             METH_FASTCALL, nullptr},
    {nullptr}
};

// ---------------------------------------------------------------------------
// Type object
// ---------------------------------------------------------------------------

PyTypeObject ThreadCallCounts_Type = {
    .ob_base = PyVarObject_HEAD_INIT(nullptr, 0)
    .tp_name = MODULE "ThreadCallCounts",
    .tp_basicsize = sizeof(ThreadCallCounts),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)ThreadCallCounts_dealloc,
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_doc = "Per-thread call-count state.\n"
              "\n"
              "Returned by CallCounter() for the current thread.\n"
              "Use as a context manager to scope cursor tracking.",
    .tp_methods = ThreadCallCounts_methods,
};
