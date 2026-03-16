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
    //   Inline pointer entry (8-byte aligned pointer only)
    //
    //     63                         3 2       0
    //    +----------------------------+---------+
    //    |        pointer bits        |  kind   |
    //    +----------------------------+---------+
    //
    //     kind is a 3-bit PointerKind in the range 0x0..0x6.
    //
    //   Extended entry
    //
    //     63                    6 5       3 2       0
    //    +-----------------------+---------+---------+
    //    |    len / metadata     | extkind |   111   |
    //    +-----------------------+---------+---------+
    //
    //     low 3 bits  : 0x7 means "extended entry"
    //     next 3 bits : extended kind
    //     upper bits  : length / metadata
    //
    //   Command header
    //
    //     63                   10 9      6 5       3 2       0
    //    +-----------------------+--------+---------+---------+
    //    |    len / metadata     |  cmd   |   111   |   111   |
    //    +-----------------------+--------+---------+---------+
    //
    //     The command family is itself an extended kind:
    //       tag     = 0x7
    //       extkind = 0x7
    //       cmd     = command id
    //
    //   Escaped pointer sequence
    //
    //     entry 0:
    //       extended header with extkind = PointerKind
    //
    //     entry 1:
    //       payload_ptr_entry(raw_pointer)
    //
    using QEntry = uintptr_t;
    using Ref = void*;

    static constexpr QEntry TAG_MASK = 0x7;
    static constexpr QEntry TAG_EXTENDED = 0x7;
    static constexpr QEntry POINTER_KIND_MASK = 0x7;
    static constexpr QEntry EXT_KIND_MASK = 0x7;

    enum PointerKind : uint32_t {
        PTR_OWNED_OBJECT       = 0x0,
        PTR_HANDLE_REF         = 0x1,
        PTR_BIND               = 0x2,
        PTR_IMMORTAL           = 0x3,
        PTR_BOUND_REF          = 0x4,
        PTR_BOUND_REF_DELETE   = 0x5,
        PTR_NEW_EXT_WRAPPED    = 0x6,
    };

    static constexpr int EXT_KIND_SHIFT = 3;
    static constexpr uint32_t EXT_KIND_COMMAND = 0x7;
    static constexpr int CMD_SHIFT = 6;
    static constexpr int CMD_BITS  = 4;
    static constexpr int LEN_SHIFT = 10;

    inline QEntry tag_of(QEntry e) { return e & TAG_MASK; }
    inline bool is_extended_entry(QEntry e) { return tag_of(e) == TAG_EXTENDED; }
    inline uint32_t ext_kind_of(QEntry e) { return (uint32_t)((e >> EXT_KIND_SHIFT) & EXT_KIND_MASK); }
    inline bool is_command_entry(QEntry e) {
        return is_extended_entry(e) && ext_kind_of(e) == EXT_KIND_COMMAND;
    }
    inline bool is_escaped_pointer_entry(QEntry e) {
        return is_extended_entry(e) && ext_kind_of(e) != EXT_KIND_COMMAND;
    }
    inline PointerKind pointer_kind_of(QEntry e) { return (PointerKind)(e & POINTER_KIND_MASK); }
    inline PointerKind escaped_pointer_kind_of(QEntry e) { return (PointerKind)ext_kind_of(e); }

    inline bool supports_inline_pointer_kind(const void* ptr) {
        return ((((uintptr_t)ptr) & 0x4) == 0);
    }

    inline PyObject* as_owned_obj(QEntry e) { return (PyObject*)(e & ~POINTER_KIND_MASK); }
    inline Ref as_handle_ref(QEntry e) { return (Ref)(e & ~POINTER_KIND_MASK); }
    inline PyObject* as_bind_obj(QEntry e) { return (PyObject*)(e & ~POINTER_KIND_MASK); }
    inline Ref as_bound_ref(QEntry e) { return (Ref)(e & ~POINTER_KIND_MASK); }
    inline Ref as_bound_ref_delete(QEntry e) { return (Ref)(e & ~POINTER_KIND_MASK); }

    inline PyObject* as_payload_obj(QEntry e) { return reinterpret_cast<PyObject*>(e); }
    inline void* as_payload_raw_ptr(QEntry e) { return reinterpret_cast<void*>(e); }
    inline PyThreadState* as_payload_tstate(QEntry e) { return reinterpret_cast<PyThreadState*>(e); }

    inline QEntry owned_obj_entry(PyObject* p) { return (QEntry)p | PTR_OWNED_OBJECT; }
    inline QEntry handle_ref_entry(Ref handle) { return (QEntry)handle | PTR_HANDLE_REF; }
    inline QEntry bind_entry(PyObject* p) { return (QEntry)p | PTR_BIND; }
    inline QEntry bound_ref_entry(Ref ref) { return (QEntry)ref | PTR_BOUND_REF; }
    inline QEntry bound_ref_delete_entry(Ref ref) { return (QEntry)ref | PTR_BOUND_REF_DELETE; }
    inline QEntry payload_ptr_entry(void* p) { return (QEntry)p; }
    inline QEntry escaped_ptr_entry(PointerKind kind) {
        return ((QEntry)kind << EXT_KIND_SHIFT) | TAG_EXTENDED;
    }

    inline QEntry cmd_entry(uint32_t cmd, uint32_t len = 0) {
        return ((QEntry)len << LEN_SHIFT)
             | ((QEntry)cmd << CMD_SHIFT)
             | ((QEntry)EXT_KIND_COMMAND << EXT_KIND_SHIFT)
             | TAG_EXTENDED;
    }

    inline uint32_t cmd_of(QEntry e) { return (uint32_t)((e >> CMD_SHIFT) & ((1U << CMD_BITS) - 1)); }
    inline uint32_t len_of(QEntry e) { return (uint32_t)(e >> LEN_SHIFT); }
    inline Ref handle_from_index(uintptr_t index) {
        return (Ref)((index + 1) << 3);
    }

    inline uintptr_t index_of_handle(Ref handle) {
        return (((uintptr_t)handle) >> 3) - 1;
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

        CMD_HANDLE_DELETE,
        
        CMD_DELETE,
        CMD_THREAD,
        CMD_PICKLED,
        CMD_NEW_HANDLE,
        CMD_NEW_PATCHED,
        CMD_SERIALIZE_ERROR,
    };

}
