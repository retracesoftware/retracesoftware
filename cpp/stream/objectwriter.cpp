#include "stream.h"
#include "writer.h"
#include "queueentry.h"

#include <cstddef>
#include <cstdint>
#include <chrono>
#include <thread>
#include <cstdlib>
#include <new>
#include <structmember.h>
#include "wireformat.h"
#include <algorithm>
#include <vector>
#include "unordered_dense.h"

using namespace ankerl::unordered_dense;

#ifdef _WIN32
    #include <process.h>
    #include <windows.h>
    #define getpid _getpid
#else
    #include <unistd.h>
#endif

int pid() {
#ifdef _WIN32
    return static_cast<int>(GetCurrentProcessId());
#else
    return static_cast<int>(getpid());
#endif
}

namespace retracesoftware_stream {

    struct ObjectWriter;
    class Persister;

    static BindingHandle intern_handle(PyObject* obj) {
        return static_cast<BindingHandle>(reinterpret_cast<uintptr_t>(obj));
    }

    struct ObjectWriter : public PyObject {

        Queue * queue = nullptr;
        set<BindingHandle> interned_handles;

        size_t messages_written = 0;
        int pid;
        bool verbose;
        bool quit_on_error;
        vectorcallfunc vectorcall = nullptr;
        PyObject *weakreflist = nullptr;

        ObjectWriter() {}

        inline bool is_disabled() const { return queue == nullptr || !queue->accepting_pushes(); }

        void clear_queue_ref() {
            Py_CLEAR(queue);
        }

        static bool queue_result_to_bool(PyObject* result) {
            if (!result) throw nullptr;
            int truth = PyObject_IsTrue(result);
            Py_DECREF(result);
            if (truth < 0) throw nullptr;
            return truth != 0;
        }

        static PyObject* ptr_as_int(void* ptr) {
            return PyLong_FromUnsignedLongLong((unsigned long long)(uintptr_t)ptr);
        }

        bool call_queue_size(const char* method, size_t len) {
            return queue_result_to_bool(PyObject_CallMethod(queue, const_cast<char*>(method), const_cast<char*>("n"), (Py_ssize_t)len));
        }

        bool call_queue_ptr(const char* method, void* ptr) {
            PyObject* value = ptr_as_int(ptr);
            if (!value) throw nullptr;
            PyObject* result = PyObject_CallMethod(queue, const_cast<char*>(method), const_cast<char*>("O"), value);
            Py_DECREF(value);
            return queue_result_to_bool(result);
        }

        bool call_queue_ptr_obj(const char* method, void* ptr, PyObject* obj) {
            PyObject* value = ptr_as_int(ptr);
            if (!value) throw nullptr;
            PyObject* result = PyObject_CallMethod(queue, const_cast<char*>(method), const_cast<char*>("OO"), value, obj);
            Py_DECREF(value);
            return queue_result_to_bool(result);
        }

        void disable_push_fail() {
            fprintf(stderr, "retrace: writer queue stalled, disabling recording\n");
            if (queue) {
                queue->disable();
            }
        }

        bool disable_if_push_failed(bool ok) {
            if (!ok) disable_push_fail();
            return ok;
        }

        void debug_prefix(size_t bytes_written = 0) {
            printf("Retrace(%i) - ObjectWriter[%lu] -- ", ::pid(), messages_written);
        }

        // Avoid re-entrant repr() on complex proxied objects in verbose mode.
        const char* debugstr(PyObject* obj) {
            static thread_local char buffer[256];
            if (!obj) {
                return "<null>";
            }

            PyTypeObject* tp = Py_TYPE(obj);
            bool scalar =
                obj == Py_None ||
                tp == &PyBool_Type ||
                tp == &PyLong_Type ||
                tp == &PyFloat_Type ||
                tp == &PyUnicode_Type ||
                tp == &PyBytes_Type;

            if (scalar) {
                PyObject* s = PyObject_Str(obj);
                if (s) {
                    const char* utf8 = PyUnicode_AsUTF8(s);
                    if (utf8) {
                        PyOS_snprintf(buffer, sizeof(buffer), "%s", utf8);
                        Py_DECREF(s);
                        return buffer;
                    }
                    Py_DECREF(s);
                }
                PyErr_Clear();
            }

            PyOS_snprintf(buffer, sizeof(buffer), "<%s at %p>", tp->tp_name, obj);
            return buffer;
        }

        void intern_value(PyObject* obj) {
            if (is_disabled()) return;

            if (verbose) {
                debug_prefix();
                printf("INTERN(%s)\n", Py_TYPE(obj)->tp_name);
            }

            if (!disable_if_push_failed(queue->push_intern(obj, intern_handle(obj)))) return;
            messages_written++;
        }

