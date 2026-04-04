#include "stream.h"
#include "wireformat.h"
#include <chrono>
#include <stdexcept>
#include <utility>
#include <thread>
#include <sstream>
#include <structmember.h>

namespace retracesoftware_stream {

    static FILE * open(PyObject * path) {
        FILE * file = fopen(PyUnicode_AsUTF8(path), "rb");

        if (!file) {
            PyErr_Format(PyExc_IOError, "Could not open file: %S, mode: %s for reader, error: %s", path, "rb", strerror(errno));
            throw nullptr;
        }

        return file;
    }

    struct ObjectStream : public PyObject {
        FILE * file = nullptr;
        PyObject * path = nullptr;
        size_t bytes_read = 0;
        size_t messages_read = 0;
        int read_timeout = 0;
        vectorcallfunc vectorcall;
        std::vector<PyObject *> interned_strings;

        map<int, PyObject *> bindings;
        bool pending_bind = false;
        int binding_counter = 0;
        PyObject * create_pickled = nullptr;
        PyObject * stub_factory = nullptr;
        PyObject * create_thread_switch;
        PyObject * create_dropped = nullptr;
        PyObject * create_heartbeat = nullptr;
        bool verbose = false;

        static int init(ObjectStream * self, PyObject* args, PyObject* kwds) {
            
            PyObject * path;
            PyObject * create_pickled;
            PyObject * stub_factory;
            PyObject * create_thread_switch;
            PyObject * create_dropped = nullptr;
            PyObject * create_heartbeat = nullptr;

            int read_timeout = 0;
            int verbose = 0;
            long long start_offset = 0;

            static const char* kwlist[] = {
                "path", 
                "deserialize",
                "stub_factory",
                "on_thread_switch",
                "read_timeout",
                "verbose",
                "on_dropped",
                "on_heartbeat",
                "start_offset",
                nullptr};

            if (!PyArg_ParseTupleAndKeywords(args, kwds, "O!OOOip|OOL", (char **)kwlist, 
                &PyUnicode_Type, &path, 
                &create_pickled,
                &stub_factory,
                &create_thread_switch,
                &read_timeout,
                &verbose,
                &create_dropped,
                &create_heartbeat,
                &start_offset)) {
                return -1;
            }

            new (&self->interned_strings) std::vector<PyObject *>();
            new (&self->bindings) map<int, PyObject *>();

            self->create_pickled = Py_NewRef(create_pickled);
            self->stub_factory = Py_NewRef(stub_factory);
            self->create_thread_switch = Py_NewRef(create_thread_switch);
            self->create_dropped = Py_XNewRef(create_dropped);
            self->create_heartbeat = Py_XNewRef(create_heartbeat);
            self->read_timeout = read_timeout;
            self->verbose = verbose;

            self->vectorcall = (vectorcallfunc)call;

            self->path = Py_NewRef(path);

            try {
                self->file = open(path);
                if (start_offset > 0) {
                    fseek(self->file, (long)start_offset, SEEK_SET);
                }
            } catch (...) {
                return -1;
            }
            return 0;
        }

        void close() {
            if (file) {
                fclose(file);
                file = nullptr;
            }
        }

        static void dealloc(ObjectStream* self) {
            PyObject_GC_UnTrack(self);
            clear(self);

            self->interned_strings.std::vector<PyObject *>::~vector();
            self->bindings.~map<int, PyObject *>();

            Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
        }

        static int traverse(ObjectStream* self, visitproc visit, void* arg) {
            Py_VISIT(self->path);
            Py_VISIT(self->create_pickled);
            Py_VISIT(self->stub_factory);
            Py_VISIT(self->create_thread_switch);
            Py_VISIT(self->create_dropped);
            Py_VISIT(self->create_heartbeat);

            return 0;
        }

        static int clear(ObjectStream* self) {

            for (auto elem : self->interned_strings) {
                Py_XDECREF(elem);
            }
            self->interned_strings.clear();

            for (auto const& [index, value] : self->bindings) {
                Py_XDECREF(value);
            }
            self->bindings.clear();

            Py_CLEAR(self->path);
            Py_CLEAR(self->create_pickled);
            Py_CLEAR(self->stub_factory);
            Py_CLEAR(self->create_thread_switch);
            Py_CLEAR(self->create_dropped);
            Py_CLEAR(self->create_heartbeat);

            return 0;
        }

