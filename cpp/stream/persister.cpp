#include "persister.h"

#include <cassert>
#include <cerrno>
#include <stdexcept>

#ifndef _WIN32
    #include <fcntl.h>
    #include <limits.h>
    #include <sys/file.h>
    #include <sys/socket.h>
    #include <sys/stat.h>
    #include <sys/un.h>
    #include <unistd.h>
#endif

namespace retracesoftware_stream {
    namespace {
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

        PyObject* Persister_py_write_ref(Persister* self, PyObject* obj) {
            PyObject* owned = Py_NewRef(obj);
            PyObject* result = call_persister_method([&] {
                self->write_ref(reinterpret_cast<Ref>(owned));
            });
            Py_DECREF(owned);
            return result;
        }

        PyObject* Persister_py_intern(Persister* self, PyObject* obj) {
            PyObject* owned = Py_NewRef(obj);
            PyObject* result = call_persister_bool_method([&] { return self->intern(owned); });
            Py_DECREF(owned);
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
            unsigned long value = PyLong_AsUnsignedLong(arg);
            if (PyErr_Occurred()) return nullptr;
            return call_persister_method([&] { self->start_list((uint32_t)value); });
        }

        PyObject* Persister_py_start_tuple(Persister* self, PyObject* arg) {
            unsigned long value = PyLong_AsUnsignedLong(arg);
            if (PyErr_Occurred()) return nullptr;
            return call_persister_method([&] { self->start_tuple((uint32_t)value); });
        }

        PyObject* Persister_py_start_dict(Persister* self, PyObject* arg) {
            unsigned long value = PyLong_AsUnsignedLong(arg);
            if (PyErr_Occurred()) return nullptr;
            return call_persister_method([&] { self->start_dict((uint32_t)value); });
        }

        PyObject* Persister_py_write_heartbeat(Persister* self, PyObject*) {
            return call_persister_method([&] { self->write_heartbeat(); });
        }

