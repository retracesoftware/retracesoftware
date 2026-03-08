#include "module.h"
#include <algorithm>

void
check_watches(WatchSlot slot)
{
    if (tc->suspend_depth > 0) return;
    auto &ws = tc->watches;
    ws.erase(
        std::remove_if(ws.begin(), ws.end(), [&](WatchState &w) {
            return w(slot, tc->cursor_stack);
        }),
        ws.end());
}

// ---------------------------------------------------------------------------
// Thread-switch detection (forward declaration; defined after CallCounter)
// ---------------------------------------------------------------------------

struct CallCounter;
static void check_thread_switch(CallCounter *cc);

// ---------------------------------------------------------------------------
// sys.monitoring callbacks (module-level PyCFunctions for registration)
// ---------------------------------------------------------------------------

static PyObject *
on_py_start(PyObject *self, PyObject *const *args, Py_ssize_t nargs)
{
    get_tc(self);
    check_thread_switch((CallCounter *)self);
    if (tc->suspend_depth > 0) Py_RETURN_NONE;

    int new_count = 0;
    if (!tc->cursor_stack.empty()) {
        tc->cursor_stack.back().call_count++;
    } else {
        _PyInterpreterFrame *frame = PyThreadState_Get()->cframe->current_frame;
        _PyInterpreterFrame *parent = frame ? frame->previous : nullptr;
        if (parent) {
            int parent_lasti = _PyInterpreterFrame_LASTI(parent) * (int)sizeof(_Py_CODEUNIT);
            if (tc->root_parent_valid &&
                tc->root_parent_frame == (void *)parent &&
                tc->root_parent_lasti == parent_lasti) {
                tc->root_repeat_count++;
            } else {
                tc->root_repeat_count = 0;
            }
            tc->root_parent_frame = (void *)parent;
            tc->root_parent_lasti = parent_lasti;
            tc->root_parent_valid = true;
            new_count = tc->root_repeat_count;
        } else {
            tc->root_parent_valid = false;
            tc->root_parent_frame = nullptr;
            tc->root_parent_lasti = -1;
            tc->root_repeat_count = 0;
        }
    }
    tc->cursor_stack.push_back({new_count});
    check_watches(WatchSlot::start);
    Py_RETURN_NONE;
}

static PyObject *
on_py_return(PyObject *self, PyObject *const *args, Py_ssize_t nargs)
{
    get_tc(self);
    check_thread_switch((CallCounter *)self);
    if (tc->suspend_depth > 0) Py_RETURN_NONE;

    check_watches(WatchSlot::on_return);
    if (!tc->cursor_stack.empty()) {
        tc->cursor_stack.pop_back();
    }
    check_watches(WatchSlot::start);
    Py_RETURN_NONE;
}

static PyObject *
on_py_unwind(PyObject *self, PyObject *const *args, Py_ssize_t nargs)
{
    get_tc(self);
    check_thread_switch((CallCounter *)self);
    if (tc->suspend_depth > 0) Py_RETURN_NONE;

    check_watches(WatchSlot::unwind);
    if (!tc->cursor_stack.empty()) {
        tc->cursor_stack.pop_back();
    }
    check_watches(WatchSlot::start);
    Py_RETURN_NONE;
}

static PyObject *
on_py_jump(PyObject *self, PyObject *const *args, Py_ssize_t nargs)
{
    get_tc(self);
    check_thread_switch((CallCounter *)self);
    if (tc->suspend_depth > 0) Py_RETURN_NONE;
    if (nargs < 3) Py_RETURN_NONE;

    long src = PyLong_AsLong(args[1]);
    long dst = PyLong_AsLong(args[2]);
    if ((src == -1 || dst == -1) && PyErr_Occurred()) {
        PyErr_Clear();
        Py_RETURN_NONE;
    }
    if (dst < src) {
        check_watches(WatchSlot::backjump);
    }
    Py_RETURN_NONE;
}

// ---------------------------------------------------------------------------
// Frame position helpers
// ---------------------------------------------------------------------------

#if PY_VERSION_HEX >= 0x030C0000
static bool is_python_frame(_PyInterpreterFrame *frame) {
    if (frame->owner == FRAME_OWNED_BY_CSTACK) return false;
    PyObject *func = frame->f_funcobj;
    return func && !PyDict_Check(func);
}
#else
static bool is_python_frame(_PyInterpreterFrame *frame) {
    return frame->f_func != nullptr;
}
#endif

