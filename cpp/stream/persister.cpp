#include "writer.h"

#include <cassert>
#include <cerrno>
#include <new>
#include <stdexcept>
#include "gilguard.h"

#ifndef _WIN32
    #include <fcntl.h>
    #include <limits.h>
    #include <sys/file.h>
    #include <sys/socket.h>
    #include <sys/stat.h>
    #include <sys/un.h>
    #include <unistd.h>
#endif

static const char* get_utf8_if_exists(PyObject* op, Py_ssize_t* out_size) {
    if (!PyUnicode_Check(op)) return nullptr;
    PyASCIIObject* ascii = _PyASCIIObject_CAST(op);
    if (PyUnicode_IS_COMPACT_ASCII(op)) {
        if (out_size) *out_size = ascii->length;
        return reinterpret_cast<const char*>(ascii + 1);
    }
    PyCompactUnicodeObject* compact = _PyCompactUnicodeObject_CAST(op);
    if (compact->utf8 != nullptr) {
        if (out_size) *out_size = compact->utf8_length;
        return compact->utf8;
    }
    return nullptr;
}

// static const char* get_utf8_if_exists(PyObject* op, Py_ssize_t* out_size) {
//     if (!PyUnicode_Check(op)) return nullptr;

//     // Cast to the compact struct to access utf8 members
//     PyCompactUnicodeObject* compact = (PyCompactUnicodeObject*)op;
//     PyASCIIObject* ascii = &compact->_base;

//     // Case 1: Compact ASCII
//     // For pure ASCII, the UTF-8 version is the data itself.
//     if (ascii->state.ascii && ascii->state.compact) {
//         if (out_size) *out_size = ascii->length;
//         // Data is immediately following the ASCII header
//         return (const char*)(ascii + 1);
//     }

//     // Case 2: Non-ASCII (or non-compact) but UTF-8 is already cached
//     // We check the 'utf8' member of the compact struct.
//     if (compact->utf8 != nullptr) {
//         if (out_size) *out_size = compact->utf8_length;
//         return compact->utf8;
//     }

//     // Case 3: Buffer doesn't exist yet
//     return nullptr;
// }

namespace retracesoftware_stream {
    class Persister : public PyObject {
        PyObject* framed_writer_obj = nullptr;
        FramedWriter* fw = nullptr;
        PyObject* serializer = nullptr;
        map<PyObject*, int> bindings;
        map<PyObject*, int> interns;
        int binding_counter = 0;
        int intern_counter = 0;
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
        void write_memory_view(PyObject* obj);
        void write_size(SizedTypes type, Py_ssize_t size);
        void write_unsigned_number(SizedTypes type, uint64_t value);
        void write_binding_lookup(int ref);
        void write_intern_lookup(int ref);
        void write_str_value(PyObject* obj);
        void write_bytes_header(PyObject* obj);
        void write_bytes_data(PyObject* obj);
        void write_bytes_value(PyObject* obj);
        void write_pickled_value(PyObject* bytes);
        void write_sized_int(int64_t value);
        bool object_freed(PyObject* obj);
        void remember_binding(PyObject* key, int index);
        void remember_intern(PyObject* key, int index);
        void forget_binding(PyObject* key);

    public:
        Persister() {}
        ~Persister() = default;

        PyObject* writer_object() const { return framed_writer_obj; }
        FramedWriter* native_writer() const { return fw; }
        void reset_state();

        bool intern(PyObject* obj, Ref ref);
        void flush();
        void shutdown();
        void prepare_resume();
        void flush_background();

        void start_collection(PyObject* type, size_t len);
        bool write_object(PyObject* obj);
        void write_delete(Ref ref);
        bool write_thread_switch(PyObject* thread_handle);
        void bind(Ref ref);

        void write_heartbeat();
        void write_pickled(PyObject* obj);

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
            self->interns.clear();
            Py_CLEAR(self->framed_writer_obj);
            self->fw = nullptr;
            self->binding_counter = 0;
            self->intern_counter = 0;
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

