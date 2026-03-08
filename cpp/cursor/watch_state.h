#pragma once
#include <Python.h>
#include <vector>

struct CursorEntry {
    int call_count;
};

enum class WatchSlot { start, on_return, unwind, backjump };

class WatchState {
public:
    WatchState(std::vector<int> target,
               PyObject *on_start = nullptr,
               PyObject *on_return = nullptr,
               PyObject *on_unwind = nullptr,
               PyObject *on_backjump = nullptr,
               PyObject *on_overshoot = nullptr);

    WatchState(WatchState &&other) noexcept;
    WatchState &operator=(WatchState &&other) noexcept;
    ~WatchState();

    WatchState(const WatchState &) = delete;
    WatchState &operator=(const WatchState &) = delete;

    bool operator()(WatchSlot slot,
                    const std::vector<CursorEntry> &cursor_stack);

    void fire_overshoot();

private:
    bool completed() const;
    std::vector<int> target_;
    size_t start_match_prefix_ = 0;
    PyObject *start_ = nullptr;
    PyObject *return_ = nullptr;
    PyObject *unwind_ = nullptr;
    PyObject *backjump_ = nullptr;
    PyObject *overshoot_ = nullptr;

    PyObject *&get_slot(WatchSlot s);
    PyObject *const &get_slot(WatchSlot s) const;

    void clear();
    static void fire_synchronously(PyObject *cb);

    bool fire_exact(PyObject *&slot, const std::vector<CursorEntry> &cursor_stack);
    bool fire_start(const std::vector<CursorEntry> &cursor_stack);
};