PyObject *
build_frame_positions()
{
    Py_ssize_t n = (Py_ssize_t)tc->cursor_stack.size();

    std::vector<int> frame_lastis;
    _PyInterpreterFrame *frame =
        (tc->suspend_depth > 0 && tc->suspended_frame)
            ? tc->suspended_frame
            : PyThreadState_Get()->cframe->current_frame;
    while (frame) {
        if (is_python_frame(frame)) {
            frame_lastis.push_back(
                _PyInterpreterFrame_LASTI(frame) * (int)sizeof(_Py_CODEUNIT));
        }
        frame = frame->previous;
    }
    std::reverse(frame_lastis.begin(), frame_lastis.end());

    PyObject *result = PyTuple_New(n);
    if (!result) return nullptr;

    Py_ssize_t frame_count = (Py_ssize_t)frame_lastis.size();
    Py_ssize_t offset = frame_count - n;
    if (offset < 0) offset = 0;

    for (Py_ssize_t i = 0; i < n; i++) {
        int lasti = (offset + i < frame_count) ? frame_lastis[offset + i] : -1;
        PyObject *obj = PyLong_FromLong(lasti);
        if (!obj) {
            Py_DECREF(result);
            return nullptr;
        }
        PyTuple_SET_ITEM(result, i, obj);
    }

    return result;
}

PyObject *
build_current_cursor()
{
    Py_ssize_t n = (Py_ssize_t)tc->cursor_stack.size();
    PyObject *result = PyTuple_New(n);
    if (!result) return nullptr;

    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *cc_obj = PyLong_FromLong(tc->cursor_stack[i].call_count);
        if (!cc_obj) {
            Py_DECREF(result);
            return nullptr;
        }
        PyTuple_SET_ITEM(result, i, cc_obj);
    }

    return result;
}

static void
reset_cursor_state(PyObject *owner)
{
    get_tc(owner);
    tc->cursor_stack.clear();
    tc->root_parent_valid = false;
    tc->root_parent_frame = nullptr;
    tc->root_parent_lasti = -1;
    tc->root_repeat_count = 0;
    tc->suspend_depth = 0;
    tc->suspended_frame = nullptr;
    tc->watches.clear();
}

// ---------------------------------------------------------------------------
// Python 3.11 fallback — PyEval_SetFrameEvalFunction wrapper
// ---------------------------------------------------------------------------

#if PY_VERSION_HEX >= 0x030B0000 && PY_VERSION_HEX < 0x030C0000

struct CursorFrame311 {
    PyFunctionObject *f_func;
    PyObject         *f_globals;
    PyObject         *f_builtins;
    PyObject         *f_locals;
    PyCodeObject     *f_code;
    PyFrameObject    *frame_obj;
    struct CursorFrame311 *previous;
    _Py_CODEUNIT     *prev_instr;
    int               stacktop;
    bool              is_entry;
    char              owner;
    PyObject         *localsplus[1];
};

static _PyFrameEvalFunction real_eval = nullptr;

static int
parent_lasti_311(CursorFrame311 *parent)
{
    if (!parent) return -1;
    auto *p = (struct _PyInterpreterFrame *)parent;
    return _PyInterpreterFrame_LASTI(p) * (int)sizeof(_Py_CODEUNIT);
}

static PyObject *
eval_frame(PyThreadState *tstate,
           struct _PyInterpreterFrame *frame,
           int throw_flag)
{
    if (tstate->tracing || tc->suspend_depth > 0) {
        return real_eval(tstate, frame, throw_flag);
    }

    CursorFrame311 *f = (CursorFrame311 *)frame;

    if (!tc->cursor_stack.empty()) {
        tc->cursor_stack.back().call_count++;
    } else if (f->previous) {
        int plasti = parent_lasti_311(f->previous);
        if (tc->root_parent_valid &&
            tc->root_parent_frame == (void *)f->previous &&
            tc->root_parent_lasti == plasti) {
            tc->root_repeat_count++;
        } else {
            tc->root_repeat_count = 0;
        }
        tc->root_parent_frame = (void *)f->previous;
        tc->root_parent_lasti = plasti;
        tc->root_parent_valid = true;
    } else {
        tc->root_parent_valid = false;
        tc->root_parent_frame = nullptr;
        tc->root_parent_lasti = -1;
        tc->root_repeat_count = 0;
    }

    int new_count = tc->cursor_stack.empty() ? tc->root_repeat_count : 0;
    tc->cursor_stack.push_back({new_count});
    check_watches(WatchSlot::start);
    PyObject *result = real_eval(tstate, frame, throw_flag);
    if (result)
        check_watches(WatchSlot::on_return);
    else
        check_watches(WatchSlot::unwind);
    if (!tc->cursor_stack.empty())
        tc->cursor_stack.pop_back();
    check_watches(WatchSlot::start);

    return result;
}