    namespace {
        PyObject* collection_type_object(Cmd cmd) {
            switch (cmd) {
                case CMD_LIST:
                    return reinterpret_cast<PyObject*>(&PyList_Type);
                case CMD_TUPLE:
                    return reinterpret_cast<PyObject*>(&PyTuple_Type);
                case CMD_DICT:
                    return reinterpret_cast<PyObject*>(&PyDict_Type);
                default:
                    return nullptr;
            }
        }

        template <typename Fn>
        void run_persister_without_gil(Fn&& fn) {
            PyThreadState* save = PyEval_SaveThread();
            try {
                fn();
                PyEval_RestoreThread(save);
            } catch (...) {
                PyEval_RestoreThread(save);
                throw;
            }
        }

        void set_python_error_from_current_exception_impl() {
            try {
                throw;
            } catch (const std::invalid_argument& exc) {
                PyErr_SetString(PyExc_ValueError, exc.what());
            } catch (const std::out_of_range& exc) {
                PyErr_SetString(PyExc_KeyError, exc.what());
            } catch (const std::exception& exc) {
                PyErr_SetString(PyExc_RuntimeError, exc.what());
            } catch (...) {
                if (!PyErr_Occurred()) {
                    PyErr_SetString(PyExc_RuntimeError, "Persister operation failed");
                }
            }
        }

        template <typename Fn>
        PyObject* call_persister_method(Fn&& fn) {
            try {
                run_persister_without_gil(std::forward<Fn>(fn));
            } catch (...) {
                set_python_error_from_current_exception_impl();
                return nullptr;
            }
            Py_RETURN_NONE;
        }

        template <typename Fn>
        PyObject* call_persister_bool_method(Fn&& fn) {
            try {
                bool ok = false;
                run_persister_without_gil([&] { ok = std::forward<Fn>(fn)(); });
                if (!ok) {
                    if (!PyErr_Occurred()) {
                        PyErr_SetString(PyExc_RuntimeError, "Persister operation failed");
                    }
                    return nullptr;
                }
            } catch (...) {
                set_python_error_from_current_exception_impl();
                return nullptr;
            }
            Py_RETURN_NONE;
        }

        PyObject* Persister_py_write_object(Persister* self, PyObject* obj) {
            PyObject* owned = Py_NewRef(obj);
            PyObject* result = call_persister_bool_method([&] { return self->write_object(owned); });
            Py_DECREF(owned);
            return result;
        }

        PyObject* Persister_py_intern(Persister* self, PyObject* args) {
            PyObject* obj;
            PyObject* ref = nullptr;
            if (!PyArg_ParseTuple(args, "O|O", &obj, &ref)) return nullptr;
            PyObject* owned_obj = Py_NewRef(obj);
            PyObject* owned_ref = ref ? Py_NewRef(ref) : Py_NewRef(owned_obj);
            PyObject* result = call_persister_bool_method([&] { return self->intern(owned_obj, owned_ref); });
            Py_DECREF(owned_obj);
            Py_DECREF(owned_ref);
            return result;
        }

        PyObject* Persister_py_bind(Persister* self, PyObject* obj) {
            PyObject* owned = Py_NewRef(obj);
            PyObject* result = call_persister_method([&] { self->bind(reinterpret_cast<Ref>(owned)); });
            Py_DECREF(owned);
            return result;
        }

        PyObject* Persister_py_write_delete(Persister* self, PyObject* obj) {
            PyObject* owned = Py_NewRef(obj);
            PyObject* result = call_persister_method([&] {
                self->write_delete(reinterpret_cast<Ref>(owned));
            });
            Py_DECREF(owned);
            return result;
        }

        PyObject* Persister_py_flush(Persister* self, PyObject*) {
            return call_persister_method([&] { self->flush(); });
        }

        PyObject* Persister_py_shutdown(Persister* self, PyObject*) {
            return call_persister_method([&] { self->shutdown(); });
        }

