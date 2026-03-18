#include "stream.h"
#include "wireformat.h"
#include "framed_writer.h"

#include <cstdio>
#include <cstdlib>

namespace retracesoftware_stream {

    inline bool bind_trace_enabled() {
        static int enabled = -1;
        if (enabled == -1) {
            const char* value = std::getenv("RETRACE_BIND_TRACE");
            enabled = (value && value[0] && value[0] != '0') ? 1 : 0;
        }
        return enabled == 1;
    }

    inline const char* bind_label(PyObject* obj) {
        if (!obj) return "<null>";
        if (PyType_Check(obj)) return reinterpret_cast<PyTypeObject*>(obj)->tp_name;
        return Py_TYPE(obj)->tp_name;
    }

    uint64_t StreamHandle_index(PyObject*);

    void on_free(void* obj);
    void generic_free(void* obj);
    void PyObject_GC_Del_Wrapper(void* obj);
    void PyObject_Free_Wrapper(void* obj);
    bool is_patched(freefunc func);
    void patch_free(PyTypeObject* cls);

    inline bool is_retrace_patched_type_for_stream(PyTypeObject* tp) {
        int status = PyObject_HasAttrString(reinterpret_cast<PyObject*>(tp), "__retrace_system__");
        if (status < 0) {
            PyErr_Clear();
            return false;
        }
        return status == 1;
    }
}
