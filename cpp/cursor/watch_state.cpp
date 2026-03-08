#include "watch_state.h"

WatchState::WatchState(std::vector<int> target,
                       PyObject *on_start,
                       PyObject *on_return,
                       PyObject *on_unwind,
                       PyObject *on_backjump,
                       PyObject *on_overshoot)
    : target_(std::move(target)),
      start_(Py_XNewRef(on_start)),
      return_(Py_XNewRef(on_return)),
      unwind_(Py_XNewRef(on_unwind)),
      backjump_(Py_XNewRef(on_backjump)),
      overshoot_(Py_XNewRef(on_overshoot))
{}

WatchState::WatchState(WatchState &&other) noexcept
    : target_(std::move(other.target_)),
      start_match_prefix_(other.start_match_prefix_),
      start_(other.start_),
      return_(other.return_),
      unwind_(other.unwind_),
      backjump_(other.backjump_),
      overshoot_(other.overshoot_)
{
    other.start_ = nullptr;
    other.return_ = nullptr;
    other.unwind_ = nullptr;
    other.backjump_ = nullptr;
    other.overshoot_ = nullptr;
    other.start_match_prefix_ = 0;
}

WatchState &WatchState::operator=(WatchState &&other) noexcept
{
    if (this != &other) {
        clear();
        target_ = std::move(other.target_);
        start_match_prefix_ = other.start_match_prefix_;
        start_ = other.start_;
        return_ = other.return_;
        unwind_ = other.unwind_;
        backjump_ = other.backjump_;
        overshoot_ = other.overshoot_;
        other.start_ = nullptr;
        other.return_ = nullptr;
        other.unwind_ = nullptr;
        other.backjump_ = nullptr;
        other.overshoot_ = nullptr;
        other.start_match_prefix_ = 0;
    }
    return *this;
}

WatchState::~WatchState()
{
    clear();
}

PyObject *&WatchState::get_slot(WatchSlot s)
{
    switch (s) {
        case WatchSlot::start:     return start_;
        case WatchSlot::on_return: return return_;
        case WatchSlot::unwind:    return unwind_;
        case WatchSlot::backjump:  return backjump_;
    }
    __builtin_unreachable();
}

PyObject *const &WatchState::get_slot(WatchSlot s) const
{
    switch (s) {
        case WatchSlot::start:     return start_;
        case WatchSlot::on_return: return return_;
        case WatchSlot::unwind:    return unwind_;
        case WatchSlot::backjump:  return backjump_;
    }
    __builtin_unreachable();
}

void WatchState::clear()
{
    Py_CLEAR(start_);
    Py_CLEAR(return_);
    Py_CLEAR(unwind_);
    Py_CLEAR(backjump_);
    Py_CLEAR(overshoot_);
    start_match_prefix_ = 0;
}

void WatchState::fire_synchronously(PyObject *cb)
{
    if (!cb) return;
    PyObject *result = PyObject_CallNoArgs(cb);
    Py_DECREF(cb);
    if (!result) { PyErr_Clear(); return; }
    Py_DECREF(result);
}

bool WatchState::completed() const
{
    return !start_ && !return_ && !unwind_ && !backjump_ && !overshoot_;
}

bool WatchState::fire_exact(PyObject *&slot,
                            const std::vector<CursorEntry> &cursor_stack)
{
    if (!slot) return completed();
    const size_t n = cursor_stack.size();
    if (n != target_.size()) return completed();
    for (size_t i = 0; i < n; i++) {
        if (cursor_stack[i].call_count != target_[i]) return completed();
    }
    PyObject *cb = slot;
    slot = nullptr;
    Py_CLEAR(overshoot_);
    fire_synchronously(cb);
    return completed();
}

bool WatchState::fire_start(const std::vector<CursorEntry> &cursor_stack)
{
    if (!start_) return completed();

    const size_t target_size = target_.size();
    const size_t n = cursor_stack.size();
    if (n < target_size) return completed();

    while (start_match_prefix_ < target_size) {
        const size_t i = start_match_prefix_;
        const int cur = cursor_stack[i].call_count;
        const int tgt = target_[i];
        if (cur < tgt) return completed();
        if (cur > tgt) {
            Py_CLEAR(start_);
            start_match_prefix_ = 0;
            if (overshoot_) {
                PyObject *cb = overshoot_;
                overshoot_ = nullptr;
                clear();
                fire_synchronously(cb);
            }
            return true;
        }
        start_match_prefix_++;
    }

    if (n != target_size) return completed();

    PyObject *cb = start_;
    start_ = nullptr;
    start_match_prefix_ = 0;
    Py_CLEAR(overshoot_);
    fire_synchronously(cb);
    return completed();
}

bool WatchState::operator()(WatchSlot slot,
                            const std::vector<CursorEntry> &cursor_stack)
{
    if (slot == WatchSlot::start) {
        return fire_start(cursor_stack);
    } else {
        return fire_exact(get_slot(slot), cursor_stack);
    }
}

void WatchState::fire_overshoot()
{
    if (overshoot_) {
        PyObject *cb = overshoot_;
        overshoot_ = nullptr;
        clear();
        fire_synchronously(cb);
    }
}