        void read(uint8_t * bytes, size_t size) {
            size_t r = fread(bytes, sizeof(uint8_t), size, file);

            if (r < size) {
                std::this_thread::sleep_for(std::chrono::milliseconds(read_timeout));

                r += fread(bytes + r, sizeof(uint8_t), size - r, file);

                if (r < size) {
                    std::stringstream message;
                    message << "Could not read: " << (size - r) << " bytes from tracefile with timeout: " << read_timeout 
                            << " milliseconds";
                    throw std::runtime_error(message.str());
                }
            }
            bytes_read += size;
        }

        template<typename T>
        T read() {
            if constexpr (std::is_same_v<T, int8_t>) {
                return (int8_t)read<uint8_t>();
            }
            else if constexpr (std::is_same_v<T, uint8_t>) {
                uint8_t byte;
                read(&byte, 1);
                return byte;
            }
            else if constexpr (std::is_same_v<T, int16_t>) {
                return (int16_t)read<uint16_t>();
            }
            else if constexpr (std::is_same_v<T, uint16_t>) {
                uint8_t buffer[sizeof(uint16_t)];
                read(buffer, sizeof(buffer));

                uint16_t value = (uint16_t)buffer[0];
                value |= ((uint16_t)buffer[1] << 8);
                return value;
            }
            else if constexpr (std::is_same_v<T, int32_t>) {
                return (int16_t)read<uint32_t>();
            }
            else if constexpr (std::is_same_v<T, uint32_t>) {
                uint8_t buffer[sizeof(uint32_t)];
                read(buffer, sizeof(buffer));

                uint32_t value = (uint32_t)buffer[0];
                value |= ((uint32_t)buffer[1] << 8);
                value |= ((uint32_t)buffer[2] << 16);
                value |= ((uint32_t)buffer[3] << 24);
                return value;
            }
            else if constexpr (std::is_same_v<T, int64_t>) {
                return (int64_t)read<uint64_t>();
            }
            else if constexpr (std::is_same_v<T, uint64_t>) {
                uint8_t buffer[sizeof(uint64_t)];
                read(buffer, sizeof(buffer));

                uint64_t value = (uint64_t)buffer[0];
                value |= ((uint64_t)buffer[1] << 8);
                value |= ((uint64_t)buffer[2] << 16);
                value |= ((uint64_t)buffer[3] << 24);
                value |= ((uint64_t)buffer[4] << 32);
                value |= ((uint64_t)buffer[5] << 40);
                value |= ((uint64_t)buffer[6] << 48);
                value |= ((uint64_t)buffer[7] << 56);
                return value;
            }

            else if constexpr (std::is_same_v<T, double>) {
                uint64_t raw = read<uint64_t>();
                return *(double *)&raw;
            }
            else {
                static_assert(std::is_void_v<T>, "Unsupported type in read<T>()");
                // static_assert(false, "read<T>() not specialized for this type");
            }
        }

        Control read_control() {
            return Control(read<uint8_t>());
        }

        size_t read_unsigned_number(Control control) {
            switch (control.Sized.size) {
                case ONE_BYTE_SIZE:
                    return (size_t)read<uint8_t>();
                case TWO_BYTE_SIZE:
                    return (size_t)read<uint16_t>();
                case FOUR_BYTE_SIZE:
                    return (size_t)read<uint32_t>();
                case EIGHT_BYTE_SIZE:
                    return (size_t)read<uint64_t>();
                default:
                    return (size_t)(control.Sized.size);
            }
        }

        PyObject * read_list(size_t size) {
            auto list = PyObjectPtr(PyList_New(size));

            if (!list.get()) throw nullptr;

            for (size_t i = 0; i < size; i++) {
                PyList_SetItem(list.get(), i, read());
            }
            return Py_NewRef(list.get());
        }

        PyObject * read_tuple(size_t size) {
            auto tuple = PyObjectPtr(PyTuple_New(size));

            if (!tuple.get()) throw nullptr;

            for (size_t i = 0; i < size; i++) {
                if (PyTuple_SetItem(tuple.get(), i, read()) == -1) {
                    throw nullptr;
                }
            }
            return Py_NewRef(tuple.get());
        }

        PyObject * read_dict(size_t size) {

            auto dict = PyObjectPtr(PyDict_New());

            if (!dict.get()) {
                throw std::exception();
            }

            while (size--) {
                auto key = PyObjectPtr(read());

                if (!key.get()) {
                    assert(PyErr_Occurred());
                    throw std::exception();
                }

                auto value = PyObjectPtr(read());

                if (!value.get()) {
                    assert(PyErr_Occurred());
                    throw std::exception();
                }

                if (PyDict_SetItem(dict.get(), key.get(), value.get()) == -1) {
                    assert(PyErr_Occurred());
                    throw std::exception();
                }
            }
            return Py_NewRef(dict.get());
        }