        BindingHandle ensure_interned(PyObject* obj) {
            BindingHandle handle = intern_handle(obj);
            auto [_, inserted] = interned_handles.emplace(handle);
            if (inserted) {
                intern_value(obj);
            }
            return handle;
        }

        static constexpr int MAX_FLATTEN_DEPTH = 32;

        void push_value(PyObject* obj, int depth = 0) {
            if (is_disabled()) return;

            if (Binding_Check(obj)) {
                disable_if_push_failed(queue->push_ref(Binding_Handle(obj)));
            } else {
                PyTypeObject* tp = Py_TYPE(obj);
                
                if (PyMemoryView_Check(obj)) [[unlikely]] {
                    PyObject* bytes = PyObject_Bytes(obj);
                    if (!bytes) {
                        throw nullptr;
                    }
                    const bool pushed = disable_if_push_failed(queue->push_obj(bytes));
                    Py_DECREF(bytes);
                    if (!pushed) {
                        return;
                    }
                }
                else if (tp == &PyUnicode_Type && is_interned_unicode(obj)) [[unlikely]] {
                    disable_if_push_failed(queue->push_ref(ensure_interned(obj)));
                }
                else if (tp == &PyList_Type) [[unlikely]] {
                    assert (depth < MAX_FLATTEN_DEPTH);
                    Py_ssize_t n = PyList_GET_SIZE(obj);
                    if (!disable_if_push_failed(queue->push_list_header((size_t)n))) return;
                    for (Py_ssize_t i = 0; i < n; i++)
                        push_value(PyList_GET_ITEM(obj, i), depth + 1);
                } else if (tp == &PyTuple_Type) [[unlikely]]{
                    assert (depth < MAX_FLATTEN_DEPTH);
                    Py_ssize_t n = PyTuple_GET_SIZE(obj);
                    if (!disable_if_push_failed(queue->push_tuple_header((size_t)n))) return;
                    for (Py_ssize_t i = 0; i < n; i++)
                        push_value(PyTuple_GET_ITEM(obj, i), depth + 1);
                } else if (tp == &PyDict_Type) [[unlikely]] {
                    assert (depth < MAX_FLATTEN_DEPTH);
                    Py_ssize_t n = PyDict_Size(obj);
                    if (!disable_if_push_failed(queue->push_dict_header((size_t)n))) return;
                    Py_ssize_t pos = 0;
                    PyObject *key, *value;
                    while (PyDict_Next(obj, &pos, &key, &value)) {
                        push_value(key, depth + 1);
                        push_value(value, depth + 1);
                    }
                } else {
                    disable_if_push_failed(queue->push_obj(obj));
                }
            }
        }

        void write_root(PyObject * obj) {
            if (verbose) {
                debug_prefix();
                printf("%s\n", debugstr(obj));
            }

            push_value(obj);
            messages_written++;
        }

        void write_all(PyObject*const * args, size_t nargs) {

            if (!is_disabled()) {
                for (size_t i = 0; i < nargs; i++) {
                    write_root(args[i]);
                }
            }
        }

        static PyObject* py_vectorcall(ObjectWriter* self, PyObject*const * args, size_t nargsf, PyObject* kwnames) {

            if (self->is_disabled()) Py_RETURN_NONE;

            if (kwnames) {
                PyErr_SetString(PyExc_TypeError, "ObjectWriter does not accept keyword arguments");
                return nullptr;
            }
         
            try {
                self->write_all(args, PyVectorcall_NARGS(nargsf));
                Py_RETURN_NONE;
            } catch (...) {
                return nullptr;
            }
        }

        static PyObject * py_flush(ObjectWriter * self, PyObject* unused) {
            if (self->is_disabled()) Py_RETURN_NONE;
            try {
                self->disable_if_push_failed(self->queue->push_flush());
                Py_RETURN_NONE;
            } catch (...) {
                return nullptr;
            }
        }

        static PyObject* py_disable(ObjectWriter* self, PyObject* unused) {
            if (self->queue) {
                self->queue->disable();
            }
            Py_RETURN_NONE;
        }

        static PyObject* py_heartbeat(ObjectWriter* self, PyObject* payload) {
            if (self->is_disabled()) Py_RETURN_NONE;
            if (!PyDict_Check(payload)) {
                PyErr_SetString(PyExc_TypeError, "heartbeat payload must be a dict");
                return nullptr;
            }
            try {
                if (!self->disable_if_push_failed(self->queue->push_heartbeat())) Py_RETURN_NONE;
                self->push_value(payload);
                if (self->is_disabled()) Py_RETURN_NONE;
                self->disable_if_push_failed(self->queue->push_flush());
                Py_RETURN_NONE;
            } catch (...) {
                return nullptr;
            }
        }

        static PyObject * py__intern(ObjectWriter * self, PyObject* obj);