        PyObject* Persister_py_write_new_ext_wrapped(Persister* self, PyObject* obj) {
            if (!PyType_Check(obj)) {
                PyErr_SetString(PyExc_TypeError, "expected type object");
                return nullptr;
            }
            PyObject* owned = Py_NewRef(obj);
            PyObject* result = call_persister_bool_method([&] {
                return self->write_new_ext_wrapped(reinterpret_cast<PyTypeObject*>(owned));
            });
            Py_DECREF(owned);
            return result;
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

        PyObject* Persister_py_write_new_patched(Persister* self, PyObject* args) {
            PyObject* obj;
            PyObject* type;
            if (!PyArg_ParseTuple(args, "OO", &obj, &type)) return nullptr;
            PyObject* owned_obj = Py_NewRef(obj);
            PyObject* owned_type = Py_NewRef(type);
            PyObject* result = call_persister_method([&] {
                self->write_new_patched(owned_obj, owned_type);
            });
            Py_DECREF(owned_obj);
            Py_DECREF(owned_type);
            return result;
        }

        PyObject* Persister_py_reset_state(Persister* self, PyObject*) {
            return call_persister_method([&] { self->reset_state(); });
        }

        PyMethodDef Persister_methods[] = {
            {"write_object", (PyCFunction)Persister_py_write_object, METH_O, "Write an object while mimicking consumer threading"},
            {"write_ref", (PyCFunction)Persister_py_write_ref, METH_O, "Write a bound reference while mimicking consumer threading"},
            {"intern", (PyCFunction)Persister_py_intern, METH_O, "Write and bind an object while mimicking consumer threading"},
            {"bind", (PyCFunction)Persister_py_bind, METH_O, "Register a bound object while mimicking consumer threading"},
            {"write_delete", (PyCFunction)Persister_py_write_delete, METH_O, "Write a delete event while mimicking consumer threading"},
            {"flush", (PyCFunction)Persister_py_flush, METH_NOARGS, "Flush the writer while mimicking consumer threading"},
            {"shutdown", (PyCFunction)Persister_py_shutdown, METH_NOARGS, "Write shutdown while mimicking consumer threading"},
            {"start_list", (PyCFunction)Persister_py_start_list, METH_O, "Write a list header while mimicking consumer threading"},
            {"start_tuple", (PyCFunction)Persister_py_start_tuple, METH_O, "Write a tuple header while mimicking consumer threading"},
            {"start_dict", (PyCFunction)Persister_py_start_dict, METH_O, "Write a dict header while mimicking consumer threading"},
            {"write_heartbeat", (PyCFunction)Persister_py_write_heartbeat, METH_NOARGS, "Write a heartbeat while mimicking consumer threading"},
            {"write_new_ext_wrapped", (PyCFunction)Persister_py_write_new_ext_wrapped, METH_O, "Write an ext-wrapped type while mimicking consumer threading"},
            {"write_thread_switch", (PyCFunction)Persister_py_write_thread_switch, METH_O, "Write a thread switch while mimicking consumer threading"},
            {"write_pickled", (PyCFunction)Persister_py_write_pickled, METH_O, "Write a pre-pickled payload while mimicking consumer threading"},
            {"write_new_patched", (PyCFunction)Persister_py_write_new_patched, METH_VARARGS, "Write a new patched object while mimicking consumer threading"},
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

    void Persister::write_lookup(int ref) {
        write_unsigned_number(SizedTypes::BINDING, ref);
    }

    void Persister::write_str_value(PyObject* obj) {
        Py_ssize_t size = 0;
        PyGILState_STATE gil = PyGILState_Ensure();
        const char* utf8 = PyUnicode_AsUTF8AndSize(obj, &size);
        if (!utf8) {
            PyGILState_Release(gil);
            throw nullptr;
        }

        write_size(SizedTypes::STR, size);
        emit_bytes(reinterpret_cast<const uint8_t*>(utf8), size);
        PyGILState_Release(gil);
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

    void Persister::write_bool_value(PyObject* obj) {
        emit(obj == Py_True ? FixedSizeTypes::TRUE : FixedSizeTypes::FALSE);
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
        } else if (value == -1) {
            emit_control(CreateFixedSize(FixedSizeTypes::NEG1));
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
        } else if (Py_TYPE(obj) == &PyBool_Type) {
            write_bool_value(obj);
        } else if (bindings.contains(obj)) {
            write_lookup(bindings[obj]);
        } 
        else {
            return write_fallback(obj);
        }
        return true;
    }

    void Persister::bind(Ref ref) {
        PyObject* obj = reinterpret_cast<PyObject*>(ref);
        assert(!bindings.contains(obj));
        bindings[obj] = binding_counter++;
    }

    bool Persister::object_freed(PyObject* obj) {
        auto it = bindings.find(obj);
        if (it == bindings.end()) {
            return false;
        }

        write_unsigned_number(SizedTypes::BINDING_DELETE, it->second);
        bindings.erase(it);
        return true;
    }

    bool Persister::write_object(PyObject* obj) {
        return write(obj);
    }

    void Persister::write_ref(Ref ref) {
        auto it = bindings.find(reinterpret_cast<PyObject*>(ref));
        if (it == bindings.end()) throw std::out_of_range("unknown bound ref");
        write_lookup(it->second);
    }

    bool Persister::intern(PyObject* obj) {
        if (bindings.contains(obj)) {
            write_lookup(bindings[obj]);
            return true;
        }

        emit_control(Intern);
        if (!write(obj)) {
            return false;
        }
        bindings[obj] = binding_counter++;
        return true;
    }

    void Persister::flush() {
        fw->flush();
    }

    void Persister::shutdown() {
        flush();
    }

    void Persister::start_list(uint32_t len) {
        write_size(SizedTypes::LIST, len);
    }

    void Persister::start_tuple(uint32_t len) {
        write_size(SizedTypes::TUPLE, len);
    }

    void Persister::start_dict(uint32_t len) {
        write_size(SizedTypes::DICT, len);
    }

    void Persister::write_heartbeat() {
        emit_control(Heartbeat);
    }

    bool Persister::write_new_ext_wrapped(PyTypeObject* type) {
        return write(reinterpret_cast<PyObject*>(type));
    }

    void Persister::write_delete(Ref ref) {
        object_freed(reinterpret_cast<PyObject*>(ref));
    }

    bool Persister::write_thread_switch(PyObject* thread_handle) {
        emit_control(ThreadSwitch);
        return write(thread_handle);
    }

    void Persister::write_pickled(PyObject* obj) {
        PyGILState_STATE gil = PyGILState_Ensure();
        write_pickled_value(obj);
        PyGILState_Release(gil);
    }

    void Persister::write_new_patched(PyObject* obj, PyObject* type) {
        PyGILState_STATE gil = PyGILState_Ensure();
        assert(!bindings.contains(obj));
        bindings[obj] = binding_counter++;
        assert(bindings.contains(type));

        emit(FixedSizeTypes::NEW_PATCHED);
        write_lookup(bindings[type]);
        PyGILState_Release(gil);
    }

    void Persister::reset_state() {
        bindings.clear();
        binding_counter = 0;
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
}