        PyObject * read_str(size_t size) {

            static thread_local int8_t scratch[1024];

            if (size >= sizeof(scratch)) {
                int8_t * buffer = (int8_t *)malloc(size + 1);

                if (!buffer) {
                    PyErr_Format(PyExc_MemoryError, "Error allocating: %zu bytes", size + 1);
                    throw nullptr;
                }

                read((uint8_t *)buffer, size);
                buffer[size] = '\0';

                PyObject * str = PyUnicode_DecodeUTF8((char *)buffer, size, "strict");

                free(buffer);

                if (!str) throw nullptr;
                return str;

            } else {
            
                read((uint8_t *)scratch, size);
                scratch[size] = '\0';

                PyObject * decoded = PyUnicode_DecodeUTF8((char *)scratch, size, "strict");

                if (!decoded) throw nullptr;
                return decoded;
            }
        }

        PyObject * read_bytes(size_t size) {

            if (size == 0) {
                return PyBytes_FromStringAndSize(NULL, 0);
            } else {
                auto bytes_obj = PyObjectPtr(PyBytes_FromStringAndSize(NULL, size));

                if (!bytes_obj.get()) {
                    PyErr_NoMemory();
                    throw nullptr;
                }

                read((uint8_t *)PyBytes_AS_STRING(bytes_obj.get()), size);

                return Py_NewRef(bytes_obj.get());
            }
        }

        PyObject * read_pickled(size_t size) {
            PyObject * bytes = read_bytes(size);

            PyObject * res = PyObject_CallOneArg(create_pickled, bytes);

            Py_DECREF(bytes);

            if (!res) throw nullptr;
            return res;
        }

        static PyObject * create_indexed(PyObject * factory, size_t index) {
            PyObject * i = PyLong_FromLong(index);
            if (!i) throw nullptr;
            PyObject * res = PyObject_CallOneArg(factory, i);
            Py_DECREF(i);
            if (!res) throw nullptr;
            return res;
        }

        PyObject * read_bigint(size_t size) {
            // Read big-endian signed bytes and convert to PyLong
            uint8_t * buf = (uint8_t *)malloc(size);
            if (!buf) {
                PyErr_NoMemory();
                throw nullptr;
            }
            read(buf, size);
            int little_endian = 0;
            int is_signed = 1;
            PyObject * result = _PyLong_FromByteArray(buf, size, little_endian, is_signed);
            free(buf);
            if (!result) throw nullptr;
            return result;
        }

        PyObject * read_sized(Control control) {

            size_t size = read_unsigned_number(control);

            switch (control.Sized.type) {
                case SizedTypes::UINT: {
                    PyObject * result = PyLong_FromLongLong(size);
                    if (!result) throw nullptr;
                    return result;
                }
                case SizedTypes::BINDING:
                case SizedTypes::INTERN:
                    return Py_NewRef(bindings[size]);
                case SizedTypes::BYTES: return read_bytes(size);
                case SizedTypes::LIST: return read_list(size);
                case SizedTypes::DICT: return read_dict(size);
                case SizedTypes::TUPLE: return read_tuple(size);
                case SizedTypes::STR: {
                    // Read string and store for potential STR_REF later
                    PyObject * str = read_str(size);
                    interned_strings.push_back(Py_NewRef(str));
                    return str;
                }
                case SizedTypes::STR_REF:
                    // Reference to previously-read string
                    return Py_NewRef(interned_strings[size]);
                case SizedTypes::PICKLED: return read_pickled(size);
                case SizedTypes::BIGINT: return read_bigint(size);
                default:
                    PyErr_Format(PyExc_RuntimeError, "unknown sized type: %i", control.Sized.type);
                    throw nullptr;
            }
        }

        PyObject * create_from_next(PyObject * factory) {
            PyObject * next = read();
            if (!next) return nullptr;

            PyObject * result = PyObject_CallOneArg(factory, next);

            Py_DECREF(next);
            return result;
        }

        uint64_t read_expected_int() {
            uint8_t i = read<uint8_t>();
            
            return i == 255 ? read<uint64_t>() : (uint64_t)i;
        }

