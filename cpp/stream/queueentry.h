#pragma once
#include <Python.h>
#include <cassert>
#include <cstdint>

namespace retracesoftware_stream {

    // Tagged word-sized queue protocol.
    //
    // Layout:
    //   bit 0      : 0 => pointer payload, 1 => tagged immediate/control
    //   bits 1..2  : subtype when bit 0 is set
    //   bits 3..   : payload
    //
    // Tagged subtypes:
    //   00 = binding create
    //   01 = binding lookup
    //   10 = binding delete
    //   11 = command header
    //
    // Pointer payloads remain raw object pointers whose low bit is zero due
    // to object alignment. Tagged entries inline either a binding handle or a
    // command opcode/length payload.
    //
    using QEntry = uintptr_t;
    using Ref = PyObject*;
    using BindingHandle = uint64_t;

    static constexpr QEntry ENTRY_TAG_BIT = 0x1;
    static constexpr int ENTRY_KIND_SHIFT = 1;
    static constexpr QEntry ENTRY_KIND_MASK = 0x3;
    static constexpr int ENTRY_PAYLOAD_SHIFT = 3;
    static constexpr int CMD_SHIFT = ENTRY_PAYLOAD_SHIFT;
    static constexpr int CMD_BITS  = 5;
    static constexpr int LEN_SHIFT = CMD_SHIFT + CMD_BITS;

    enum EntryKind : uint32_t {
        ENTRY_BIND = 0,
        ENTRY_REF = 1,
        ENTRY_DELETE = 2,
        ENTRY_COMMAND = 3,
    };

    static constexpr BindingHandle MAX_INLINE_BINDING_HANDLE =
        static_cast<BindingHandle>(UINTPTR_MAX >> ENTRY_PAYLOAD_SHIFT);

    inline bool is_pointer_entry(QEntry e) { return (e & ENTRY_TAG_BIT) == 0; }
    inline bool is_tagged_entry(QEntry e) { return !is_pointer_entry(e); }
    inline uint32_t kind_of(QEntry e) {
        return static_cast<uint32_t>((e >> ENTRY_KIND_SHIFT) & ENTRY_KIND_MASK);
    }
    inline bool is_bind_entry(QEntry e) {
        return is_tagged_entry(e) && kind_of(e) == ENTRY_BIND;
    }
    inline bool is_ref_entry(QEntry e) {
        return is_tagged_entry(e) && kind_of(e) == ENTRY_REF;
    }
    inline bool is_delete_entry(QEntry e) {
        return is_tagged_entry(e) && kind_of(e) == ENTRY_DELETE;
    }
    inline bool is_command_entry(QEntry e) {
        return is_tagged_entry(e) && kind_of(e) == ENTRY_COMMAND;
    }

    inline PyObject* as_object(QEntry e) {
        return reinterpret_cast<PyObject*>(e);
    }

    inline PyObject* as_payload_obj(QEntry e) { return reinterpret_cast<PyObject*>(e); }
    inline void* as_payload_raw_ptr(QEntry e) { return reinterpret_cast<void*>(e); }
    inline PyThreadState* as_payload_tstate(QEntry e) { return reinterpret_cast<PyThreadState*>(e); }

    inline QEntry object_entry(PyObject* p) { return reinterpret_cast<QEntry>(p); }
    inline QEntry payload_ptr_entry(void* p) { return (QEntry)p; }

    inline BindingHandle as_binding_handle(QEntry e) {
        return static_cast<BindingHandle>(e >> ENTRY_PAYLOAD_SHIFT);
    }

    inline QEntry bind_entry(BindingHandle handle) {
        assert(handle <= MAX_INLINE_BINDING_HANDLE);
        return ENTRY_TAG_BIT
             | ((QEntry)ENTRY_BIND << ENTRY_KIND_SHIFT)
             | ((QEntry)handle << ENTRY_PAYLOAD_SHIFT);
    }

    inline QEntry ref_entry(BindingHandle handle) {
        assert(handle <= MAX_INLINE_BINDING_HANDLE);
        return ENTRY_TAG_BIT
             | ((QEntry)ENTRY_REF << ENTRY_KIND_SHIFT)
             | ((QEntry)handle << ENTRY_PAYLOAD_SHIFT);
    }

    inline QEntry delete_entry(BindingHandle handle) {
        assert(handle <= MAX_INLINE_BINDING_HANDLE);
        return ENTRY_TAG_BIT
             | ((QEntry)ENTRY_DELETE << ENTRY_KIND_SHIFT)
             | ((QEntry)handle << ENTRY_PAYLOAD_SHIFT);
    }

    inline QEntry cmd_entry(uint32_t cmd, uint32_t len = 0) {
        return ENTRY_TAG_BIT
             | ((QEntry)ENTRY_COMMAND << ENTRY_KIND_SHIFT)
             | ((QEntry)cmd << CMD_SHIFT)
             | ((QEntry)len << LEN_SHIFT);
    }

    inline uint32_t cmd_of(QEntry e) {
        return (uint32_t)((e >> CMD_SHIFT) & ((1U << CMD_BITS) - 1));
    }
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

    inline bool is_retrace_patched_type(PyTypeObject* tp) {
        int status = PyObject_HasAttrString(reinterpret_cast<PyObject*>(tp), "__retrace_system__");
        if (status < 0) {
            PyErr_Clear();
            return false;
        }
        return status == 1;
    }

    enum Cmd : uint32_t {
        CMD_INTERN,
        CMD_FLUSH,
        CMD_SHUTDOWN,
        CMD_LIST,
        CMD_TUPLE,
        CMD_DICT,
        CMD_HEARTBEAT,
    };

}
