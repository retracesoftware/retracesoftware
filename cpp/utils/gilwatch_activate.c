#define Py_BUILD_CORE
#include <Python.h>
#include <internal/pycore_interp.h>
#include <internal/pycore_gil.h>
#include <pthread.h>

pthread_mutex_t * gilwatch_get_gil_mutex(void) {
#if PY_VERSION_HEX >= 0x030C0000
    PyInterpreterState *interp = PyInterpreterState_Get();
    return &interp->_gil.mutex;
#else
    return NULL;
#endif
}