        PyObject * read_fixedsize(FixedSizeTypes type) {
            assert (!PyErr_Occurred());

            // if (verbose) printf("%s ", FixedSizeTypes_Name(type));

            switch (type) {
                case FixedSizeTypes::NONE: 
                    return Py_NewRef(Py_None);
                case FixedSizeTypes::FLOAT:
                    return PyFloat_FromDouble(read<double>());
                case FixedSizeTypes::INT64:
                    return PyLong_FromLongLong(read<int64_t>());

                case FixedSizeTypes::THREAD_SWITCH: {
                    PyObject* thread = read();
                    if (!thread) {
                        return nullptr;
                    }
                    PyObject* result = PyObject_CallOneArg(create_thread_switch, thread);
                    Py_DECREF(thread);
                    return result;
                }

                case FixedSizeTypes::HEARTBEAT:
                    return create_heartbeat ? PyObject_CallNoArgs(create_heartbeat)
                                            : Py_NewRef(Py_None);

                case FixedSizeTypes::SERIALIZE_ERROR:
                    return read();

                default:
                    const char * name = FixedSizeTypes_Name(static_cast<FixedSizeTypes>(type));

                    if (name) {
                        PyErr_Format(PyExc_RuntimeError, 
                            "unhandled subtype: %s (0x%02X) for FixedSized at byte %zu, message %zu", 
                            name, (unsigned)type, bytes_read, messages_read);
                    } else {
                        PyErr_Format(PyExc_RuntimeError, 
                            "Unknown subtype: %i (0x%02X) for FixedSized at byte %zu, message %zu", 
                            type, (unsigned)type, bytes_read, messages_read);
                    }
                    throw nullptr;
            };
        }

        PyObject * read() {
            while (true) {
                size_t start = bytes_read;
                Control control = read_control();
                if (verbose > 1) {
                    printf("    read control: 0x%02X at byte %zu\n", control.raw, bytes_read - 1);
                }

                if (control.Sized.type == SizedTypes::BINDING_DELETE) {
                    if (verbose) {
                        printf("Retrace - ObjectStream[%lu, %zu] - Inline BINDING_DELETE\n", messages_read, start);
                    }
                    size_t size = read_unsigned_number(control);
                    Py_DECREF(bindings[size]);
                    bindings.erase(size);
                    messages_read++;
                    continue;
                }

                if (control == Intern) {
                    if (verbose) {
                        printf("Retrace - ObjectStream[%lu, %zu] - Inline INTERN\n", messages_read, start);
                    }
                    bindings[binding_counter++] = read();
                    messages_read++;
                    continue;
                }

                if (control == Bind) {
                    PyErr_Format(
                        PyExc_RuntimeError,
                        "unexpected inline BIND at byte %zu, message %zu",
                        start,
                        messages_read
                    );
                    throw nullptr;
                }

                return read(control);
            }
        }

        PyObject * read(Control control) {
            return control.Sized.type == SizedTypes::FIXED_SIZE
                ? read_fixedsize(control.Fixed.type)
                : read_sized(control);
        }

        PyTypeObject * read_bound_type(const char * opname) {
            PyObject * type_obj = read();

            if (!type_obj) {
                if (!PyErr_Occurred()) {
                    PyErr_Format(PyExc_RuntimeError, "read() returned NULL without setting exception in %s", opname);
                }
                throw nullptr;
            }

            if (!PyType_Check(type_obj)) {
                PyErr_Format(PyExc_TypeError, "%s expected a type but read: %S", opname, type_obj);
                Py_DECREF(type_obj);
                throw nullptr;
            }

            return reinterpret_cast<PyTypeObject *>(type_obj);
        }

        PyObject * create_stub(PyTypeObject * cls, const char * opname) {
            PyObject * instance = PyObject_CallOneArg(stub_factory, reinterpret_cast<PyObject *>(cls));

            if (!instance) {
                throw nullptr;
            }

            if (!PyObject_TypeCheck(instance, cls)) {
                PyErr_Format(PyExc_TypeError,
                    "%s stub_factory returned <%s object at %p> for type %s",
                    opname, Py_TYPE(instance)->tp_name, (void *)instance, cls->tp_name);
                Py_DECREF(instance);
                throw nullptr;
            }

            return instance;
        }

        PyObject * read_ext_bind() {
            PyTypeObject * cls = read_bound_type("BIND");
            PyObject * empty = PyTuple_New(0);

            if (!empty) {
                Py_DECREF(reinterpret_cast<PyObject *>(cls));
                throw nullptr;
            }

            PyObject * instance = cls->tp_new(cls, empty, nullptr);

            Py_DECREF(empty);
            Py_DECREF(reinterpret_cast<PyObject *>(cls));

            if (!instance) {
                throw nullptr;
            }
            return instance;
        }