        PyObject* Persister_py_start_list(Persister* self, PyObject* arg) {
            PyObject* type = collection_type_object(CMD_LIST);
            if (!type) return nullptr;
            unsigned long value = PyLong_AsUnsignedLong(arg);
            if (PyErr_Occurred()) return nullptr;
            return call_persister_method([&] { self->start_collection(type, static_cast<size_t>(value)); });
        }

        PyObject* Persister_py_start_tuple(Persister* self, PyObject* arg) {
            PyObject* type = collection_type_object(CMD_TUPLE);
            if (!type) return nullptr;
            unsigned long value = PyLong_AsUnsignedLong(arg);
            if (PyErr_Occurred()) return nullptr;
            return call_persister_method([&] { self->start_collection(type, static_cast<size_t>(value)); });
        }

        PyObject* Persister_py_start_dict(Persister* self, PyObject* arg) {
            PyObject* type = collection_type_object(CMD_DICT);
            if (!type) return nullptr;
            unsigned long value = PyLong_AsUnsignedLong(arg);
            if (PyErr_Occurred()) return nullptr;
            return call_persister_method([&] { self->start_collection(type, static_cast<size_t>(value)); });
        }

        PyObject* Persister_py_start_collection(Persister* self, PyObject* args) {
            PyObject* type;
            PyObject* len;
            if (!PyArg_ParseTuple(args, "OO", &type, &len)) return nullptr;
            unsigned long value = PyLong_AsUnsignedLong(len);
            if (PyErr_Occurred()) return nullptr;
            PyObject* owned_type = Py_NewRef(type);
            PyObject* result = call_persister_method([&] { self->start_collection(owned_type, static_cast<size_t>(value)); });
            Py_DECREF(owned_type);
            return result;
        }

        PyObject* Persister_py_write_heartbeat(Persister* self, PyObject*) {
            return call_persister_method([&] { self->write_heartbeat(); });
        }

        PyObject* Persister_py_write_thread_switch(Persister* self, PyObject* obj) {
            PyObject* owned = Py_NewRef(obj);
            PyObject* result = call_persister_bool_method([&] { return self->write_thread_switch(owned); });
            Py_DECREF(owned);
            return result;
        }

        PyObject* Persister_py_write_pickled(Persister* self, PyObject* obj) {
            PyObject* owned = Py_NewRef(obj);
            PyObject* result = call_persister_method([&] { self->write_pickled(owned); });
            Py_DECREF(owned);
            return result;
        }

        PyObject* Persister_py_reset_state(Persister* self, PyObject*) {
            return call_persister_method([&] { self->reset_state(); });
        }

        PyObject* Persister_py_prepare_resume(Persister* self, PyObject*) {
            return call_persister_method([&] { self->prepare_resume(); });
        }

        PyObject* Persister_py_flush_background(Persister* self, PyObject*) {
            return call_persister_method([&] { self->flush_background(); });
        }

        PyMethodDef Persister_methods[] = {
            {"write_object", (PyCFunction)Persister_py_write_object, METH_O, "Write an object while mimicking consumer threading"},
            {"intern", (PyCFunction)Persister_py_intern, METH_VARARGS, "Write and bind an object while mimicking consumer threading"},
            {"bind", (PyCFunction)Persister_py_bind, METH_O, "Register a bound object while mimicking consumer threading"},
            {"write_delete", (PyCFunction)Persister_py_write_delete, METH_O, "Write a delete event while mimicking consumer threading"},
            {"flush", (PyCFunction)Persister_py_flush, METH_NOARGS, "Flush the writer while mimicking consumer threading"},
            {"flush_background", (PyCFunction)Persister_py_flush_background, METH_NOARGS, "Flush buffered output after a worker batch"},
            {"shutdown", (PyCFunction)Persister_py_shutdown, METH_NOARGS, "Write shutdown while mimicking consumer threading"},
            {"prepare_resume", (PyCFunction)Persister_py_prepare_resume, METH_NOARGS, "Restamp the writer before resuming queue workers"},
            {"start_list", (PyCFunction)Persister_py_start_list, METH_O, "Write a list header while mimicking consumer threading"},
            {"start_tuple", (PyCFunction)Persister_py_start_tuple, METH_O, "Write a tuple header while mimicking consumer threading"},
            {"start_dict", (PyCFunction)Persister_py_start_dict, METH_O, "Write a dict header while mimicking consumer threading"},
            {"start_collection", (PyCFunction)Persister_py_start_collection, METH_VARARGS, "Write a collection header while mimicking consumer threading"},
            {"write_heartbeat", (PyCFunction)Persister_py_write_heartbeat, METH_NOARGS, "Write a heartbeat while mimicking consumer threading"},
            {"write_thread_switch", (PyCFunction)Persister_py_write_thread_switch, METH_O, "Write a thread switch while mimicking consumer threading"},
            {"write_pickled", (PyCFunction)Persister_py_write_pickled, METH_O, "Write a pre-pickled payload while mimicking consumer threading"},
            {"reset_state", (PyCFunction)Persister_py_reset_state, METH_NOARGS, "Reset persister state while mimicking consumer threading"},
            {nullptr}
        };
    }

