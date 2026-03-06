#pragma once
#include <Python.h>

namespace retracesoftware {

    class GILReleaseGuard {
    private:
        // Stores the thread state pointer when the GIL is released.
        PyThreadState* saved_state_;

    public:
        // Constructor: Saves the thread state and releases the GIL.
        inline GILReleaseGuard() {
            // PyEval_SaveThread() releases the GIL and returns the current thread state.
            saved_state_ = PyEval_SaveThread();
        }

        // Destructor: Reacquires the GIL and restores the thread state.
        inline ~GILReleaseGuard() {
            // PyEval_RestoreThread() reacquires the GIL using the saved state.
            PyEval_RestoreThread(saved_state_);
        }

        // Deleted Copy/Move Constructors and Assignment Operators
        GILReleaseGuard(const GILReleaseGuard&) = delete;
        GILReleaseGuard& operator=(const GILReleaseGuard&) = delete;
        GILReleaseGuard(GILReleaseGuard&&) = delete;
        GILReleaseGuard& operator=(GILReleaseGuard&&) = delete;
    };

    class GILGuard {
    private:
        // Stores the state returned by PyGILState_Ensure()
        PyGILState_STATE gstate_;

    public:
        // Constructor: Acquires the GIL
        inline GILGuard() {
            // PyGILState_Ensure() handles both acquiring the GIL and establishing 
            // a valid PyThreadState for the current thread.
            gstate_ = PyGILState_Ensure();
            
            // Note: For C++ exception safety, you might want a check here 
            // (though PyGILState_Ensure is generally non-failing in a way that needs exception throwing).
            // If gstate_ indicated a failure state (which it doesn't typically do), 
            // you would throw an exception here.
        }

        // Destructor: Releases the GIL
        inline ~GILGuard() {
            // PyGILState_Release() must be called with the state returned by PyGILState_Ensure().
            PyGILState_Release(gstate_);
        }

        // Deleted Copy/Move Constructors and Assignment Operators
        // Prevents accidental copying or moving, as the GIL state should be unique
        // to the scope and thread that created the guard.
        inline GILGuard(const GILGuard&) = delete;
        inline GILGuard& operator=(const GILGuard&) = delete;
        inline GILGuard(GILGuard&&) = delete;
        inline GILGuard& operator=(GILGuard&&) = delete;
    };
}