        Control consume(size_t & start) {
            while (true) {
                start = bytes_read;
                Control control = read_control();
                
                if (verbose > 1) {
                    printf("  consume: control 0x%02X at byte %zu\n", control.raw, start);
                }
                
                if (control.Sized.type == SizedTypes::BINDING_DELETE) {
                    if (verbose) printf("Retrace - ObjectStream[%lu, %lu] - Consumed BINDING_DELETE\n", messages_read, start);
                    size_t size = read_unsigned_number(control);
                    Py_DECREF(bindings[size]);
                    bindings.erase(size);
                    messages_read++;
                } else if (control == Bind) {                
                    if (verbose) printf("Retrace - ObjectStream[%lu, %lu] - Consumed BIND\n", messages_read, start);
                    pending_bind = true;
                    messages_read++;
                    return control;
                } else if (control == Intern) {
                    if (verbose) printf("Retrace - ObjectStream[%lu, %lu] - Consumed INTERN\n", messages_read, start);
                    bindings[binding_counter++] = read();
                    messages_read++;
                } else {
                    return control;
                }
            }
        }

        bool bind(PyObject * binding) {
            if (!pending_bind) {
                PyErr_Format(PyExc_RuntimeError, "Trying to bind when no pending bind");
                return false;
            }
            bindings[binding_counter++] = Py_NewRef(binding);
            pending_bind = false;
            return true;
        }

        static PyObject * py_bind(ObjectStream * self, PyObject * binding) {
            try {
                if (!self->bind(binding)) {
                    return nullptr;
                }
            } catch (...) {
                assert(PyErr_Occurred());
                return nullptr;
            }
            Py_RETURN_NONE;
        }

        static PyObject * py_close(ObjectStream * self, PyObject * usused) {
            self->close();
            Py_RETURN_NONE;
        }

        static PyObject * py_file_offset(ObjectStream * self, PyObject * unused) {
            if (!self->file) {
                PyErr_SetString(PyExc_RuntimeError, "file is not open");
                return nullptr;
            }
            long offset = ftell(self->file);
            if (offset < 0) {
                PyErr_SetFromErrno(PyExc_IOError);
                return nullptr;
            }
            return PyLong_FromLong(offset);
        }

        static PyObject * py_reopen(ObjectStream * self, PyObject * args) {
            long long offset;
            if (!PyArg_ParseTuple(args, "L", &offset)) return nullptr;
            if (!self->path) {
                PyErr_SetString(PyExc_RuntimeError, "no path stored");
                return nullptr;
            }
            if (self->file) {
                fclose(self->file);
                self->file = nullptr;
            }
            try {
                self->file = open(self->path);
            } catch (...) {
                return nullptr;
            }
            if (offset > 0) {
                fseek(self->file, (long)offset, SEEK_SET);
            }
            Py_RETURN_NONE;
        }

        PyObject * next() {
            if (pending_bind) {
                PyErr_Format(PyExc_RuntimeError, "Can't reading next as unbound pending bind");
                return nullptr;
            }

            size_t start;
            Control control = consume(start);

            if (control == ThreadSwitch) {

                PyObject * thread = read();

                if (verbose) {
                    PyObject * s = PyObject_Str(thread);
                    printf("Retrace - ObjectStream[%lu, %lu] - Consumed THREAD_SWITCH(%s)\n", messages_read, start, PyUnicode_AsUTF8(s));
                    Py_DECREF(s);
                }
                messages_read++;
                PyObject * result = PyObject_CallOneArg(create_thread_switch, thread);
                Py_DECREF(thread);
                return result;
            }
            if (control == Dropped) {
                PyObject * count = read();
                if (verbose) {
                    PyObject * s = PyObject_Str(count);
                    printf("Retrace - ObjectStream[%lu, %lu] - Consumed DROPPED(%s)\n", messages_read, start, PyUnicode_AsUTF8(s));
                    Py_DECREF(s);
                }
                messages_read++;
                if (create_dropped) {
                    PyObject * result = PyObject_CallOneArg(create_dropped, count);
                    Py_DECREF(count);
                    return result;
                }
                Py_DECREF(count);
                return next();
            }
            if (control == Heartbeat) {
                PyObject * payload = read();
                if (verbose) {
                    printf("Retrace - ObjectStream[%lu, %lu] - Consumed HEARTBEAT\n", messages_read, start);
                }
                messages_read++;
                if (create_heartbeat) {
                    PyObject * result = PyObject_CallOneArg(create_heartbeat, payload);
                    Py_DECREF(payload);
                    return result;
                }
                Py_DECREF(payload);
                return next();
            }
            if (control == Bind) {
                Py_RETURN_NONE;
            }
            else {
                PyObject * result = read(control);

                if (verbose) {
                    PyObject * s = PyObject_Str(result);
                    printf("Retrace - ObjectStream[%lu, %lu] - Read: %s\n", messages_read, start, PyUnicode_AsUTF8(s));
                    Py_DECREF(s);
                }
                messages_read++;
                return result;
            }
        }

