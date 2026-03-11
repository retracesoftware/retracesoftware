#pragma once
#include <Python.h>
#include <cstdint>

#if PY_VERSION_HEX >= 0x030C0000
    inline bool is_immortal(PyObject* obj) { return _Py_IsImmortal(obj); }
#else
    inline bool is_immortal(PyObject*) { return false; }
#endif

namespace retracesoftware_stream {

    // Tagged word-sized queue protocol.
    //
    // Each queue entry is one machine word (uintptr_t).
    //
    // 64-bit: 3-bit tag in low bits (8-byte aligned pointers).
    //   Tag 0b000  TAG_OBJECT      PyObject* (incref'd, persister serializes)
    //   Tag 0b001  TAG_DELETE      PyObject* identity (no incref, deletion notification)
    //   Tag 0b010  TAG_THREAD      PyThreadState* (no incref, thread identity stamp)
    //   Tag 0b011  TAG_PICKLED     PyObject* (incref'd bytes, persister writes pre-pickled)
    //   Tag 0b100  TAG_NEW_HANDLE  PyObject* (incref'd, persister registers new handle)
    //   Tag 0b101  TAG_NEW_PATCHED borrowed PyObject* identity followed by
    //                               a borrowed obj_entry(type) for serialization
    //   Tag 0b110  TAG_BIND        borrowed PyObject* identity
    //   Tag 0b111  TAG_COMMAND     non-pointer: [len:32][cmd:29][tag:3]
    //
    // 32-bit: 2-bit tag in low bits (4-byte aligned pointers).
    //   Tag 0b00   TAG_OBJECT      PyObject*
    //   Tag 0b01   TAG_DELETE      PyObject* identity
    //   Tag 0b10   TAG_THREAD      PyThreadState*
    //   Tag 0b11   TAG_COMMAND     non-pointer: [len:26][cmd:4][tag:2]
    //   PICKLED and NEW_HANDLE are encoded as CMD_* followed by an incref'd
    //   obj_entry pointer. BIND is CMD_BIND followed by a borrowed obj_entry(obj).
    //   NEW_PATCHED is CMD_NEW_PATCHED followed by borrowed obj_entry(obj) and
    //   borrowed obj_entry(type).

    using QEntry = uintptr_t;

#if SIZEOF_VOID_P >= 8
    static constexpr QEntry TAG_MASK       = 0x7;
    static constexpr QEntry TAG_OBJECT     = 0;
    static constexpr QEntry TAG_DELETE     = 1;
    static constexpr QEntry TAG_THREAD     = 2;
    static constexpr QEntry TAG_PICKLED     = 3;
    static constexpr QEntry TAG_NEW_HANDLE  = 4;
    static constexpr QEntry TAG_NEW_PATCHED = 5;
    static constexpr QEntry TAG_BIND        = 6;
    static constexpr QEntry TAG_COMMAND     = 7;

    static constexpr int CMD_SHIFT = 3;
    static constexpr int CMD_BITS  = 29;
    static constexpr int LEN_SHIFT = 32;
#elif SIZEOF_VOID_P == 4
    static constexpr QEntry TAG_MASK    = 0x3;
    static constexpr QEntry TAG_OBJECT  = 0;
    static constexpr QEntry TAG_DELETE  = 1;
    static constexpr QEntry TAG_THREAD  = 2;
    static constexpr QEntry TAG_COMMAND = 3;

    static constexpr int CMD_SHIFT = 2;
    static constexpr int CMD_BITS  = 4;
    static constexpr int LEN_SHIFT = 6;
#else
    #error "Unsupported pointer size"
#endif

    inline QEntry tag_of(QEntry e)              { return e & TAG_MASK; }
    inline PyObject* as_ptr(QEntry e)           { return (PyObject*)(e & ~TAG_MASK); }
    inline PyThreadState* as_tstate(QEntry e)   { return (PyThreadState*)(e & ~TAG_MASK); }

    inline QEntry obj_entry(PyObject* p)            { return (QEntry)p; }
    inline QEntry delete_entry(PyObject* p)         { return (QEntry)p | TAG_DELETE; }
    inline QEntry thread_entry(PyThreadState* t)    { return (QEntry)t | TAG_THREAD; }

#if SIZEOF_VOID_P >= 8
    inline QEntry pickled_entry(PyObject* p)        { return (QEntry)p | TAG_PICKLED; }
    inline QEntry new_handle_entry(PyObject* p)     { return (QEntry)p | TAG_NEW_HANDLE; }
    inline QEntry new_patched_entry(PyObject* p)    { return (QEntry)p | TAG_NEW_PATCHED; }
    inline QEntry bind_entry(PyObject* p)           { return (QEntry)p | TAG_BIND; }
#endif

    inline QEntry cmd_entry(uint32_t cmd, uint32_t len = 0) {
        return ((QEntry)len << LEN_SHIFT) | ((QEntry)cmd << CMD_SHIFT) | TAG_COMMAND;
    }

    inline uint32_t cmd_of(QEntry e) { return (uint32_t)((e >> CMD_SHIFT) & ((1U << CMD_BITS) - 1)); }
    inline uint32_t len_of(QEntry e) { return (uint32_t)(e >> LEN_SHIFT); }

    inline int64_t estimate_long_size(PyObject* obj) {
        return 28;
    }

    inline int64_t estimate_float_size(PyObject* obj) {
        return 24;
    }

    inline int64_t estimate_unicode_size(PyObject* obj) {
        return (int64_t)(sizeof(PyObject) + PyUnicode_GET_LENGTH(obj) * PyUnicode_KIND(obj));
    }

    inline int64_t estimate_bytes_size(PyObject* obj) {
        return (int64_t)(sizeof(PyObject) + PyBytes_GET_SIZE(obj));
    }

    inline int64_t estimate_memory_view_size(PyObject* obj) {
        return (int64_t)(sizeof(PyObject) + PyMemoryView_GET_BUFFER(obj)->len);
    }

    inline int64_t estimate_stream_handle_size(PyObject* obj) {
        return 64;
    }

    inline bool is_retrace_patched_type(PyTypeObject* tp) {
        int status = PyObject_HasAttrString(reinterpret_cast<PyObject*>(tp), "__retrace_system__");
        if (status < 0) {
            PyErr_Clear();
            return false;
        }
        return status == 1;
    }

    inline int64_t estimate_size(PyObject* obj) {
        if (is_immortal(obj)) return 0;
        PyTypeObject* tp = Py_TYPE(obj);
        if (tp == &PyLong_Type)    return estimate_long_size(obj);
        if (tp == &PyFloat_Type)   return estimate_float_size(obj);
        if (tp == &PyUnicode_Type) return estimate_unicode_size(obj);
        if (tp == &PyBytes_Type)   return estimate_bytes_size(obj);
        if (tp == &PyMemoryView_Type) return estimate_memory_view_size(obj);
        if (tp == &StreamHandle_Type) return estimate_stream_handle_size(obj);
        if (is_retrace_patched_type(tp)) return 64;
        return -1;
    }

    enum Cmd : uint32_t {
        CMD_HANDLE_REF,
        CMD_HANDLE_DELETE,

        CMD_FLUSH,
        CMD_SHUTDOWN,

        CMD_LIST,
        CMD_TUPLE,
        CMD_DICT,
        CMD_HEARTBEAT,

        CMD_PICKLED,
        CMD_NEW_HANDLE,
        CMD_NEW_PATCHED,
        CMD_BIND,
        CMD_SERIALIZE_ERROR,
    };

}
