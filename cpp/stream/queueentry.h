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
    // Each queue entry is one machine word (uintptr_t) with a uniform 2-bit
    // tag layout on both 32-bit and 64-bit builds:
    //
    //   Tag 0b00  TAG_OBJECT         PyObject* payload (or command payload word)
    //   Tag 0b01  TAG_HANDLE_REF     tagged Ref
    //   Tag 0b10  TAG_HANDLE_DELETE  tagged Ref
    //   Tag 0b11  TAG_COMMAND        non-pointer: [len/payload][cmd:4][tag:2]
    //
    // Commands that need extra data use TAG_COMMAND followed by one or more
    // payload words. Pointer payloads are emitted as TAG_OBJECT words and are
    // only interpreted according to the active command.

    using QEntry = uintptr_t;
    using Ref = void*;

    static constexpr QEntry TAG_MASK          = 0x3;
    static constexpr QEntry TAG_OBJECT        = 0;
    static constexpr QEntry TAG_HANDLE_REF    = 1;
    static constexpr QEntry TAG_HANDLE_DELETE = 2;
    static constexpr QEntry TAG_COMMAND       = 3;

    static constexpr int CMD_SHIFT = 2;
    static constexpr int CMD_BITS  = 4;
    static constexpr int LEN_SHIFT = 6;

    inline QEntry tag_of(QEntry e)              { return e & TAG_MASK; }
    inline PyObject* as_ptr(QEntry e)           { return (PyObject*)(e & ~TAG_MASK); }
    inline void* as_raw_ptr(QEntry e)           { return (void*)(e & ~TAG_MASK); }
    inline PyThreadState* as_tstate(QEntry e)   { return (PyThreadState*)(e & ~TAG_MASK); }

    inline QEntry obj_entry(PyObject* p)            { return (QEntry)p; }
    inline QEntry raw_ptr_entry(void* p)            { return (QEntry)p; }
    inline QEntry handle_ref_entry(Ref handle)   { return (QEntry)handle | TAG_HANDLE_REF; }
    inline QEntry handle_delete_entry(Ref handle) { return (QEntry)handle | TAG_HANDLE_DELETE; }

    inline QEntry cmd_entry(uint32_t cmd, uint32_t len = 0) {
        return ((QEntry)len << LEN_SHIFT) | ((QEntry)cmd << CMD_SHIFT) | TAG_COMMAND;
    }

    inline uint32_t cmd_of(QEntry e) { return (uint32_t)((e >> CMD_SHIFT) & ((1U << CMD_BITS) - 1)); }
    inline uint32_t len_of(QEntry e) { return (uint32_t)(e >> LEN_SHIFT); }
    inline Ref handle_ref_of(QEntry e) { return (Ref)(e & ~TAG_MASK); }
    inline Ref handle_delete_of(QEntry e) { return (Ref)(e & ~TAG_MASK); }

    inline Ref handle_from_index(uintptr_t index) {
        return (Ref)((index + 1) << CMD_SHIFT);
    }

    inline uintptr_t index_of_handle(Ref handle) {
        return (((uintptr_t)handle) >> CMD_SHIFT) - 1;
    }

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
        CMD_FLUSH,
        CMD_SHUTDOWN,

        CMD_LIST,
        CMD_TUPLE,
        CMD_DICT,
        CMD_HEARTBEAT,

        CMD_EXTERNAL_WRAPPED,
        
        CMD_DELETE,
        CMD_THREAD,
        CMD_PICKLED,
        CMD_NEW_HANDLE,
        CMD_NEW_PATCHED,
        CMD_BIND,
        CMD_SERIALIZE_ERROR,
    };

}