#endif // Python 3.11

// ---------------------------------------------------------------------------
// DisabledCallback — hidden C callable that suspends cursor tracking
// ---------------------------------------------------------------------------

struct DisabledCallback : public PyObject {
    PyObject *fn;
    PyObject *owner;
    vectorcallfunc vectorcall;

    static PyObject *call(DisabledCallback *self,
                          PyObject *const *args, size_t nargsf, PyObject *kwnames) {
        get_tc(self->owner);
        if (tc->suspend_depth == 0)
            tc->suspended_frame = PyThreadState_Get()->cframe->current_frame;
        tc->suspend_depth++;
        PyObject *result = PyObject_Vectorcall(self->fn, args, nargsf, kwnames);
        tc->suspend_depth--;
        if (tc->suspend_depth == 0)
            tc->suspended_frame = nullptr;
        return result;
    }

    static void dealloc(DisabledCallback *self) {
        Py_XDECREF(self->fn);
        Py_XDECREF(self->owner);
        Py_TYPE(self)->tp_free((PyObject *)self);
    }

    static PyObject *repr(DisabledCallback *self) {
        PyObject *fn_repr = PyObject_Repr(self->fn);
        if (!fn_repr) return nullptr;
        PyObject *result = PyUnicode_FromFormat("<DisabledCallback wrapping %U>", fn_repr);
        Py_DECREF(fn_repr);
        return result;
    }
};

PyTypeObject DisabledCallback_Type = {
    .ob_base = PyVarObject_HEAD_INIT(nullptr, 0)
    .tp_name = MODULE "DisabledCallback",
    .tp_basicsize = sizeof(DisabledCallback),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)DisabledCallback::dealloc,
    .tp_repr = (reprfunc)DisabledCallback::repr,
    .tp_vectorcall_offset = offsetof(DisabledCallback, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "Internal wrapper that suspends cursor tracking during a call.",
};

// ---------------------------------------------------------------------------
// CallCounter C extension type
// ---------------------------------------------------------------------------

#define CURSOR_NOT_INSTALLED  -1
#define CURSOR_FRAME_EVAL    -2

struct CallCounter : public PyObject {
    int tool_id;
    PyObject *mon_start_cb;
    PyObject *mon_return_cb;
    PyObject *mon_unwind_cb;
    PyObject *mon_jump_cb;
    PyObject *on_thread_switch_cb;
    PyThreadState *last_tstate;

    static int init(CallCounter *self, PyObject *args, PyObject *kwds) {
        self->tool_id = CURSOR_NOT_INSTALLED;
        self->mon_start_cb = nullptr;
        self->mon_return_cb = nullptr;
        self->mon_unwind_cb = nullptr;
        self->mon_jump_cb = nullptr;
        self->on_thread_switch_cb = nullptr;
        self->last_tstate = PyThreadState_Get();
        return 0;
    }

    static void dealloc(CallCounter *self) {
        if (self->tool_id != CURSOR_NOT_INSTALLED) {
            PyObject *r = CallCounter::uninstall_impl(self, nullptr);
            Py_XDECREF(r);
            if (PyErr_Occurred()) PyErr_Clear();
        }
        Py_XDECREF(self->mon_start_cb);
        Py_XDECREF(self->mon_return_cb);
        Py_XDECREF(self->mon_unwind_cb);
        Py_XDECREF(self->mon_jump_cb);
        Py_XDECREF(self->on_thread_switch_cb);
        Py_TYPE(self)->tp_free((PyObject *)self);
    }

    // -- install ----------------------------------------------------------