        static PyObject* py_new(PyTypeObject* type, PyObject*, PyObject*) {
            ObjectWriter* self = reinterpret_cast<ObjectWriter*>(type->tp_alloc(type, 0));
            if (!self) return nullptr;
            new (self) ObjectWriter();
            return reinterpret_cast<PyObject*>(self);
        }

        static int init(ObjectWriter * self, PyObject* args, PyObject* kwds) {

            PyObject * queue_obj;
            
            int verbose = 0;
            int quit_on_error = 0;
            static const char* kwlist[] = {
                "queue",
                "verbose",
                "quit_on_error",
                nullptr};

            if (!PyArg_ParseTupleAndKeywords(args, kwds, "O!|pp", (char **)kwlist,
                &Queue_Type, &queue_obj, &verbose, &quit_on_error)) {
                return -1;
            }

            self->verbose = verbose;
            self->quit_on_error = quit_on_error;
            
            self->messages_written = 0;
            
            self->vectorcall = reinterpret_cast<vectorcallfunc>(ObjectWriter::py_vectorcall);

            self->queue = reinterpret_cast<Queue*>(Py_NewRef(queue_obj));

            return 0;
        }

        static int traverse(ObjectWriter* self, visitproc visit, void* arg) {
            Py_VISIT(self->queue);
            return 0;
        }

        static int clear(ObjectWriter* self) {
            self->interned_handles.clear();
            self->clear_queue_ref();
            return 0;
        }

        static void dealloc(ObjectWriter* self) {
            if (self->queue) {
                try {
                    (void)self->queue->push_shutdown();
                } catch (...) {
                    PyErr_Clear();
                }
            }
            
            if (self->weakreflist != NULL) {
                PyObject_ClearWeakRefs((PyObject *)self);
            }

            PyObject_GC_UnTrack(self);
            clear(self);
            self->~ObjectWriter();

            Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
        }

        static PyObject * output_getter(ObjectWriter *self, void *closure) {
            return Py_NewRef(self->queue ? self->queue : Py_None);
        }

        static int output_setter(ObjectWriter *self, PyObject *value, void *closure) {
            if (value == nullptr) {
                PyErr_SetString(PyExc_AttributeError, "deletion of 'output' is not allowed");
                return -1;
            }
            if (value == Py_None) {
                self->clear_queue_ref();
                return 0;
            }
            if (!PyObject_TypeCheck(value, &Queue_Type)) {
                PyErr_SetString(PyExc_TypeError, "output must be a Queue or None");
                return -1;
            }
            self->clear_queue_ref();
            self->queue = reinterpret_cast<Queue*>(Py_NewRef(value));
            return 0;
        }
    };

    PyObject* ObjectWriter::py__intern(ObjectWriter* self, PyObject* obj) {
        try {
            (void)self->ensure_interned(obj);
            Py_RETURN_NONE;
        } catch (...) {
            return nullptr;
        }
    }

    // --- ObjectWriter type ---

    static PyMethodDef methods[] = {
        {"flush", (PyCFunction)ObjectWriter::py_flush, METH_NOARGS, "Flush buffered data to the output callback"},
        {"disable", (PyCFunction)ObjectWriter::py_disable, METH_NOARGS, "Null queue pointers to prevent further writes"},
        {"heartbeat", (PyCFunction)ObjectWriter::py_heartbeat, METH_O, "Push heartbeat payload dict and flush"},
        {"_intern", (PyCFunction)ObjectWriter::py__intern, METH_O, "TODO"},
        {NULL}
    };

    static PyMemberDef members[] = {
        {"messages_written", T_ULONGLONG, OFFSET_OF_MEMBER(ObjectWriter, messages_written), READONLY, "TODO"},
        {"verbose", T_BOOL, OFFSET_OF_MEMBER(ObjectWriter, verbose), 0, "TODO"},
        {NULL}
    };

    static PyGetSetDef getset[] = {
        {"queue", (getter)ObjectWriter::output_getter, (setter)ObjectWriter::output_setter, "Attached queue", NULL},
        {"output", (getter)ObjectWriter::output_getter, (setter)ObjectWriter::output_setter, "Output callback", NULL},
        {NULL}
    };

    PyTypeObject ObjectWriter_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "ObjectWriter",
        .tp_basicsize = sizeof(ObjectWriter),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)ObjectWriter::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(ObjectWriter, vectorcall),
        .tp_hash = (hashfunc)_Py_HashPointer,
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)ObjectWriter::traverse,
        .tp_clear = (inquiry)ObjectWriter::clear,
        .tp_weaklistoffset = OFFSET_OF_MEMBER(ObjectWriter, weakreflist),
        .tp_methods = methods,
        .tp_members = members,
        .tp_getset = getset,
        .tp_init = (initproc)ObjectWriter::init,
        .tp_new = ObjectWriter::py_new,
    };
}
