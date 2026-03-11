#pragma once
#include <Python.h>
#include <vector>
#include <internal/pycore_frame.h>
#include "watch_state.h"

#define MODULE "retracesoftware_cursor."

struct ThreadCallCounts : public PyObject {
    std::vector<CursorEntry> cursor_stack;
    int suspend_depth;
    int check_watches_depth;
    std::vector<WatchState> watches;

    _PyInterpreterFrame *suspended_frame;
    PyObject *suspended_cursor;

    Py_ssize_t context_depth;
};

extern thread_local ThreadCallCounts *tc;
ThreadCallCounts *get_tc(PyObject *owner);
void invalidate_tc_cache(PyObject *owner);

PyObject *build_current_cursor();
PyObject *build_frame_positions();
void check_watches(WatchSlot slot);

extern PyTypeObject ThreadCallCounts_Type;
extern PyTypeObject CallCounter_Type;
extern PyTypeObject DisabledCallback_Type;