    static PyObject *install_impl(CallCounter *self, PyObject *Py_UNUSED(ignored)) {
        if (self->tool_id != CURSOR_NOT_INSTALLED)
            Py_RETURN_NONE;

#if PY_VERSION_HEX >= 0x030C0000
        PyObject *sys_mod = PyImport_ImportModule("sys");
        if (!sys_mod) return nullptr;
        PyObject *monitoring = PyObject_GetAttrString(sys_mod, "monitoring");
        Py_DECREF(sys_mod);
        if (!monitoring) return nullptr;

        int tid = -1;
        for (int i = 0; i < 6; i++) {
            PyObject *r = PyObject_CallMethod(monitoring, "use_tool_id", "is", i, "retrace_cursor");
            if (r) {
                Py_DECREF(r);
                tid = i;
                break;
            }
            PyErr_Clear();
        }
        if (tid < 0) {
            Py_DECREF(monitoring);
            PyErr_SetString(PyExc_RuntimeError, "No free sys.monitoring tool IDs available");
            return nullptr;
        }

        static PyMethodDef start_def  = {"_cc_py_start",  (PyCFunction)on_py_start,  METH_FASTCALL, nullptr};
        static PyMethodDef return_def = {"_cc_py_return", (PyCFunction)on_py_return, METH_FASTCALL, nullptr};
        static PyMethodDef unwind_def = {"_cc_py_unwind", (PyCFunction)on_py_unwind, METH_FASTCALL, nullptr};
        static PyMethodDef jump_def   = {"_cc_py_jump",   (PyCFunction)on_py_jump,   METH_FASTCALL, nullptr};

        Py_XDECREF(self->mon_start_cb);
        Py_XDECREF(self->mon_return_cb);
        Py_XDECREF(self->mon_unwind_cb);
        Py_XDECREF(self->mon_jump_cb);
        self->mon_start_cb  = PyCFunction_New(&start_def, (PyObject *)self);
        self->mon_return_cb = PyCFunction_New(&return_def, (PyObject *)self);
        self->mon_unwind_cb = PyCFunction_New(&unwind_def, (PyObject *)self);
        self->mon_jump_cb   = PyCFunction_New(&jump_def, (PyObject *)self);
        if (!self->mon_start_cb || !self->mon_return_cb ||
            !self->mon_unwind_cb || !self->mon_jump_cb) {
            Py_DECREF(monitoring);
            return nullptr;
        }

        PyObject *events_ns = PyObject_GetAttrString(monitoring, "events");
        if (!events_ns) { Py_DECREF(monitoring); return nullptr; }

        PyObject *ev_start  = PyObject_GetAttrString(events_ns, "PY_START");
        PyObject *ev_return = PyObject_GetAttrString(events_ns, "PY_RETURN");
        PyObject *ev_unwind = PyObject_GetAttrString(events_ns, "PY_UNWIND");
        PyObject *ev_jump   = PyObject_GetAttrString(events_ns, "JUMP");
        Py_DECREF(events_ns);
        if (!ev_start || !ev_return || !ev_unwind) {
            Py_XDECREF(ev_start); Py_XDECREF(ev_return); Py_XDECREF(ev_unwind);
            Py_XDECREF(ev_jump);
            Py_DECREF(monitoring);
            return nullptr;
        }
        if (!ev_jump) PyErr_Clear();

        long vs = PyLong_AsLong(ev_start);
        long vr = PyLong_AsLong(ev_return);
        long vu = PyLong_AsLong(ev_unwind);
        long vj = ev_jump ? PyLong_AsLong(ev_jump) : 0;

        PyObject *r;
        r = PyObject_CallMethod(monitoring, "register_callback", "iOO", tid, ev_start, self->mon_start_cb);
        if (!r) goto fail_events;
        Py_DECREF(r);

        r = PyObject_CallMethod(monitoring, "register_callback", "iOO", tid, ev_return, self->mon_return_cb);
        if (!r) goto fail_events;
        Py_DECREF(r);

        r = PyObject_CallMethod(monitoring, "register_callback", "iOO", tid, ev_unwind, self->mon_unwind_cb);
        if (!r) goto fail_events;
        Py_DECREF(r);

        if (ev_jump) {
            r = PyObject_CallMethod(monitoring, "register_callback", "iOO", tid, ev_jump, self->mon_jump_cb);
            if (!r) goto fail_events;
            Py_DECREF(r);
        }

        r = PyObject_CallMethod(monitoring, "set_events", "il", tid, vs | vr | vu | vj);
        if (!r) goto fail_events;
        Py_DECREF(r);

        Py_DECREF(ev_start);
        Py_DECREF(ev_return);
        Py_DECREF(ev_unwind);
        Py_XDECREF(ev_jump);
        Py_DECREF(monitoring);

        self->tool_id = tid;
        reset_cursor_state((PyObject *)self);
        Py_RETURN_NONE;

    fail_events:
        Py_DECREF(ev_start);
        Py_DECREF(ev_return);
        Py_DECREF(ev_unwind);
        Py_XDECREF(ev_jump);
        Py_DECREF(monitoring);
        return nullptr;

#elif PY_VERSION_HEX >= 0x030B0000
        if (!real_eval) {
            PyInterpreterState *interp = PyInterpreterState_Get();
            real_eval = _PyInterpreterState_GetEvalFrameFunc(interp);
            _PyInterpreterState_SetEvalFrameFunc(interp,
                (_PyFrameEvalFunction)eval_frame);
        }
        self->tool_id = CURSOR_FRAME_EVAL;
#else
        PyErr_SetString(PyExc_RuntimeError, "CallCounter tracking requires Python 3.11+");
        return nullptr;
#endif

        reset_cursor_state((PyObject *)self);
        Py_RETURN_NONE;
    }

