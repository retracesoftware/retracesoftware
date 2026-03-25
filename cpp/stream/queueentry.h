#pragma once
#include <Python.h>
#include <cstdint>

namespace retracesoftware_stream {

    // Tagged word-sized queue protocol.
    //
    // Pointer payloads use the low 2 bits as a small tag:
    //   bit 0 set => command header
    //   bit 1 set => unowned pointer
    //
    // That yields:
    //   00 = owned pointer
    //   01 = command header
    //   10 = unowned pointer
    //   11 = reserved / invalid
    //
    // Command headers occupy the command tag and carry opcode plus optional
    // length metadata in the upper bits.
    //
    using QEntry = uintptr_t;
    using Ref = PyObject*;

    static constexpr QEntry ENTRY_COMMAND_BIT = 0x1;
    static constexpr QEntry ENTRY_UNOWNED_BIT = 0x2;
    static constexpr QEntry ENTRY_TAG_MASK = ENTRY_COMMAND_BIT | ENTRY_UNOWNED_BIT;
    static constexpr QEntry ENTRY_COMMAND = ENTRY_COMMAND_BIT;
    static constexpr int CMD_SHIFT = 2;
    static constexpr int CMD_BITS  = 5;
    static constexpr int LEN_SHIFT = 7;

    inline bool is_command_entry(QEntry e) { return (e & ENTRY_TAG_MASK) == ENTRY_COMMAND; }
    inline bool is_unowned_entry(QEntry e) { return (e & ENTRY_TAG_MASK) == ENTRY_UNOWNED_BIT; }
    inline bool is_pointer_entry(QEntry e) { return !is_command_entry(e); }

    inline PyObject* as_object(QEntry e) {
        return reinterpret_cast<PyObject*>(e & ~ENTRY_TAG_MASK);
    }

    inline PyObject* as_payload_obj(QEntry e) { return reinterpret_cast<PyObject*>(e); }
    inline void* as_payload_raw_ptr(QEntry e) { return reinterpret_cast<void*>(e); }
    inline PyThreadState* as_payload_tstate(QEntry e) { return reinterpret_cast<PyThreadState*>(e); }

    inline QEntry object_entry(PyObject* p) { return reinterpret_cast<QEntry>(p); }
    inline QEntry unowned_entry(PyObject* p) { return reinterpret_cast<QEntry>(p) | ENTRY_UNOWNED_BIT; }
    inline QEntry payload_ptr_entry(void* p) { return (QEntry)p; }

    inline QEntry cmd_entry(uint32_t cmd, uint32_t len = 0) {
        return ENTRY_COMMAND
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
        CMD_BIND,
        CMD_INTERN,
        CMD_DELETE,
        CMD_THREAD_SWITCH,
        CMD_FLUSH,
        CMD_SHUTDOWN,
        CMD_LIST,
        CMD_TUPLE,
        CMD_DICT,
        CMD_HEARTBEAT,
    };

}
