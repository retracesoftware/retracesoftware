#include "utils.h"
#include <vector>
#include <algorithm>
#include <internal/pycore_frame.h>

namespace retracesoftware {

// ---------------------------------------------------------------------------
// Per-thread cursor + watch state
// ---------------------------------------------------------------------------

struct CursorEntry {
    int call_count;
};

struct CallbackSlot {
    PyObject *callback = nullptr;
    bool armed = false;
};

struct WatchState {
    std::vector<int> target;
    size_t start_match_prefix = 0;
    CallbackSlot start_slot;
    CallbackSlot return_slot;
    CallbackSlot unwind_slot;
    CallbackSlot backjump_slot;
};

struct ThreadCounts {
    std::vector<CursorEntry> cursor_stack;
    int suspend_depth = 0;
    std::vector<WatchState> watches;

    // Root parent tracking
    void *root_parent_frame = nullptr;
    int root_parent_lasti = -1;
    int root_repeat_count = 0;
    bool root_parent_valid = false;

    _PyInterpreterFrame *suspended_frame = nullptr;
};

static thread_local ThreadCounts tc;

// ---------------------------------------------------------------------------
// Slot helpers
// ---------------------------------------------------------------------------

static void
disarm_slot(CallbackSlot &s)
{
    s.armed = false;
    Py_CLEAR(s.callback);
}

static void
arm_slot(CallbackSlot &s, PyObject *cb)
{
    Py_XDECREF(s.callback);
    s.callback = Py_NewRef(cb);
    s.armed = true;
}

static void
fire_synchronously(PyObject *cb)
{
    if (!cb) return;
    PyObject *result = PyObject_CallNoArgs(cb);
    Py_DECREF(cb);
    if (!result) { PyErr_Clear(); return; }
    Py_DECREF(result);
}

static void
disarm_watch(WatchState &w)
{
    disarm_slot(w.start_slot);
    disarm_slot(w.return_slot);
    disarm_slot(w.unwind_slot);
    disarm_slot(w.backjump_slot);
}

static void
disarm_all(std::vector<WatchState> &watches)
{
    for (auto &w : watches) disarm_watch(w);
    watches.clear();
}

static void
maybe_fire_slot(ThreadCounts &t, WatchState &w, CallbackSlot &s)
{
    if (t.suspend_depth > 0) return;
    if (!s.armed || !s.callback) return;
    const size_t n = t.cursor_stack.size();
    if (n != w.target.size()) return;
    for (size_t i = 0; i < n; i++) {
        if (t.cursor_stack[i].call_count != w.target[i]) return;
    }
    PyObject *cb = s.callback;
    s.callback = nullptr;
    s.armed = false;
    fire_synchronously(cb);
}

static void
maybe_fire_start(ThreadCounts &t, WatchState &w)
{
    if (t.suspend_depth > 0) return;
    if (!w.start_slot.armed || !w.start_slot.callback) return;

    const size_t target_size = w.target.size();
    const size_t n = t.cursor_stack.size();
    if (n < target_size) return;

    while (w.start_match_prefix < target_size) {
        const size_t i = w.start_match_prefix;
        const int cur = t.cursor_stack[i].call_count;
        const int tgt = w.target[i];
        if (cur < tgt) return;
        if (cur > tgt) {
            disarm_slot(w.start_slot);
            w.start_match_prefix = 0;
            return;
        }
        w.start_match_prefix++;
    }

    if (n != target_size) return;

    PyObject *cb = w.start_slot.callback;
    w.start_slot.callback = nullptr;
    w.start_slot.armed = false;
    w.start_match_prefix = 0;
    fire_synchronously(cb);
}

static void
check_watches_start(ThreadCounts &t)
{
    for (size_t i = 0; i < t.watches.size(); i++)
        maybe_fire_start(t, t.watches[i]);
}

static void
check_watches_slot(ThreadCounts &t, CallbackSlot WatchState::*slot)
{
    for (size_t i = 0; i < t.watches.size(); i++)
        maybe_fire_slot(t, t.watches[i], t.watches[i].*slot);
}

// ---------------------------------------------------------------------------
// sys.monitoring callbacks (module-level PyCFunctions for registration)
// ---------------------------------------------------------------------------

static PyObject *
on_py_start(PyObject *self, PyObject *const *args, Py_ssize_t nargs)
{
    if (tc.suspend_depth > 0) Py_RETURN_NONE;

    int new_count = 0;
    if (!tc.cursor_stack.empty()) {
        tc.cursor_stack.back().call_count++;
    } else {
        _PyInterpreterFrame *frame = PyThreadState_Get()->cframe->current_frame;
        _PyInterpreterFrame *parent = frame ? frame->previous : nullptr;
        if (parent) {
            int parent_lasti = _PyInterpreterFrame_LASTI(parent) * (int)sizeof(_Py_CODEUNIT);
            if (tc.root_parent_valid &&
                tc.root_parent_frame == (void *)parent &&
                tc.root_parent_lasti == parent_lasti) {
                tc.root_repeat_count++;
            } else {
                tc.root_repeat_count = 0;
            }
            tc.root_parent_frame = (void *)parent;
            tc.root_parent_lasti = parent_lasti;
            tc.root_parent_valid = true;
            new_count = tc.root_repeat_count;
        } else {
            tc.root_parent_valid = false;
            tc.root_parent_frame = nullptr;
            tc.root_parent_lasti = -1;
            tc.root_repeat_count = 0;
        }
    }
    tc.cursor_stack.push_back({new_count});
    check_watches_start(tc);
    Py_RETURN_NONE;
}

static PyObject *
on_py_return(PyObject *self, PyObject *const *args, Py_ssize_t nargs)
{
    if (tc.suspend_depth > 0) Py_RETURN_NONE;

    check_watches_slot(tc, &WatchState::return_slot);
    if (!tc.cursor_stack.empty()) {
        tc.cursor_stack.pop_back();
    }
    check_watches_start(tc);
    Py_RETURN_NONE;
}

static PyObject *
on_py_unwind(PyObject *self, PyObject *const *args, Py_ssize_t nargs)
{
    if (tc.suspend_depth > 0) Py_RETURN_NONE;

    check_watches_slot(tc, &WatchState::unwind_slot);
    if (!tc.cursor_stack.empty()) {
        tc.cursor_stack.pop_back();
    }
    check_watches_start(tc);
    Py_RETURN_NONE;
}

static PyObject *
on_py_jump(PyObject *self, PyObject *const *args, Py_ssize_t nargs)
{
    if (tc.suspend_depth > 0) Py_RETURN_NONE;
    if (nargs < 3) Py_RETURN_NONE;

    long src = PyLong_AsLong(args[1]);
    long dst = PyLong_AsLong(args[2]);
    if ((src == -1 || dst == -1) && PyErr_Occurred()) {
        PyErr_Clear();
        Py_RETURN_NONE;
    }
    if (dst < src) {
        check_watches_slot(tc, &WatchState::backjump_slot);
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

static PyObject *
build_frame_positions()
{
    Py_ssize_t n = (Py_ssize_t)tc.cursor_stack.size();

    std::vector<int> frame_lastis;
    _PyInterpreterFrame *frame =
        (tc.suspend_depth > 0 && tc.suspended_frame)
            ? tc.suspended_frame
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

static PyObject *
build_current_cursor()
{
    Py_ssize_t n = (Py_ssize_t)tc.cursor_stack.size();
    PyObject *result = PyTuple_New(n);
    if (!result) return nullptr;

    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *cc_obj = PyLong_FromLong(tc.cursor_stack[i].call_count);
        if (!cc_obj) {
            Py_DECREF(result);
            return nullptr;
        }
        PyTuple_SET_ITEM(result, i, cc_obj);
    }

    return result;
}

static void
reset_cursor_state()
{
    tc.cursor_stack.clear();
    tc.root_parent_valid = false;
    tc.root_parent_frame = nullptr;
    tc.root_parent_lasti = -1;
    tc.root_repeat_count = 0;
    tc.suspend_depth = 0;
    tc.suspended_frame = nullptr;
    disarm_all(tc.watches);
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
    if (tstate->tracing || tc.suspend_depth > 0) {
        return real_eval(tstate, frame, throw_flag);
    }

    CursorFrame311 *f = (CursorFrame311 *)frame;

    if (!tc.cursor_stack.empty()) {
        tc.cursor_stack.back().call_count++;
    } else if (f->previous) {
        int plasti = parent_lasti_311(f->previous);
        if (tc.root_parent_valid &&
            tc.root_parent_frame == (void *)f->previous &&
            tc.root_parent_lasti == plasti) {
            tc.root_repeat_count++;
        } else {
            tc.root_repeat_count = 0;
        }
        tc.root_parent_frame = (void *)f->previous;
        tc.root_parent_lasti = plasti;
        tc.root_parent_valid = true;
    } else {
        tc.root_parent_valid = false;
        tc.root_parent_frame = nullptr;
        tc.root_parent_lasti = -1;
        tc.root_repeat_count = 0;
    }

    int new_count = tc.cursor_stack.empty() ? tc.root_repeat_count : 0;
    tc.cursor_stack.push_back({new_count});
    check_watches_start(tc);
    PyObject *result = real_eval(tstate, frame, throw_flag);
    if (result)
        check_watches_slot(tc, &WatchState::return_slot);
    else
        check_watches_slot(tc, &WatchState::unwind_slot);
    if (!tc.cursor_stack.empty())
        tc.cursor_stack.pop_back();
    check_watches_start(tc);

    return result;
}

#endif // Python 3.11

// ---------------------------------------------------------------------------
// DisabledCallback — hidden C callable that suspends cursor tracking
// ---------------------------------------------------------------------------

struct DisabledCallback : public PyObject {
    PyObject *fn;
    vectorcallfunc vectorcall;

    static PyObject *call(DisabledCallback *self,
                          PyObject *const *args, size_t nargsf, PyObject *kwnames) {
        if (tc.suspend_depth == 0)
            tc.suspended_frame = PyThreadState_Get()->cframe->current_frame;
        tc.suspend_depth++;
        PyObject *result = PyObject_Vectorcall(self->fn, args, nargsf, kwnames);
        tc.suspend_depth--;
        if (tc.suspend_depth == 0)
            tc.suspended_frame = nullptr;
        return result;
    }

    static void dealloc(DisabledCallback *self) {
        Py_XDECREF(self->fn);
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
    .tp_vectorcall_offset = offsetof(DisabledCallback, vectorcall),
    .tp_repr = (reprfunc)DisabledCallback::repr,
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

    static int init(CallCounter *self, PyObject *args, PyObject *kwds) {
        self->tool_id = CURSOR_NOT_INSTALLED;
        self->mon_start_cb = nullptr;
        self->mon_return_cb = nullptr;
        self->mon_unwind_cb = nullptr;
        self->mon_jump_cb = nullptr;
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
        self->mon_start_cb  = PyCFunction_New(&start_def, Py_None);
        self->mon_return_cb = PyCFunction_New(&return_def, Py_None);
        self->mon_unwind_cb = PyCFunction_New(&unwind_def, Py_None);
        self->mon_jump_cb   = PyCFunction_New(&jump_def, Py_None);
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
        reset_cursor_state();
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

        reset_cursor_state();
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
        reset_cursor_state();
        Py_RETURN_NONE;
    }

    // -- reset ------------------------------------------------------------

    static PyObject *reset_impl(CallCounter *self, PyObject *Py_UNUSED(ignored)) {
        reset_cursor_state();
        Py_RETURN_NONE;
    }

    // -- current ----------------------------------------------------------

    static PyObject *current_impl(CallCounter *self, PyObject *Py_UNUSED(ignored)) {
        return build_current_cursor();
    }

    // -- frame_positions --------------------------------------------------

    static PyObject *frame_positions_impl(CallCounter *self, PyObject *Py_UNUSED(ignored)) {
        return build_frame_positions();
    }

    // -- position (zipped call_counts + frame lastis) ---------------------

    static PyObject *position_impl(CallCounter *self, PyObject *Py_UNUSED(ignored)) {
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

    // -- add_watch --------------------------------------------------------
    //
    //   add_watch(call_counts, *,
    //             on_start=None, on_return=None,
    //             on_unwind=None, on_backjump=None)

    static PyObject *add_watch_impl(CallCounter *self,
                                    PyObject *const *args, Py_ssize_t nargs,
                                    PyObject *kwnames)
    {
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

        PyObject *kw_start    = nullptr;
        PyObject *kw_return   = nullptr;
        PyObject *kw_unwind   = nullptr;
        PyObject *kw_backjump = nullptr;

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
                else {
                    PyErr_Format(PyExc_TypeError,
                        "add_watch() got unexpected keyword argument '%U'", key);
                    return nullptr;
                }
            }
        }

        #define VALIDATE_CB(var, name) \
            if (var && var != Py_None && !PyCallable_Check(var)) { \
                PyErr_SetString(PyExc_TypeError, name " must be callable"); \
                return nullptr; \
            }
        VALIDATE_CB(kw_start,    "on_start")
        VALIDATE_CB(kw_return,   "on_return")
        VALIDATE_CB(kw_unwind,   "on_unwind")
        VALIDATE_CB(kw_backjump, "on_backjump")
        #undef VALIDATE_CB

        WatchState w;
        w.target.reserve((size_t)PyTuple_GET_SIZE(counts));
        for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(counts); i++) {
            long value = PyLong_AsLong(PyTuple_GET_ITEM(counts, i));
            if (value == -1 && PyErr_Occurred()) {
                PyErr_SetString(PyExc_TypeError, "call_counts must be a tuple of ints");
                return nullptr;
            }
            w.target.push_back((int)value);
        }

        if (kw_start && kw_start != Py_None)
            arm_slot(w.start_slot, kw_start);
        if (kw_return && kw_return != Py_None)
            arm_slot(w.return_slot, kw_return);
        if (kw_unwind && kw_unwind != Py_None)
            arm_slot(w.unwind_slot, kw_unwind);
        if (kw_backjump && kw_backjump != Py_None)
            arm_slot(w.backjump_slot, kw_backjump);

        tc.watches.push_back(std::move(w));
        if (tc.watches.back().start_slot.armed) check_watches_start(tc);
        Py_RETURN_NONE;
    }

    // -- yield_at (backward compat, delegates to on_start) ----------------

    static PyObject *yield_at_impl(CallCounter *self, PyObject *const *args, Py_ssize_t nargs) {
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

        WatchState w;
        w.target.reserve((size_t)PyTuple_GET_SIZE(counts));
        for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(counts); i++) {
            long value = PyLong_AsLong(PyTuple_GET_ITEM(counts, i));
            if (value == -1 && PyErr_Occurred()) {
                PyErr_SetString(PyExc_TypeError, "call_counts must be a tuple of ints");
                return nullptr;
            }
            w.target.push_back((int)value);
        }
        arm_slot(w.start_slot, callback);
        tc.watches.push_back(std::move(w));
        check_watches_start(tc);
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
        wrapper->vectorcall = (vectorcallfunc)DisabledCallback::call;
        return (PyObject *)wrapper;
    }

    // -- properties -------------------------------------------------------

    static PyObject *get_installed(CallCounter *self, void *) {
        return PyBool_FromLong(self->tool_id != CURSOR_NOT_INSTALLED);
    }

    static PyObject *get_depth(CallCounter *self, void *) {
        return PyLong_FromSsize_t((Py_ssize_t)tc.cursor_stack.size());
    }

    static PyObject *get_tool_id(CallCounter *self, void *) {
        return PyLong_FromLong(self->tool_id);
    }

    // -- context manager --------------------------------------------------

    static PyObject *enter_impl(CallCounter *self, PyObject *Py_UNUSED(ignored)) {
        PyObject *r = install_impl(self, nullptr);
        if (!r) return nullptr;
        Py_DECREF(r);
        return Py_NewRef((PyObject *)self);
    }

    static PyObject *exit_impl(CallCounter *self, PyObject *const *args, Py_ssize_t nargs) {
        return uninstall_impl(self, nullptr);
    }

    // -- repr -------------------------------------------------------------

    static PyObject *repr_impl(CallCounter *self) {
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
        return (Py_ssize_t)tc.cursor_stack.size();
    }
};

// ---------------------------------------------------------------------------
// Method / getset / sequence tables
// ---------------------------------------------------------------------------

static PyMethodDef CallCounter_methods[] = {
    {"install",          (PyCFunction)CallCounter::install_impl,          METH_NOARGS,   "Install call-count tracking hooks"},
    {"uninstall",        (PyCFunction)CallCounter::uninstall_impl,        METH_NOARGS,   "Remove tracking hooks and reset state"},
    {"reset",            (PyCFunction)CallCounter::reset_impl,            METH_NOARGS,   "Clear the call-count stack"},
    {"current",          (PyCFunction)CallCounter::current_impl,          METH_NOARGS,   "Return the current call counts as a tuple of ints"},
    {"frame_positions",  (PyCFunction)CallCounter::frame_positions_impl,  METH_NOARGS,   "Return a tuple of f_lasti ints aligned to the call-count stack"},
    {"position",         (PyCFunction)CallCounter::position_impl,         METH_NOARGS,   "Return tuple of (call_count, f_lasti) pairs"},
    {"add_watch",        (PyCFunction)(void(*)(void))CallCounter::add_watch_impl,
                         METH_FASTCALL | METH_KEYWORDS,
                         "add_watch(call_counts, *, on_start=None, on_return=None, on_unwind=None, on_backjump=None)\n"
                         "Add a one-shot watch for a target call-counts position."},
    {"yield_at",         (PyCFunction)CallCounter::yield_at_impl,         METH_FASTCALL, "Arm a one-shot start callback (backward compat alias)"},
    {"disable_for",      (PyCFunction)CallCounter::disable_for_impl,      METH_O,        "Return a C wrapper that freezes call-count tracking for the duration of the call"},
    {"__enter__",        (PyCFunction)CallCounter::enter_impl,            METH_NOARGS,   nullptr},
    {"__exit__",         (PyCFunction)CallCounter::exit_impl,             METH_FASTCALL, nullptr},
    {nullptr}
};

static PyGetSetDef CallCounter_getset[] = {
    {"installed", (getter)CallCounter::get_installed, nullptr, "True if hooks are currently installed", nullptr},
    {"depth",     (getter)CallCounter::get_depth,     nullptr, "Current call-count stack depth", nullptr},
    {"tool_id",   (getter)CallCounter::get_tool_id,   nullptr, "sys.monitoring tool ID (-1 if not installed)", nullptr},
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
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_doc = "Call-count tracking for replay positioning.\n"
              "\n"
              "Usage:\n"
              "    cc = CallCounter()\n"
              "    cc.install()\n"
              "    print(cc.current())\n"
              "    cc.uninstall()\n"
              "\n"
              "Or as a context manager:\n"
              "    with CallCounter() as cc:\n"
              "        print(cc.current())\n",
    .tp_methods = CallCounter_methods,
    .tp_getset = CallCounter_getset,
    .tp_init = (initproc)CallCounter::init,
    .tp_new = PyType_GenericNew,
};

} // namespace retracesoftware