    void handle_write_error() {
        PyErr_Print();
        PyErr_Clear();
    }

    void set_python_error_from_current_exception() {
        set_python_error_from_current_exception_impl();
    }

    void handle_debug_error(bool quit_on_error) {
        if (quit_on_error) {
            fprintf(stderr, "retrace: python persister callback error (quit_on_error is set)\n");
            PyErr_Print();
            _exit(1);
        }
        PyErr_Print();
        PyErr_Clear();
    }

    void Persister::write_size(SizedTypes type, Py_ssize_t size) {
        assert(type < 16);

        if (verbose) {
            printf("%s(%i) ", SizedTypes_Name(type), (int)size);
        }

        Control control;
        control.Sized.type = type;

        if (size <= 11) {
            control.Sized.size = (Sizes)size;
            emit_control(control);
        } else if (size < UINT8_MAX) {
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

    void Persister::write_unsigned_number(SizedTypes type, uint64_t value) {
        write_size(type, value);
    }

    void Persister::write_binding_lookup(int ref) {
        write_unsigned_number(SizedTypes::BINDING, ref);
    }

    void Persister::write_intern_lookup(int ref) {
        write_unsigned_number(SizedTypes::INTERN, ref);
    }

    void Persister::write_str_value(PyObject* obj) {
        Py_ssize_t size = 0;

        const char* utf8 = get_utf8_if_exists(obj, &size);

        if (!utf8 || size == 0) {
            retracesoftware::GILGuard gil;
            utf8 = PyUnicode_AsUTF8AndSize(obj, &size);
        }
        if (!utf8) {
            throw nullptr;
        }

        write_size(SizedTypes::STR, size);
        emit_bytes(reinterpret_cast<const uint8_t*>(utf8), size);
    }

    void Persister::write_bytes_header(PyObject* obj) {
        write_size(SizedTypes::BYTES, PyBytes_GET_SIZE(obj));
    }

    void Persister::write_bytes_data(PyObject* obj) {
        emit_bytes(reinterpret_cast<const uint8_t*>(PyBytes_AS_STRING(obj)), PyBytes_GET_SIZE(obj));
    }

    void Persister::write_bytes_value(PyObject* obj) {
        write_bytes_header(obj);
        write_bytes_data(obj);
    }

    void Persister::write_pickled_value(PyObject* bytes) {
        assert(PyBytes_Check(bytes));
        write_size(SizedTypes::PICKLED, PyBytes_GET_SIZE(bytes));
        write_bytes_data(bytes);
    }

    void Persister::write_memory_view(PyObject* obj) {
        Py_buffer* view = PyMemoryView_GET_BUFFER(obj);
        assert(view->readonly);
        write_size(SizedTypes::BYTES, view->len);
        emit_bytes(reinterpret_cast<const uint8_t*>(view->buf), view->len);
    }

    void Persister::write_sized_int(int64_t value) {
        if (value >= 0) {
            write_unsigned_number(SizedTypes::UINT, value);
        } else {
            emit_control(CreateFixedSize(FixedSizeTypes::INT64));
            emit(value);
        }
    }

    bool Persister::write_fallback(PyObject* value) {
        PyGILState_STATE gil = PyGILState_Ensure();

        PyObject* result = nullptr;
        if (PyGC_IsEnabled()) {
            PyGC_Disable();
            result = PyObject_CallOneArg(serializer, value);
            PyGC_Enable();
        } else {
            result = PyObject_CallOneArg(serializer, value);
        }

        if (!result) {
            PyGILState_Release(gil);
            return false;
        }

        bool ok = true;
        try {
            if (PyBytes_Check(result)) {
                write_pickled_value(result);
            } else {
                emit_control(SerializeError);
                ok = write(result);
            }
        } catch (...) {
            Py_DECREF(result);
            PyGILState_Release(gil);
            throw;
        }

        Py_DECREF(result);
        PyGILState_Release(gil);
        return ok;
    }

    void Persister::write_string(PyObject* obj) {
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

    bool Persister::write_long(PyObject* value) {
        int overflow = 0;
        long long ll = PyLong_AsLongLongAndOverflow(value, &overflow);

        if (overflow) {
            return write_fallback(value);
        } else {
            write_sized_int(ll);
            return true;
        }
    }

    bool Persister::write(PyObject* obj) {
        assert(obj);

        if (obj == Py_None) {
            emit(FixedSizeTypes::NONE);
        } else if (Py_TYPE(obj) == &PyUnicode_Type) {
            write_string(obj);
        } else if (Py_TYPE(obj) == &PyLong_Type) {
            return write_long(obj);
        } else if (Py_TYPE(obj) == &PyBytes_Type) {
            write_bytes_value(obj);
        } else if (bindings.contains(obj)) {
            write_binding_lookup(bindings[obj]);
        } else if (interns.contains(obj)) {
            write_intern_lookup(interns[obj]);
        } else {
            return write_fallback(obj);
        }
        return true;
    }

    void Persister::remember_binding(PyObject* key, int index) {
        bindings[key] = index;
    }

    void Persister::remember_intern(PyObject* key, int index) {
        interns[key] = index;
    }

    void Persister::forget_binding(PyObject* key) {
        bindings.erase(key);
        interns.erase(key);
    }

    void Persister::bind(Ref ref) {
        PyObject* key = reinterpret_cast<PyObject*>(ref);
        assert(!bindings.contains(key));
        assert(!interns.contains(key));
        emit_control(Bind);
        remember_binding(key, binding_counter++);
    }

    bool Persister::object_freed(PyObject* obj) {
        if (auto it = bindings.find(obj); it != bindings.end()) {
            write_unsigned_number(SizedTypes::BINDING_DELETE, it->second);
            forget_binding(obj);
            return true;
        }

        return false;
    }

    bool Persister::write_object(PyObject* obj) {
        return write(obj);
    }

    bool Persister::intern(PyObject* obj, Ref ref) {
        PyObject* key = reinterpret_cast<PyObject*>(ref);
        if (bindings.contains(key)) {
            write_binding_lookup(bindings[key]);
            return true;
        }
        if (interns.contains(key)) {
            write_intern_lookup(interns[key]);
            return true;
        }

        emit_control(Intern);
        if (!write(obj)) {
            return false;
        }
        remember_intern(key, intern_counter++);
        return true;
    }

    void Persister::flush() {
        fw->flush();
    }

    void Persister::flush_background() {
        flush();
    }

    void Persister::shutdown() {
        flush();
    }

    void Persister::prepare_resume() {
        if (fw) fw->stamp_pid();
    }

    void Persister::start_collection(PyObject* type, size_t len) {
        if (type == reinterpret_cast<PyObject*>(&PyList_Type)) {
            write_size(SizedTypes::LIST, static_cast<Py_ssize_t>(len));
        } else if (type == reinterpret_cast<PyObject*>(&PyTuple_Type)) {
            write_size(SizedTypes::TUPLE, static_cast<Py_ssize_t>(len));
        } else if (type == reinterpret_cast<PyObject*>(&PyDict_Type)) {
            write_size(SizedTypes::DICT, static_cast<Py_ssize_t>(len));
        } else {
            PyGILState_STATE error_gil = PyGILState_Ensure();
            PyErr_SetString(PyExc_ValueError, "unknown collection type");
            PyGILState_Release(error_gil);
            throw nullptr;
        }
    }

    void Persister::write_heartbeat() {
        emit_control(Heartbeat);
    }

    void Persister::write_delete(Ref ref) {
        object_freed(reinterpret_cast<PyObject*>(ref));
    }

    bool Persister::write_thread_switch(PyObject* thread_handle) {
        PyObject* key = thread_handle;
        if (!interns.contains(key)) {
            if (!intern(thread_handle, thread_handle)) {
                return false;
            }
        }

        emit_control(ThreadSwitch);
        if (interns.contains(key)) {
            write_intern_lookup(interns[key]);
            return true;
        }
        return false;
    }

    void Persister::write_pickled(PyObject* obj) {
        PyGILState_STATE gil = PyGILState_Ensure();
        write_pickled_value(obj);
        PyGILState_Release(gil);
    }

    void Persister::reset_state() {
        bindings.clear();
        interns.clear();
        binding_counter = 0;
        intern_counter = 0;
    }

    static PyObject* Persister_path_getter(PyObject* obj, void*) {
        Persister* self = reinterpret_cast<Persister*>(obj);
        if (self->writer_object()) return PyObject_GetAttrString(self->writer_object(), "path");
        return PyUnicode_FromString("");
    }

    static PyObject* Persister_fd_getter(PyObject* obj, void*) {
        Persister* self = reinterpret_cast<Persister*>(obj);
        return PyLong_FromLong(self->native_writer() ? self->native_writer()->fd() : -1);
    }

    static PyObject* Persister_is_fifo_getter(PyObject* obj, void*) {
        Persister* self = reinterpret_cast<Persister*>(obj);
        if (self->writer_object()) return PyObject_GetAttrString(self->writer_object(), "is_fifo");
        return PyBool_FromLong(0);
    }

    static PyGetSetDef Persister_getset[] = {
        {"path", Persister_path_getter, nullptr, "File path", NULL},
        {"fd", Persister_fd_getter, nullptr, "Underlying file descriptor", NULL},
        {"is_fifo", Persister_is_fifo_getter, nullptr, "True if the output is a named pipe", NULL},
        {NULL}
    };

    PyTypeObject Persister_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "Persister",
        .tp_basicsize = sizeof(Persister),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)Persister::dealloc,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
        .tp_doc = "File persister sink",
        .tp_traverse = (traverseproc)Persister::traverse,
        .tp_clear = (inquiry)Persister::clear,
        .tp_methods = Persister_methods,
        .tp_getset = Persister_getset,
        .tp_init = (initproc)Persister::init,
        .tp_new = Persister::tp_new,
    };

    bool Persister_write_heartbeat(Persister * persister) {
        retracesoftware::GILGuard gstate;
        persister->write_heartbeat();
        return true;
    }
    
    bool Persister_write_delete(Persister * persister, Ref ref) {
        // retracesoftware::GILGuard gstate;
        persister->write_delete(ref);
        return true;
    }
    
    bool Persister_write_thread_switch(Persister * persister, PyObject * thread_handle) {
        // retracesoftware::GILGuard gstate;
        return persister->write_thread_switch(thread_handle);
    }

    bool Persister_start_collection(Persister * persister, PyObject* type, size_t len) {
        // retracesoftware::GILGuard gstate;
        persister->start_collection(type, len);
        return true;
    }

    bool Persister_write_object(Persister * persister, PyObject * obj) {
        // retracesoftware::GILGuard gstate;
        return persister->write_object(obj);
    }

    bool Persister_intern(Persister * persister, PyObject * obj, Ref ref) {
        // retracesoftware::GILGuard gstate;
        return persister->intern(obj, ref);
    }

    bool Persister_bind(Persister * persister, Ref ref) {
        // retracesoftware::GILGuard gstate;
        persister->bind(ref);
        return true;
    }
}
