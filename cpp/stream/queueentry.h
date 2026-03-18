#pragma once
#include <Python.h>
#include <cstdint>

namespace retracesoftware_stream {

    // Tagged word-sized queue protocol.
    //
    // Pointer payloads use the low 2 bits as a small tag:
    //   00 = owned object
    //   01 = bound ref
    //   10 = immortal object
    //   11 = escaped / command entry
    //
    // Command headers occupy the escaped tag and carry opcode plus optional
    // length metadata in the upper bits.
    //
    using QEntry = uintptr_t;
    using Ref = void*;

    enum PointerKind : uintptr_t {
        PTR_OBJECT = 0x0,
        PTR_REF = 0x1,
        PTR_IMMORTAL = 0x2,
        PTR_ESCAPED = 0x3,
    };

    static constexpr QEntry POINTER_KIND_MASK = 0x3;
    static constexpr QEntry ENTRY_TAG_MASK = POINTER_KIND_MASK;
    static constexpr QEntry ENTRY_TAGGED = PTR_ESCAPED;
    static constexpr int CMD_SHIFT = 2;
    static constexpr int CMD_BITS  = 5;
    static constexpr int LEN_SHIFT = 7;

    inline PointerKind pointer_kind_of(QEntry e) {
        return static_cast<PointerKind>(e & POINTER_KIND_MASK);
    }
    inline bool is_command_entry(QEntry e) { return pointer_kind_of(e) == PTR_ESCAPED; }
    inline bool is_pointer_entry(QEntry e) { return !is_command_entry(e); }

    inline PyObject* as_object(QEntry e) {
        return reinterpret_cast<PyObject*>(e & ~POINTER_KIND_MASK);
    }
    inline Ref as_ref(QEntry e) {
        return reinterpret_cast<Ref>(e & ~POINTER_KIND_MASK);
    }
    inline PyObject* as_bind_obj(QEntry e) { return reinterpret_cast<PyObject*>(e & ~POINTER_KIND_MASK); }

    inline PyObject* as_payload_obj(QEntry e) { return reinterpret_cast<PyObject*>(e); }
    inline void* as_payload_raw_ptr(QEntry e) { return reinterpret_cast<void*>(e); }
    inline PyThreadState* as_payload_tstate(QEntry e) { return reinterpret_cast<PyThreadState*>(e); }

    inline QEntry object_entry(PyObject* p) { return reinterpret_cast<QEntry>(p) | PTR_OBJECT; }
    inline QEntry ref_entry(Ref ref) { return reinterpret_cast<QEntry>(ref) | PTR_REF; }
    inline QEntry immortal_entry(PyObject* p) { return reinterpret_cast<QEntry>(p) | PTR_IMMORTAL; }
    inline QEntry bind_entry(PyObject* p) { return reinterpret_cast<QEntry>(p) | PTR_REF; }
    inline QEntry payload_ptr_entry(void* p) { return (QEntry)p; }

    inline QEntry cmd_entry(uint32_t cmd, uint32_t len = 0) {
        return ENTRY_TAGGED
             | ((QEntry)cmd << CMD_SHIFT)
             | ((QEntry)len << LEN_SHIFT);
    }

    inline uint32_t cmd_of(QEntry e) {
        return (uint32_t)((e >> CMD_SHIFT) & ((1U << CMD_BITS) - 1));
    }
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

    inline bool is_retrace_patched_type(PyTypeObject* tp) {
        int status = PyObject_HasAttrString(reinterpret_cast<PyObject*>(tp), "__retrace_system__");
        if (status < 0) {
            PyErr_Clear();
            return false;
        }
        return status == 1;
    }

    enum Cmd : uint32_t {
        CMD_BIND,
        CMD_INTERN,
        CMD_DELETE,
        CMD_NEW_EXT_WRAPPED,
        CMD_NEW_PATCHED,
        CMD_THREAD_SWITCH,
        CMD_FLUSH,
        CMD_SHUTDOWN,
        CMD_LIST,
        CMD_TUPLE,
        CMD_DICT,
        CMD_HEARTBEAT,
    };

}
