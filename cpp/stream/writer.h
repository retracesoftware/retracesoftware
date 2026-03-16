#include "stream.h"
#include "wireformat.h"
#include "framed_writer.h"
#include <vector>
#include <cstring>
#include <chrono>
#include <cerrno>
#include <cstdlib>
#include <cstdio>

#ifndef _WIN32
#include <unistd.h>
#endif

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

    uint64_t StreamHandle_index(PyObject *);

    void on_free(void * obj);
    void generic_free(void * obj);
    void PyObject_GC_Del_Wrapper(void * obj);
    void PyObject_Free_Wrapper(void * obj);
    bool is_patched(freefunc func);
    void patch_free(PyTypeObject * cls);
    inline bool is_retrace_patched_type_for_stream(PyTypeObject* tp) {
        int status = PyObject_HasAttrString(reinterpret_cast<PyObject*>(tp), "__retrace_system__");
        if (status < 0) {
            PyErr_Clear();
            return false;
        }
        return status == 1;
    }

    class MessageStream {
        FramedWriter& writer;
        PyObject * serializer;
        map<PyObject *, int> bindings;
        int binding_counter = 0;
        size_t bytes_written = 0;
        bool verbose = false;
        bool quit_on_error = false;

        map<PyObject *, uint16_t> interned_index;
        uint16_t interned_counter = 0;

        static constexpr int MAX_WRITE_DEPTH = 64;
        int write_depth = 0;

        struct DepthGuard {
            int& depth;
            DepthGuard(int& d) : depth(d) { ++depth; }
            ~DepthGuard() { --depth; }
        };

        static PyObject* pickle_dumps() {
            static PyObject* dumps = nullptr;
            if (!dumps) {
                PyObject* mod = PyImport_ImportModule("pickle");
                if (!mod) return nullptr;
                dumps = PyObject_GetAttrString(mod, "dumps");
                Py_DECREF(mod);
            }
            return dumps;
        }

        // --- Primitive write helpers (delegate to FramedWriter) ---

        inline void emit(uint8_t v) { writer.write_byte(v); bytes_written++; }
        inline void emit(int8_t v) { emit((uint8_t)v); }
        inline void emit(uint16_t v) { writer.write_uint16(v); bytes_written += 2; }
        inline void emit(int16_t v) { emit((uint16_t)v); }
        inline void emit(uint32_t v) { writer.write_uint32(v); bytes_written += 4; }
        inline void emit(int32_t v) { emit((uint32_t)v); }
        inline void emit(uint64_t v) { writer.write_uint64(v); bytes_written += 8; }
        inline void emit(int64_t v) { emit((uint64_t)v); }
        inline void emit(double d) { writer.write_float64(d); bytes_written += 8; }

        inline void emit_bytes(const uint8_t* data, Py_ssize_t size) {
            writer.write_bytes(data, size);
            bytes_written += size;
        }

        inline void emit_control(Control value) { emit(value.raw); }

        inline void emit(Control control) { emit(control.raw); }

        inline void emit(FixedSizeTypes obj) {
            if (verbose) {
                printf("%s ", FixedSizeTypes_Name(obj));
            }
            emit(create_fixed_size(obj));
        }

        // --- Wire-format encoding ---

        void write_unsigned_number(SizedTypes type, uint64_t l) {
            write_size(type, l);
        }

        void write_size(SizedTypes type, Py_ssize_t size) {
            assert(type < 16);

            if (verbose) {
                printf("%s(%i) ", SizedTypes_Name(type), (int)size);
            }

            Control control;
            control.Sized.type = type;

            if (size <= 11) {
                control.Sized.size = (Sizes)size;
                emit_control(control);
            } else {
                if (size < UINT8_MAX) {
                    control.Sized.size = Sizes::ONE_BYTE_SIZE;
                    emit_control(control);
                    emit((int8_t)size);
                } else if (size < UINT16_MAX) {
                    control.Sized.size = Sizes::TWO_BYTE_SIZE;
                    emit_control(control);
                    emit((int16_t)size);
                } else if (size < UINT32_MAX) {
                    control.Sized.size = Sizes::FOUR_BYTE_SIZE;
                    emit_control(control);
                    emit((int32_t)size);
                } else {
                    control.Sized.size = Sizes::EIGHT_BYTE_SIZE;
                    emit_control(control);
                    emit((int64_t)size);
                }
            }
        }

        void write_handle_ref(uint64_t handle) {
            write_unsigned_number(SizedTypes::HANDLE, handle);
        }

        void write_lookup(int ref) {
            write_unsigned_number(SizedTypes::BINDING, ref);
        }

        void write_bignum(PyObject * pylong) {
            Py_ssize_t nbits = _PyLong_NumBits(pylong);
            size_t expected = (nbits + 7) / 8;

            uint8_t *bignum = (uint8_t *)malloc(expected);
            if (!bignum) {
                PyErr_SetString(PyExc_MemoryError, "bignum malloc failed.");
                throw nullptr;
            }

            int little_endian = 0;
            int is_signed = 1;

            int b = _PyLong_AsByteArray(reinterpret_cast<PyLongObject *>(pylong), bignum, expected, little_endian, is_signed);
            if (b < 0) {
                free(bignum);
                throw nullptr;
            } else if ((size_t)b > expected) {
                PyErr_SetString(PyExc_RuntimeError,
                    "Unexpected bignum truncation after a size check.");
                free(bignum);
                throw nullptr;
            }

            try {
                write_size(SizedTypes::BIGINT, expected);
                emit_bytes((const uint8_t *)bignum, expected);
                free(bignum);
            } catch (...) {
                free(bignum);
                throw;
            }
        }

        void write_str_value(PyObject * obj) {
            Py_ssize_t size;
            const char * utf8 = PyUnicode_AsUTF8AndSize(obj, &size);
            write_size(SizedTypes::STR, (int)size);
            emit_bytes((const uint8_t *)utf8, size);
        }

        void write_expected(uint64_t i) {
            if (i < 255) {
                emit((uint8_t)i);
            } else {
                emit((uint8_t)255);
                emit(i);
            }
        }

        void write_int_value(PyObject * obj) {
            long l = PyLong_AsLong(obj);
            if (PyErr_Occurred()) {
                if (PyErr_ExceptionMatches(PyExc_OverflowError)) {
                    PyErr_Clear();
                    write_bignum(obj);
                } else {
                    throw nullptr;
                }
            } else {
                write_sized_int(l);
            }
        }

        void write_float_value(PyObject * obj) {
            emit(FixedSizeTypes::FLOAT);
            emit(PyFloat_AsDouble(obj));
        }

        void write_bytes_header(PyObject * obj) {
            write_size(SizedTypes::BYTES, PyBytes_GET_SIZE(obj));
        }

        void write_bytes_data(PyObject * obj) {
            emit_bytes((const uint8_t *)PyBytes_AsString(obj), PyBytes_GET_SIZE(obj));
        }

        void write_bytes_value(PyObject * obj) {
            write_bytes_header(obj);
            write_bytes_data(obj);
        }

        void write_pickled_value(PyObject * bytes) {
            write_size(SizedTypes::PICKLED, PyBytes_GET_SIZE(bytes));
            write_bytes_data(bytes);
        }

        void write_bool_value(PyObject * obj) {
            emit(obj == Py_True ? FixedSizeTypes::TRUE : FixedSizeTypes::FALSE);
        }

        void write_memory_view(PyObject * obj) {
            Py_buffer *view = PyMemoryView_GET_BUFFER(obj);
            assert(view->readonly);
            write_size(SizedTypes::BYTES, view->len);
            emit_bytes((const uint8_t *)view->buf, view->len);
        }

        void write_sized_int(int64_t l) {
            if (l >= 0) {
                write_unsigned_number(SizedTypes::UINT, l);
            } else if (l == -1) {
                emit_control(CreateFixedSize(FixedSizeTypes::NEG1));
            } else {
                emit_control(CreateFixedSize(FixedSizeTypes::INT64));
                emit(l);
            }
        }

        void pickle_fallback(PyObject * obj) {
            PyObject* dumps = pickle_dumps();
            if (dumps) {
                PyObject* pickled = PyObject_CallOneArg(dumps, obj);
                if (pickled) {
                    write_pickled_value(pickled);
                    Py_DECREF(pickled);
                    return;
                }
                if (quit_on_error) {
                    fprintf(stderr, "retrace: pickle_fallback error for <%s> (quit_on_error is set)\n",
                            Py_TYPE(obj)->tp_name);
                    PyErr_Print();
                    _exit(1);
                }
                PyErr_Clear();
            }
            emit(FixedSizeTypes::NONE);
        }

        void write_dict(PyObject * obj) {
            assert(PyDict_Check(obj));

            if (write_depth >= MAX_WRITE_DEPTH) {
                pickle_fallback(obj);
                return;
            }
            DepthGuard guard(write_depth);

            write_size(SizedTypes::DICT, PyDict_Size(obj));

            Py_ssize_t pos = 0;
            PyObject *key, *value;
            while (PyDict_Next(obj, &pos, &key, &value)) {
                write(key);
                write(value);
            }
        }

        void write_tuple(PyObject * obj) {
            assert(PyTuple_Check(obj));
            write_size(SizedTypes::TUPLE, PyTuple_GET_SIZE(obj));
            for (int i = 0; i < PyTuple_GET_SIZE(obj); i++) {
                write(PyTuple_GET_ITEM(obj, i));
            }
        }

        void write_list(PyObject * obj) {
            assert(PyList_Check(obj));

            if (write_depth >= MAX_WRITE_DEPTH) {
                pickle_fallback(obj);
                return;
            }
            DepthGuard guard(write_depth);

            write_size(SizedTypes::LIST, PyList_GET_SIZE(obj));
            for (int i = 0; i < PyList_GET_SIZE(obj); i++) {
                write(PyList_GET_ITEM(obj, i));
            }
        }

        void write_serialized(PyObject * obj) {
            PyObject * res;

            if (PyGC_IsEnabled()) {
                PyGC_Disable();
                res = PyObject_CallOneArg(serializer, obj);
                PyGC_Enable();
            } else {
                res = PyObject_CallOneArg(serializer, obj);
            }

            if (!res) {
                throw nullptr;
            } else {
                if (PyBytes_Check(res)) {
                    write_pickled_value(res);
                } else {
                    write(res);
                }
                Py_DECREF(res);
            }
        }

    public:

        MessageStream() = delete;

        MessageStream(FramedWriter& writer, PyObject * serializer, bool quit_on_error = false) :
            writer(writer),
            serializer(Py_XNewRef(serializer)),
            quit_on_error(quit_on_error) {
        }

        ~MessageStream() {
            Py_XDECREF(serializer);
            for (auto& [key, value] : interned_index) {
                Py_DECREF(key);
            }
        }

        void traverse(visitproc visit, void* arg) {
            if (serializer) visit(serializer, arg);
            for (auto& [key, value] : interned_index) {
                visit(key, arg);
            }
        }

        void gc_clear() {
            Py_CLEAR(serializer);
            for (auto& [key, value] : interned_index) {
                Py_DECREF(key);
            }
            interned_index.clear();
        }

        bool is_bound(PyObject * obj) const {
            return bindings.contains(obj);
        }

        void write_control(Control c) {
            emit(c);
        }

        void write_handle_delete(uint64_t delta) {
            write_unsigned_number(SizedTypes::DELETE, delta);
        }

        void write_handle_ref_by_index(uint64_t index) {
            write_handle_ref(index);
        }

        void write_bound_ref_by_index(int index) {
            write_lookup(index);
        }

        void write_new_handle(PyObject * obj) {
            emit(NewHandle);
            write(obj);
        }

        void write_stream_handle(PyObject * obj) {
            assert(Py_TYPE(obj) == &StreamHandle_Type);
            write_handle_ref(StreamHandle_index(obj));
        }

        void write_string(PyObject * obj) {
            assert(PyUnicode_Check(obj));

            if (PyUnicode_CHECK_INTERNED(obj)) {
                auto it = interned_index.find(obj);
                if (it != interned_index.end()) {
                    write_size(SizedTypes::STR_REF, it->second);
                    return;
                }
                interned_index[Py_NewRef(obj)] = interned_counter;
            }
            write_str_value(obj);
            interned_counter++;
        }

        void write(PyObject * obj) {
            assert(obj);

            if (obj == Py_None) emit(FixedSizeTypes::NONE);

            else if (Py_TYPE(obj) == &StreamHandle_Type) write_stream_handle(obj);
            else if (Py_TYPE(obj) == &PyUnicode_Type) write_string(obj);
            else if (Py_TYPE(obj) == &PyLong_Type) write_int_value(obj);

            else if (Py_TYPE(obj) == &PyBytes_Type) write_bytes_value(obj);
            else if (Py_TYPE(obj) == &PyBool_Type) write_bool_value(obj);
            else if (Py_TYPE(obj) == &PyTuple_Type) write_tuple(obj);
            else if (Py_TYPE(obj) == &PyList_Type) write_list(obj);
            else if (Py_TYPE(obj) == &PyDict_Type) write_dict(obj);

            else if (bindings.contains(obj)) write_lookup(bindings[obj]);
            else if (!PyType_Check(obj) && is_retrace_patched_type_for_stream(Py_TYPE(obj))) {
                bind(obj);
                write_lookup(bindings[obj]);
            }

            else if (Py_TYPE(obj) == &PyFloat_Type) write_float_value(obj);

            else if (Py_TYPE(obj) == &PyMemoryView_Type) write_memory_view(obj);

            else write_serialized(obj);
        }

        void bind(PyObject * obj) {
            assert(!bindings.contains(obj));
            if (bind_trace_enabled()) {
                std::fprintf(stderr,
                             "retrace-bind stream-bind-enter obj=%p label=%s counter=%d buffered=%zu\n",
                             (void*)obj,
                             bind_label(obj),
                             binding_counter,
                             writer.buffered());
                std::fflush(stderr);
            }

            bindings[obj] = binding_counter++;

            assert(bindings.contains((PyObject *)Py_TYPE(obj)));

            emit(FixedSizeTypes::BIND);
            if (bind_trace_enabled()) {
                std::fprintf(stderr,
                             "retrace-bind stream-bind-emitted obj=%p label=%s counter=%d buffered=%zu bytes=%llu\n",
                             (void*)obj,
                             bind_label(obj),
                             binding_counter,
                             writer.buffered(),
                             (unsigned long long)writer.bytes_written());
                std::fflush(stderr);
            }
        }

        void new_patched(PyObject * obj, PyObject * type) {
            assert(!bindings.contains(obj));

            bindings[obj] = binding_counter++;

            assert(bindings.contains(type));

            emit(FixedSizeTypes::NEW_PATCHED);
            int ref = bindings[type];
            write_lookup(ref);
        }

        bool object_freed(PyObject * obj) {
            auto it = bindings.find(obj);
            if (it != bindings.end()) {
                write_unsigned_number(SizedTypes::BINDING_DELETE, it->second);
                bindings.erase(it);
                return true;
            }
            return false;
        }

        bool delete_bound_ref_by_index(int index) {
            for (auto it = bindings.begin(); it != bindings.end(); ++it) {
                if (it->second == index) {
                    write_unsigned_number(SizedTypes::BINDING_DELETE, index);
                    bindings.erase(it);
                    return true;
                }
            }
            return false;
        }

        int binding_index(PyObject* obj) const {
            auto it = bindings.find(obj);
            return it == bindings.end() ? -1 : it->second;
        }

        void write_thread_switch(PyObject * thread_handle) {
            emit(ThreadSwitch);
            write(thread_handle);
        }

        inline size_t get_bytes_written() const { return bytes_written; }

        bool is_closed() const { return writer.is_closed(); }

        void close() { writer.flush(); }

        void flush() { writer.flush(); }

        void write_pre_pickled(PyObject* bytes_obj) {
            assert(PyBytes_Check(bytes_obj));
            write_pickled_value(bytes_obj);
        }

        void write_list_header(size_t n) { write_size(SizedTypes::LIST, n); }
        void write_tuple_header(size_t n) { write_size(SizedTypes::TUPLE, n); }
        void write_dict_header(size_t n) { write_size(SizedTypes::DICT, n); }
    };
}