    // -- uninstall --------------------------------------------------------

    static PyObject *uninstall_impl(CallCounter *self, PyObject *Py_UNUSED(ignored)) {
        if (self->tool_id == CURSOR_NOT_INSTALLED)
            Py_RETURN_NONE;

#if PY_VERSION_HEX >= 0x030C0000
        if (self->tool_id >= 0) {
            PyObject *sys_mod = PyImport_ImportModule("sys");
            if (!sys_mod) return nullptr;
            PyObject *monitoring = PyObject_GetAttrString(sys_mod, "monitoring");
            Py_DECREF(sys_mod);
            if (!monitoring) return nullptr;

            PyObject *events_ns = PyObject_GetAttrString(monitoring, "events");
            if (!events_ns) { Py_DECREF(monitoring); return nullptr; }

            PyObject *ev_start  = PyObject_GetAttrString(events_ns, "PY_START");
            PyObject *ev_return = PyObject_GetAttrString(events_ns, "PY_RETURN");
            PyObject *ev_unwind = PyObject_GetAttrString(events_ns, "PY_UNWIND");
            PyObject *ev_jump   = PyObject_GetAttrString(events_ns, "JUMP");
            Py_DECREF(events_ns);
            if (!ev_jump) PyErr_Clear();

            PyObject *r;
            r = PyObject_CallMethod(monitoring, "set_events", "ii", self->tool_id, 0);
            Py_XDECREF(r);

            if (ev_start) {
                r = PyObject_CallMethod(monitoring, "register_callback", "iOO", self->tool_id, ev_start, Py_None);
                Py_XDECREF(r);
            }
            if (ev_return) {
                r = PyObject_CallMethod(monitoring, "register_callback", "iOO", self->tool_id, ev_return, Py_None);
                Py_XDECREF(r);
            }
            if (ev_unwind) {
                r = PyObject_CallMethod(monitoring, "register_callback", "iOO", self->tool_id, ev_unwind, Py_None);
                Py_XDECREF(r);
            }
            if (ev_jump) {
                r = PyObject_CallMethod(monitoring, "register_callback", "iOO", self->tool_id, ev_jump, Py_None);
                Py_XDECREF(r);
            }

            r = PyObject_CallMethod(monitoring, "free_tool_id", "i", self->tool_id);
            Py_XDECREF(r);

            Py_XDECREF(ev_start);
            Py_XDECREF(ev_return);
            Py_XDECREF(ev_unwind);
            Py_XDECREF(ev_jump);
            Py_DECREF(monitoring);

            Py_CLEAR(self->mon_start_cb);
            Py_CLEAR(self->mon_return_cb);
            Py_CLEAR(self->mon_unwind_cb);
            Py_CLEAR(self->mon_jump_cb);
        }
#endif

#if PY_VERSION_HEX >= 0x030B0000 && PY_VERSION_HEX < 0x030C0000
        if (self->tool_id == CURSOR_FRAME_EVAL && real_eval) {
            PyInterpreterState *interp = PyInterpreterState_Get();
            _PyInterpreterState_SetEvalFrameFunc(interp, real_eval);
            real_eval = nullptr;
        }
#endif

        self->tool_id = CURSOR_NOT_INSTALLED;
        reset_cursor_state((PyObject *)self);

        PyObject *dict = PyThreadState_GetDict();
        if (dict) PyDict_DelItem(dict, (PyObject *)self);
        PyErr_Clear();
        invalidate_tc_cache((PyObject *)self);
        Py_RETURN_NONE;
    }

