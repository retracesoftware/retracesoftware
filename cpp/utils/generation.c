#define Py_BUILD_CORE
#include <Python.h>
// #include <internal/pycore_global_objects.h>
#include "internal/pycore_interp.h"
// #include "pytypedefs.h"

#define NUM_GENERATIONS 3

typedef struct _gc_runtime_state GCState;

static GCState * get_gc_state(void)
{
    PyInterpreterState *interp = PyInterpreterState_Get();
    return &interp->gc;
}

int generation_to_collect(int multiplier) {
    GCState * gcstate = get_gc_state();

    for (int i = NUM_GENERATIONS-1; i >= 0; i--) {
        if (gcstate->generations[i].count * multiplier > gcstate->generations[i].threshold) {
            return i;
        }
    }
    return -1;
}
