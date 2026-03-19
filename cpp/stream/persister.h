#pragma once

#include <Python.h>
#include <new>

#include "writer.h"

namespace retracesoftware_stream {
    void handle_write_error();
    void handle_debug_error(bool quit_on_error);
    void set_python_error_from_current_exception();
    
    class Persister : public PyObject {
        PyObject* framed_writer_obj = nullptr;
        FramedWriter* fw = nullptr;
        PyObject* serializer = nullptr;
        map<PyObject*, int> bindings;
        int binding_counter = 0;
        size_t bytes_written = 0;
        bool verbose = false;
        map<PyObject*, uint16_t> interned_index;
        uint16_t interned_counter = 0;
        
        inline void emit(uint8_t v) { fw->write_byte(v); bytes_written++; }
        inline void emit(int8_t v) { emit((uint8_t)v); }
        inline void emit(uint16_t v) { fw->write_uint16(v); bytes_written += 2; }
        inline void emit(int16_t v) { emit((uint16_t)v); }
        inline void emit(uint32_t v) { fw->write_uint32(v); bytes_written += 4; }
        inline void emit(int32_t v) { emit((uint32_t)v); }
        inline void emit(uint64_t v) { fw->write_uint64(v); bytes_written += 8; }
        inline void emit(int64_t v) { emit((uint64_t)v); }
        inline void emit(double d) { fw->write_float64(d); bytes_written += 8; }
        inline void emit_bytes(const uint8_t* data, Py_ssize_t size) {
            fw->write_bytes(data, size);
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

        bool write(PyObject* obj);
        bool write_fallback(PyObject* value);
        bool write_long(PyObject* value);
        void write_string(PyObject* obj);
        // void write_tuple_value(PyObject* obj);
        // void write_list_value(PyObject* obj);
        // void write_dict_value(PyObject* obj);
        void write_memory_view(PyObject* obj);
        void write_size(SizedTypes type, Py_ssize_t size);
        void write_unsigned_number(SizedTypes type, uint64_t value);
        void write_lookup(int ref);
        void write_str_value(PyObject* obj);
        void write_bytes_header(PyObject* obj);
        void write_bytes_data(PyObject* obj);
        void write_bytes_value(PyObject* obj);
        void write_pickled_value(PyObject* bytes);
        void write_bool_value(PyObject* obj);
        void write_sized_int(int64_t value);
        bool object_freed(PyObject* obj);

    public:
        Persister() {}
        ~Persister() = default;

        PyObject* writer_object() const { return framed_writer_obj; }
        FramedWriter* native_writer() const { return fw; }
        void reset_state();

        bool write_object(PyObject* obj);
        void write_ref(Ref ref);
        bool intern(PyObject* obj);
        void flush();
        void shutdown();

        void start_list(uint32_t len);
        void start_tuple(uint32_t len);
        void start_dict(uint32_t len);
        
        void write_heartbeat();
        bool write_new_ext_wrapped(PyTypeObject* type);
        void write_delete(Ref ref);
        bool write_thread_switch(PyObject* thread_handle);
        void write_pickled(PyObject* obj);
        void write_new_patched(PyObject* obj, PyObject* type);
        void bind(Ref ref);

        static PyObject* tp_new(PyTypeObject* type, PyObject*, PyObject*) {
            Persister* self = reinterpret_cast<Persister*>(type->tp_alloc(type, 0));
            if (!self) return nullptr;
            new (self) Persister();
            return reinterpret_cast<PyObject*>(self);
        }

        static int init(Persister* self, PyObject* args, PyObject* kwds) {
            PyObject* writer_obj;
            PyObject* serializer;
            PyObject* thread_key = nullptr;

            static const char* kwlist[] = {"writer", "serializer", "thread", nullptr};
            if (!PyArg_ParseTupleAndKeywords(
                    args, kwds, "OO|O", (char**)kwlist,
                    &writer_obj, &serializer, &thread_key)) {
                return -1;
            }
            (void)thread_key;

            FramedWriter* fw_ptr = FramedWriter_get(writer_obj);
            if (!fw_ptr) return -1;

            self->framed_writer_obj = Py_NewRef(writer_obj);
            self->fw = fw_ptr;
            self->serializer = Py_NewRef(serializer);
            return 0;
        }

        static int traverse(Persister* self, visitproc visit, void* arg) {
            Py_VISIT(self->framed_writer_obj);
            Py_VISIT(self->serializer);
            for (auto& [key, value] : self->interned_index) {
                visit(key, arg);
            }
            return 0;
        }

        static int clear(Persister* self) {
            Py_CLEAR(self->serializer);
            for (auto& [key, value] : self->interned_index) {
                Py_DECREF(key);
            }
            self->interned_index.clear();
            self->bindings.clear();
            Py_CLEAR(self->framed_writer_obj);
            self->fw = nullptr;
            self->binding_counter = 0;
            self->bytes_written = 0;
            self->interned_counter = 0;
            return 0;
        }

        static void dealloc(Persister* self) {
            PyObject_GC_UnTrack(self);
            clear(self);
            self->~Persister();
            Py_TYPE(self)->tp_free((PyObject*)self);
        }
    };
}