    // -- reset ------------------------------------------------------------

    static PyObject *reset_impl(CallCounter *self, PyObject *Py_UNUSED(ignored)) {
        reset_cursor_state((PyObject *)self);
        Py_RETURN_NONE;
    }

    // -- current (convenience, delegates to ThreadCallCounts) -------------

    static PyObject *current_impl(CallCounter *self, PyObject *Py_UNUSED(ignored)) {
        get_tc((PyObject *)self);
        return build_current_cursor();
    }

    // -- frame_positions (convenience, delegates to ThreadCallCounts) -----

    static PyObject *frame_positions_impl(CallCounter *self, PyObject *Py_UNUSED(ignored)) {
        get_tc((PyObject *)self);
        return build_frame_positions();
    }

    // -- yield_at (backward compat, delegates to on_start) ----------------

    static PyObject *yield_at_impl(CallCounter *self, PyObject *const *args, Py_ssize_t nargs) {
        get_tc((PyObject *)self);
        if (nargs != 2) {
            PyErr_SetString(PyExc_TypeError, "yield_at expects (callback, call_counts)");
            return nullptr;
        }
        PyObject *callback = args[0];
        if (!PyCallable_Check(callback)) {
            PyErr_SetString(PyExc_TypeError, "callback must be callable");
            return nullptr;
        }
        PyObject *counts = args[1];
        if (!PyTuple_Check(counts)) {
            PyErr_SetString(PyExc_TypeError, "call_counts must be a tuple of ints");
            return nullptr;
        }

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
        tc->watches.emplace_back(std::move(target), callback);
        check_watches(WatchSlot::start);
        Py_RETURN_NONE;
    }

    // -- disable_for ------------------------------------------------------

    static PyObject *disable_for_impl(CallCounter *self, PyObject *fn) {
        if (!PyCallable_Check(fn)) {
            PyErr_SetString(PyExc_TypeError, "argument must be callable");
            return nullptr;
        }
        DisabledCallback *wrapper =
            PyObject_New(DisabledCallback, &DisabledCallback_Type);
        if (!wrapper) return nullptr;
        wrapper->fn = Py_NewRef(fn);
        wrapper->owner = Py_NewRef((PyObject *)self);
        wrapper->vectorcall = (vectorcallfunc)DisabledCallback::call;
        return (PyObject *)wrapper;
    }

    // -- properties -------------------------------------------------------

    static PyObject *get_installed(CallCounter *self, void *) {
        return PyBool_FromLong(self->tool_id != CURSOR_NOT_INSTALLED);
    }

    static PyObject *get_depth(CallCounter *self, void *) {
        get_tc((PyObject *)self);
        return PyLong_FromSsize_t((Py_ssize_t)tc->cursor_stack.size());
    }

    static PyObject *get_tool_id(CallCounter *self, void *) {
        return PyLong_FromLong(self->tool_id);
    }

    // -- call (returns ThreadCallCounts for current thread) ---------------

    static PyObject *call_impl(CallCounter *self, PyObject *args, PyObject *kwds) {
        ThreadCallCounts *result = get_tc((PyObject *)self);
        if (!result) {
            PyErr_SetString(PyExc_RuntimeError, "failed to get ThreadCallCounts");
            return nullptr;
        }
        return Py_NewRef((PyObject *)result);
    }

    // -- repr -------------------------------------------------------------

    static PyObject *repr_impl(CallCounter *self) {
        get_tc((PyObject *)self);
        PyObject *cur = build_current_cursor();
        if (!cur) return nullptr;
        PyObject *cur_repr = PyObject_Repr(cur);
        Py_DECREF(cur);
        if (!cur_repr) return nullptr;
        const char *state = (self->tool_id != CURSOR_NOT_INSTALLED) ? "installed" : "idle";
        PyObject *result = PyUnicode_FromFormat("<CallCounter %s %U>", state, cur_repr);
        Py_DECREF(cur_repr);
        return result;
    }