        static PyObject* call(ObjectStream *self, PyObject *const *args, size_t nargsf, PyObject *kwnames) {
            try {
                return self->next();
            } catch (std::exception &e) {
                if (!PyErr_Occurred()) {
                    PyErr_SetString(PyExc_RuntimeError, e.what());
                }
                return nullptr;
            } catch (...) {
                if (!PyErr_Occurred()) {
                    PyErr_SetString(PyExc_RuntimeError, "Unknown C++ exception in ObjectStream");
                }
                return nullptr;
            }
        }
    };

    static PyMemberDef members[] = {
        // {"bytes_read", T_ULONGLONG, OFFSET_OF_MEMBER(ObjectReader, bytes_read), READONLY, "TODO"},
        // {"messages_read", T_ULONGLONG, OFFSET_OF_MEMBER(ObjectReader, messages_read), READONLY, "TODO"},
        // {"stacktraces", T_OBJECT, OFFSET_OF_MEMBER(ObjectReader, stacktraces), READONLY, "TODO"},
        // {"active_thread", T_OBJECT, OFFSET_OF_MEMBER(ObjectReader, active_thread), READONLY, "TODO"},
        // {"stack_stop_at", T_ULONGLONG, OFFSET_OF_MEMBER(ObjectReader, stack_stop_at), 0, "TODO"},
        // {"pending_reads", T_OBJECT, OFFSET_OF_MEMBER(ObjectReader, pending_reads), READONLY, "TODO"},
        // {"path", T_OBJECT, OFFSET_OF_MEMBER(Writer, path), READONLY, "TODO"},
        {"read_timeout", T_INT, OFFSET_OF_MEMBER(ObjectStream, read_timeout), 0, "TODO"},
        {"bytes_read", T_ULONG, OFFSET_OF_MEMBER(ObjectStream, bytes_read), READONLY, "TODO"},
        {"messages_read", T_ULONG, OFFSET_OF_MEMBER(ObjectStream, messages_read), READONLY, "TODO"},
        {"pending_bind", T_BOOL, OFFSET_OF_MEMBER(ObjectStream, pending_bind), READONLY, "TODO"},
        {"verbose", T_BOOL, OFFSET_OF_MEMBER(ObjectStream, verbose), 0, "TODO"},
        {NULL}  /* Sentinel */
    };

    static PyGetSetDef getset[] = {
        // {"next_control", (getter)ObjectReader::next_control_getter, nullptr, "TODO", NULL},
        // {"next_control", (getter)ObjectReader::next_control_getter, nullptr, "TODO", NULL},
        // {"pending", (getter)ObjectReader::pending_getter, nullptr, "TODO", NULL},
        // {"thread_number", (getter)Writer::thread_getter, (setter)Writer::thread_setter, "TODO", NULL},
        {NULL}  // Sentinel
    };

    static PyMethodDef methods[] = {
        {"bind", (PyCFunction)ObjectStream::py_bind, METH_O, "TODO"},
        {"close", (PyCFunction)ObjectStream::py_close, METH_NOARGS, "TODO"},
        {"file_offset", (PyCFunction)ObjectStream::py_file_offset, METH_NOARGS,
         "Return the current file read position"},
        {"reopen", (PyCFunction)ObjectStream::py_reopen, METH_VARARGS,
         "Close and reopen the trace file at the given byte offset"},
        {NULL}  // Sentinel
    };

    PyTypeObject ObjectStream_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "ObjectStreamReader",
        .tp_basicsize = sizeof(ObjectStream),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)ObjectStream::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(ObjectStream, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)ObjectStream::traverse,
        .tp_clear = (inquiry)ObjectStream::clear,
        .tp_methods = methods,
        .tp_members = members,
        .tp_getset = getset,
        .tp_init = (initproc)ObjectStream::init,
        .tp_new = PyType_GenericNew,
    };

}