    // -- len (depth of cursor stack) --------------------------------------

    static Py_ssize_t len_impl(CallCounter *self) {
        get_tc((PyObject *)self);
        return (Py_ssize_t)tc->cursor_stack.size();
    }
};

static void check_thread_switch(CallCounter *cc) {
    PyThreadState *tstate = PyThreadState_Get();
    if (cc->last_tstate != tstate) {
        cc->last_tstate = tstate;
        if (cc->on_thread_switch_cb) {
            PyObject *result = PyObject_CallNoArgs(cc->on_thread_switch_cb);
            Py_XDECREF(result);
            if (PyErr_Occurred()) PyErr_Clear();
        }
    }
}

// ---------------------------------------------------------------------------
// Method / getset / sequence tables
// ---------------------------------------------------------------------------

static PyMethodDef CallCounter_methods[] = {
    {"install",          (PyCFunction)CallCounter::install_impl,          METH_NOARGS,   "Install call-count tracking hooks"},
    {"uninstall",        (PyCFunction)CallCounter::uninstall_impl,        METH_NOARGS,   "Remove tracking hooks and reset state"},
    {"reset",            (PyCFunction)CallCounter::reset_impl,            METH_NOARGS,   "Clear the call-count stack"},
    {"current",          (PyCFunction)CallCounter::current_impl,          METH_NOARGS,   "Return the current call counts as a tuple of ints"},
    {"frame_positions",  (PyCFunction)CallCounter::frame_positions_impl,  METH_NOARGS,   "Return a tuple of f_lasti ints aligned to the call-count stack"},
    {"yield_at",         (PyCFunction)CallCounter::yield_at_impl,         METH_FASTCALL, "Arm a one-shot start callback (backward compat alias)"},
    {"disable_for",      (PyCFunction)CallCounter::disable_for_impl,      METH_O,        "Return a C wrapper that freezes call-count tracking for the duration of the call"},
    {nullptr}
};

static PyObject *get_on_thread_switch(CallCounter *self, void *) {
    if (self->on_thread_switch_cb)
        return Py_NewRef(self->on_thread_switch_cb);
    Py_RETURN_NONE;
}

static int set_on_thread_switch(CallCounter *self, PyObject *value, void *) {
    if (value == Py_None) value = nullptr;
    if (value && !PyCallable_Check(value)) {
        PyErr_SetString(PyExc_TypeError, "on_thread_switch must be callable");
        return -1;
    }
    Py_XDECREF(self->on_thread_switch_cb);
    self->on_thread_switch_cb = value ? Py_NewRef(value) : nullptr;
    return 0;
}

static PyGetSetDef CallCounter_getset[] = {
    {"installed",        (getter)CallCounter::get_installed, nullptr, "True if hooks are currently installed", nullptr},
    {"depth",            (getter)CallCounter::get_depth,     nullptr, "Current call-count stack depth", nullptr},
    {"tool_id",          (getter)CallCounter::get_tool_id,   nullptr, "sys.monitoring tool ID (-1 if not installed)", nullptr},
    {"on_thread_switch", (getter)get_on_thread_switch, (setter)set_on_thread_switch, "Parameterless callback fired on thread switch", nullptr},
    {nullptr}
};

static PySequenceMethods CallCounter_as_sequence = {
    .sq_length = (lenfunc)CallCounter::len_impl,
};

PyTypeObject CallCounter_Type = {
    .ob_base = PyVarObject_HEAD_INIT(nullptr, 0)
    .tp_name = MODULE "CallCounter",
    .tp_basicsize = sizeof(CallCounter),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)CallCounter::dealloc,
    .tp_repr = (reprfunc)CallCounter::repr_impl,
    .tp_as_sequence = &CallCounter_as_sequence,
    .tp_call = (ternaryfunc)CallCounter::call_impl,
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_doc = "Call-count tracking for replay positioning.\n"
              "\n"
              "Usage:\n"
              "    cc = CallCounter()\n"
              "    cc.install()\n"
              "    tc = cc()  # ThreadCallCounts for current thread\n"
              "    with tc:\n"
              "        print(tc.current())\n",
    .tp_methods = CallCounter_methods,
    .tp_getset = CallCounter_getset,
    .tp_init = (initproc)CallCounter::init,
    .tp_new = PyType_GenericNew,
};

