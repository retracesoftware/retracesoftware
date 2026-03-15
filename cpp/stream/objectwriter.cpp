#include "stream.h"
#include "writer.h"
#include "queueentry.h"

#include <cstddef>
#include <cstdint>
#include <chrono>
#include <thread>
#include <cstdlib>
#include <structmember.h>
#include "wireformat.h"
#include <algorithm>
#include <vector>
#include "unordered_dense.h"
#include "base.h"

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

    static void trace_bind_event(const char* stage, PyObject* obj, long a = -1, long b = -1) {
        if (!bind_trace_enabled()) return;
        fprintf(stderr,
                "retrace-bind[%d] %s obj=%p label=%s type=%p a=%ld b=%ld\n",
                pid(),
                stage,
                (void*)obj,
                bind_label(obj),
                obj ? (void*)Py_TYPE(obj) : nullptr,
                a,
                b);
        fflush(stderr);
    }

    struct ObjectWriter;
    struct AsyncFilePersister;

    static std::vector<ObjectWriter *> writers;

    struct StreamHandle : public PyObject {
        Ref handle;
        PyObject * writer;
        PyObject * object;
        vectorcallfunc vectorcall;
        
        static int traverse(StreamHandle* self, visitproc visit, void* arg) {
            Py_VISIT(self->writer);
            Py_VISIT(self->object);
            return 0;
        }

        static int clear(StreamHandle* self) {
            Py_CLEAR(self->writer);
            Py_CLEAR(self->object);
            return 0;
        }

        static PyObject* get_index(StreamHandle* self, void*) {
            return PyLong_FromUnsignedLongLong(index_of_handle(self->handle));
        }
    };
    
    struct WeakRefCallback : public PyObject {
        PyObject * handle;
        PyObject * writer;
        bool delete_ref_only;
        vectorcallfunc vectorcall;
        
        static PyObject* call(WeakRefCallback* self,
                              PyObject* const* args, size_t nargsf,
                              PyObject* kwnames);
        static int traverse(WeakRefCallback* self, visitproc visit, void* arg);
        static int clear(WeakRefCallback* self);
        static void dealloc(WeakRefCallback* self);
    };

    PyTypeObject WeakRefCallback_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "WeakRefCallback",
        .tp_basicsize = sizeof(WeakRefCallback),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)WeakRefCallback::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(WeakRefCallback, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_traverse = (traverseproc)WeakRefCallback::traverse,
        .tp_clear = (inquiry)WeakRefCallback::clear,
    };

    static PyObject* pickle_dumps_fn() {
        static PyObject* dumps = nullptr;
        if (!dumps) {
            PyObject* mod = PyImport_ImportModule("pickle");
            if (!mod) { PyErr_Clear(); return nullptr; }
            dumps = PyObject_GetAttrString(mod, "dumps");
            Py_DECREF(mod);
            if (!dumps) { PyErr_Clear(); return nullptr; }
        }
        return dumps;
    }

    static PyTypeObject* wrapped_type() {
        static PyTypeObject* wrapped = nullptr;
        if (!wrapped) {
            PyObject* mod = PyImport_ImportModule("retracesoftware.utils");
            if (!mod) return nullptr;

            PyObject* obj = PyObject_GetAttrString(mod, "Wrapped");
            Py_DECREF(mod);
            if (!obj) return nullptr;
            if (!PyType_Check(obj)) {
                Py_DECREF(obj);
                PyErr_SetString(PyExc_TypeError, "retracesoftware.utils.Wrapped is not a type");
                return nullptr;
            }

            wrapped = reinterpret_cast<PyTypeObject*>(obj);
        }
        return wrapped;
    }

    static inline int64_t native_estimate(PyObject* obj) {
        PyTypeObject* tp = Py_TYPE(obj);
        if (tp == &PyLong_Type)   return 28;
        if (tp == &PyUnicode_Type)
            return (int64_t)(sizeof(PyObject) + PyUnicode_GET_LENGTH(obj));
        if (tp == &PyBytes_Type)
            return (int64_t)(sizeof(PyObject) + PyBytes_GET_SIZE(obj));
        if (tp == &StreamHandle_Type) return 64;
    if (is_retrace_patched_type(tp)) return 64;
        if (tp == &PyFloat_Type)  return 24;
        if (tp == &PyMemoryView_Type) {
            Py_buffer* view = PyMemoryView_GET_BUFFER(obj);
            return (int64_t)(sizeof(PyObject) + view->len);
        }
        return -1;
    }

    static thread_local bool writing = false;

    class Writing {
    private:
        bool previous;

    public:
        Writing() : previous(writing) {
            writing = true;
        }

        ~Writing() {
            writing = previous;
        }

        inline Writing(const Writing&) = delete;
        inline Writing& operator=(const Writing&) = delete;
        inline Writing(Writing&&) = delete;
        inline Writing& operator=(Writing&&) = delete;
    };

    struct ObjectWriter : public ReaderWriterBase {
        
        Queue* queue = nullptr;
        set<PyObject*>* bindings = nullptr;
        set<PyObject*>* bound_wrapped = nullptr;
        map<PyObject*, PyObject*>* bound_wrapped_refs = nullptr;
        map<PyObject*, PyObject*>* bound_ref_deleters = nullptr;
        PyObject* persister = nullptr;

        size_t messages_written = 0;
        uintptr_t next_handle;
        int pid;
        bool verbose;
        bool validate_bindings = false;
        bool quit_on_error;
        bool serialize_errors = true;
        bool buffer_writes = true;
        PyObject * serializer = nullptr;
        PyObject * enable_when;
        PyObject* thread;
        vectorcallfunc vectorcall;
        PyObject *weakreflist;

        inline bool is_disabled() const { return queue == nullptr; }

        void disable_push_fail() {
            fprintf(stderr, "retrace: writer queue stalled, disabling recording\n");
            queue = nullptr;
        }

        void disable_if_push_failed(bool ok) {
            if (!ok) disable_push_fail();
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

        static PyObject * StreamHandle_vectorcall(StreamHandle * self, PyObject *const * args, size_t nargsf, PyObject* kwnames) {
            
            ObjectWriter * writer = reinterpret_cast<ObjectWriter *>(self->writer);

            if (writer->is_disabled()) {
                Py_RETURN_NONE;
            }

            try {
                writer->write_all(self, args, PyVectorcall_NARGS(nargsf));
                Py_RETURN_NONE;
            } catch (...) {
                return nullptr;
            }
        }
        
        void bind(PyObject * obj) {
            if (is_disabled()) return;

            if (bindings) {
                if (bindings->contains(obj)) {
                    PyErr_Format(PyExc_RuntimeError, "<%s object at %p> already bound", Py_TYPE(obj)->tp_name, (void *)obj);
                    throw nullptr;
                }
                if (!bindings->contains((PyObject *)Py_TYPE(obj))) {
                    PyErr_Format(PyExc_RuntimeError, "to bind <%s object at %p>, object type %s must have been bound first",
                        Py_TYPE(obj)->tp_name, (void *)obj, Py_TYPE(obj)->tp_name);
                    throw nullptr;
                }
                bindings->insert(obj);
            }

            ensure_bound_ref_tracking(obj);

            trace_bind_event("producer-bind-enter", obj, (long)messages_written);
            send_thread();

            Writing w;

            if (verbose) {
                debug_prefix();
                printf("BIND(%s)\n", Py_TYPE(obj)->tp_name);
            }

            disable_if_push_failed(queue->push_bind(obj));
            messages_written++;
            trace_bind_event("producer-bind-enqueued", obj, (long)messages_written);
        }

        void new_patched(PyObject * obj) {
            if (is_disabled()) return;

            PyObject* type = (PyObject *)Py_TYPE(obj);
            if (bindings) {
                if (bindings->contains(obj)) {
                    PyErr_Format(PyExc_RuntimeError, "<%s object at %p> already bound", Py_TYPE(obj)->tp_name, (void *)obj);
                    throw nullptr;
                }
                if (!bindings->contains(type)) {
                    PyErr_Format(PyExc_RuntimeError, "to new_patch <%s object at %p>, object type %s must have been bound first",
                        Py_TYPE(obj)->tp_name, (void *)obj, Py_TYPE(obj)->tp_name);
                    throw nullptr;
                }
                bindings->insert(obj);
            }

            send_thread();

            Writing w;

            if (verbose) {
                debug_prefix();
                printf("NEW_PATCHED(%s)\n", Py_TYPE(obj)->tp_name);
            }

            disable_if_push_failed(queue->push_new_patched(obj, type));
            messages_written++;
        }

        void write_delete(Ref handle) {
            if (is_disabled()) return;

            if (verbose) {
                debug_prefix();
                printf("DELETE(%p)\n", handle);
            }
            disable_if_push_failed(queue->push_ref_delete(handle));
            messages_written++;
        }

        static void StreamHandle_dealloc(StreamHandle* self) {
            
            ObjectWriter * writer = reinterpret_cast<ObjectWriter *>(self->writer);

            if (writer && !writer->is_disabled() && !_Py_IsFinalizing()) {
                writer->write_delete(self->handle);
            }

            PyObject_GC_UnTrack(self);
            StreamHandle::clear(self);
            Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
        }

        PyObject * stream_handle(Ref handle, PyObject * obj) {

            StreamHandle * self = (StreamHandle *)StreamHandle_Type.tp_alloc(&StreamHandle_Type, 0);
            if (!self) return nullptr;

            self->writer = Py_NewRef(this);
            self->handle = handle;
            self->vectorcall = (vectorcallfunc)StreamHandle_vectorcall;
            self->object = Py_XNewRef(obj);

            return (PyObject *)self;
        }

        PyObject * handle(PyObject * obj) {
            Ref handle = handle_from_index(next_handle++);

            if (is_disabled()) {
                return stream_handle(handle, nullptr);
            }

            if (verbose) {
                debug_prefix();
                printf("NEW_HANDLE(%s)\n", debugstr(obj));
            }

            if (!queue->push_new_handle(handle, obj, (int64_t)estimate_size(obj))) {
                disable_push_fail();
                return stream_handle(handle, nullptr);
            }
            messages_written++;
            return stream_handle(handle, verbose ? obj : nullptr);
        }

        void write_root(StreamHandle * obj) {
            if (verbose) {
                debug_prefix();
                printf("HANDLE_REF(%s)\n", debugstr(obj->object));
            }

            disable_if_push_failed(queue->push_ref(obj->handle));
            messages_written++;
        }

        static constexpr int MAX_FLATTEN_DEPTH = 32;

        void push_obj(PyObject* obj, size_t estimated_size) {
            if (!queue->push_obj(obj, (int64_t)estimated_size)) {
                disable_push_fail();
                return;
            }
        }

        void ensure_wrapped_tracking(PyObject* obj) {
            if (!bound_wrapped || !bound_wrapped_refs) return;
            if (bound_wrapped_refs->contains(obj)) return;

            auto* callback = reinterpret_cast<WeakRefCallback*>(
                WeakRefCallback_Type.tp_alloc(&WeakRefCallback_Type, 0));
            if (!callback) throw nullptr;

            callback->handle = obj;
            callback->writer = Py_NewRef((PyObject*)this);
            callback->delete_ref_only = false;
            callback->vectorcall = (vectorcallfunc)WeakRefCallback::call;

            PyObject* weakref = PyWeakref_NewRef(obj, (PyObject*)callback);
            Py_DECREF((PyObject*)callback);
            if (!weakref) throw nullptr;

            bound_wrapped->insert(obj);
            (*bound_wrapped_refs)[obj] = weakref;
        }

        void ensure_bound_ref_tracking(PyObject* obj) {
            if (!bound_ref_deleters) return;
            if (bound_ref_deleters->contains(obj)) return;

            auto* callback = reinterpret_cast<WeakRefCallback*>(
                WeakRefCallback_Type.tp_alloc(&WeakRefCallback_Type, 0));
            if (!callback) throw nullptr;

            callback->handle = obj;
            callback->writer = Py_NewRef((PyObject*)this);
            callback->delete_ref_only = true;
            callback->vectorcall = (vectorcallfunc)WeakRefCallback::call;

            PyObject* weakref = PyWeakref_NewRef(obj, (PyObject*)callback);
            Py_DECREF((PyObject*)callback);
            if (!weakref) {
                if (PyErr_ExceptionMatches(PyExc_TypeError)) {
                    PyErr_Clear();
                    return;
                }
                throw nullptr;
            }

            (*bound_ref_deleters)[obj] = weakref;
        }

        Ref ensure_auto_handle(PyObject* obj) {
            Ref handle = reinterpret_cast<Ref>(obj);
            PyTypeObject* wrapped = wrapped_type();
            if (wrapped && PyObject_TypeCheck(obj, wrapped) && !is_retrace_patched_type(Py_TYPE(obj))) {
                ensure_wrapped_tracking(obj);
            }
            return handle;
        }

        void push_value(PyObject* obj, int depth = 0) {

            if (is_immortal(obj)) {
                disable_if_push_failed(queue->push_obj(obj));
            } else {
                PyTypeObject* tp = Py_TYPE(obj);
                PyTypeObject* wrapped = wrapped_type();

                if (tp == &PyLong_Type) {
                    push_obj(obj, (size_t)estimate_long_size(obj));
                } else if (tp == &PyUnicode_Type) {
                    push_obj(obj, (size_t)estimate_unicode_size(obj));
                } else if (tp == &PyBytes_Type) {
                    push_obj(obj, (size_t)estimate_bytes_size(obj));
                } else if (tp == &StreamHandle_Type) {
                    disable_if_push_failed(queue->push_ref(reinterpret_cast<StreamHandle*>(obj)->handle));
                } else if (is_retrace_patched_type(tp)) {
                    disable_if_push_failed(queue->push_ref(ensure_auto_handle(obj)));
                } else if (tp == &PyList_Type) {
                    assert (depth < MAX_FLATTEN_DEPTH);
                    Py_ssize_t n = PyList_GET_SIZE(obj);
                    disable_if_push_failed(queue->push_list_header((size_t)n));
                    for (Py_ssize_t i = 0; i < n; i++)
                        push_value(PyList_GET_ITEM(obj, i), depth + 1);

                } else if (tp == &PyTuple_Type) {
                    assert (depth < MAX_FLATTEN_DEPTH);
                    Py_ssize_t n = PyTuple_GET_SIZE(obj);
                    disable_if_push_failed(queue->push_tuple_header((size_t)n));
                    for (Py_ssize_t i = 0; i < n; i++)
                        push_value(PyTuple_GET_ITEM(obj, i), depth + 1);
                } else if (tp == &PyDict_Type) {
                    assert (depth < MAX_FLATTEN_DEPTH);
                    Py_ssize_t n = PyDict_Size(obj);
                    disable_if_push_failed(queue->push_dict_header((size_t)n));
                    Py_ssize_t pos = 0;
                    PyObject *key, *value;
                    while (PyDict_Next(obj, &pos, &key, &value)) {
                        push_value(key, depth + 1);
                        push_value(value, depth + 1);
                    }
                } else if (tp == &PyFloat_Type) {
                    push_obj(obj, (size_t)estimate_float_size(obj));
                } else if (tp == &PyMemoryView_Type) {
                    push_obj(obj, (size_t)estimate_memory_view_size(obj));

                } else if (wrapped && PyObject_TypeCheck(obj, wrapped)) {
                    disable_if_push_failed(queue->push_ref(ensure_auto_handle(obj)));
                } else {
                    // Try the full serializer (type_serializer + pickle fallback)
                    PyObject* res = PyObject_CallOneArg(serializer, obj);
                    if (res) {
                        if (PyBytes_Check(res)) {
                            // Serializer returned pickled bytes
                            bool ok = queue->push_pickled(res, estimate_bytes_size(res));
                            Py_DECREF(res);
                            if (!ok) {
                                disable_push_fail();
                                return;
                            }
                        } else {
                            // Serializer returned a converted object (e.g. Stack → tuple)
                            push_value(res, depth + 1);
                            Py_DECREF(res);
                        }
                    } else {
                        // Serialization failed — push SERIALIZE_ERROR tag + error info dict
                        PyObject *ptype, *pvalue, *ptb;
                        PyErr_Fetch(&ptype, &pvalue, &ptb);

                        PyObject* error_dict = PyDict_New();
                        if (error_dict) {
                            PyObject* obj_type_str = PyUnicode_FromString(Py_TYPE(obj)->tp_name);
                            if (obj_type_str) { PyDict_SetItemString(error_dict, "object_type", obj_type_str); Py_DECREF(obj_type_str); }

                            if (ptype) {
                                PyObject* name = PyObject_GetAttrString(ptype, "__name__");
                                if (name) { PyDict_SetItemString(error_dict, "error_type", name); Py_DECREF(name); }
                                else PyErr_Clear();
                            }
                            if (pvalue) {
                                PyObject* msg = PyObject_Str(pvalue);
                                if (msg) { PyDict_SetItemString(error_dict, "error", msg); Py_DECREF(msg); }
                                else PyErr_Clear();
                            }

                            disable_if_push_failed(queue->push_serialize_error());
                            push_value(error_dict, depth + 1);
                            Py_DECREF(error_dict);
                        } else {
                            PyErr_Clear();
                            disable_if_push_failed(queue->push_obj(Py_None));
                        }

                        if (!serialize_errors) {
                            PyErr_Restore(ptype, pvalue, ptb);
                            throw nullptr;
                        } else {
                            Py_XDECREF(ptype);
                            Py_XDECREF(pvalue);
                            Py_XDECREF(ptb);
                        }
                    }
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

        void object_freed(PyObject * obj) {
            if (is_disabled()) return;

            PyTypeObject* wrapped = wrapped_type();
            bool is_auto_ref =
                is_retrace_patched_type(Py_TYPE(obj)) ||
                (wrapped && PyObject_TypeCheck(obj, wrapped) && !is_retrace_patched_type(Py_TYPE(obj)));

            if (bindings) bindings->erase(obj);
            if (bound_wrapped) bound_wrapped->erase(obj);
            if (bound_wrapped_refs) {
                auto it = bound_wrapped_refs->find(obj);
                if (it != bound_wrapped_refs->end()) {
                    Py_DECREF(it->second);
                    bound_wrapped_refs->erase(it);
                }
            }
            if (bound_ref_deleters) {
                auto it = bound_ref_deleters->find(obj);
                if (it != bound_ref_deleters->end()) {
                    Py_DECREF(it->second);
                    bound_ref_deleters->erase(it);
                }
            }
            if (is_auto_ref) {
                disable_if_push_failed(queue->push_ref_delete(reinterpret_cast<Ref>(obj)));
            }
            disable_if_push_failed(queue->push_delete(obj));
        }

        void write_all(StreamHandle * self, PyObject *const * args, size_t nargs) {
            if (!is_disabled()) {
                send_thread();

                Writing w;

                write_root(self);
                for (size_t i = 0; i < nargs; i++) {
                    write_root(args[i]);
                }
            }
        }

        void write_all(PyObject*const * args, size_t nargs) {

            if (!is_disabled()) {
                send_thread();

                Writing w;

                for (size_t i = 0; i < nargs; i++) {
                    write_root(args[i]);
                }
            }
        }

        bool enabled() {
            if (enable_when) {
                PyObject * result = PyObject_CallNoArgs(enable_when);
                if (!result) throw nullptr;
                int is_true = PyObject_IsTrue(result);
                Py_DECREF(result);

                switch(is_true) {
                    case 1: 
                        Py_DECREF(enable_when);
                        enable_when = nullptr;
                        return true;
                    case 0:
                        return false;
                    default:
                        throw nullptr;
                }
            }
            return true;
        }

        static PyObject* py_vectorcall(ObjectWriter* self, PyObject*const * args, size_t nargsf, PyObject* kwnames) {

            if (self->is_disabled()) Py_RETURN_NONE;

            if (kwnames) {
                PyErr_SetString(PyExc_TypeError, "ObjectWriter does not accept keyword arguments");
                return nullptr;
            }
         
            try {
                self->write_all(args, PyVectorcall_NARGS(nargsf));
                if (!self->buffer_writes) {
                    self->disable_if_push_failed(self->queue->push_flush());
                }
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
            self->queue = nullptr;
            Py_RETURN_NONE;
        }

        static PyObject* py_heartbeat(ObjectWriter* self, PyObject* payload) {
            if (self->is_disabled()) Py_RETURN_NONE;
            if (!PyDict_Check(payload)) {
                PyErr_SetString(PyExc_TypeError, "heartbeat payload must be a dict");
                return nullptr;
            }
            try {
                self->disable_if_push_failed(self->queue->push_heartbeat());
                self->push_value(payload);
                self->disable_if_push_failed(self->queue->push_flush());
                Py_RETURN_NONE;
            } catch (...) {
                return nullptr;
            }
        }

        static PyObject * py_handle(ObjectWriter * self, PyObject* obj) {
            try {
                return self->handle(obj);
            } catch (...) {
                return nullptr;
            }
        }

        static PyObject * py_deleter(ObjectWriter * self, PyObject* obj);

        static PyObject * py_bind(ObjectWriter * self, PyObject* obj);
        static PyObject * py_new_patched(ObjectWriter * self, PyObject* args);
        static PyObject * py_ext_bind(ObjectWriter * self, PyObject* obj);
        // defined after Deleter

        static int init(ObjectWriter * self, PyObject* args, PyObject* kwds) {

            PyObject * output;
            
            PyObject * thread = nullptr;
            PyObject * normalize_path = nullptr;
            PyObject * serializer = nullptr;
            int verbose = 0;
            int quit_on_error = 0;
            int serialize_errors = 1;
            int validate_bindings = 0;
            long long inflight_limit_arg = 128LL * 1024 * 1024;
            int stall_timeout_arg = 5;
            Py_ssize_t queue_capacity_arg = 65536;
            Py_ssize_t return_queue_capacity_arg = 131072;

            static const char* kwlist[] = {
                "output",
                "serializer",
                "thread",
                "verbose",
                "normalize_path",
                "inflight_limit",
                "stall_timeout",
                "queue_capacity",
                "return_queue_capacity",
                "quit_on_error",
                "serialize_errors",
                "validate_bindings",
                nullptr};

            if (!PyArg_ParseTupleAndKeywords(args, kwds, "OO|OpOLinnppp", (char **)kwlist,
                &output, &serializer, &thread, &verbose, &normalize_path,
                &inflight_limit_arg, &stall_timeout_arg,
                &queue_capacity_arg, &return_queue_capacity_arg,
                &quit_on_error, &serialize_errors, &validate_bindings)) {
                return -1;
            }

            self->verbose = verbose;
            self->validate_bindings = validate_bindings;
            self->quit_on_error = quit_on_error;
            self->serialize_errors = serialize_errors;
            self->buffer_writes = true;

            self->path = Py_NewRef(Py_None);
            self->thread = thread && thread != Py_None ? Py_NewRef(thread) : nullptr;

            self->messages_written = 0;
            self->next_handle = 0;
            
            self->vectorcall = reinterpret_cast<vectorcallfunc>(ObjectWriter::py_vectorcall);

            self->serializer = Py_NewRef(serializer);
            self->normalize_path = Py_XNewRef(normalize_path);
            self->enable_when = nullptr;
            self->queue = nullptr;
            self->bindings = self->validate_bindings ? new set<PyObject*>() : nullptr;
            self->bound_wrapped = new set<PyObject*>();
            self->bound_wrapped_refs = new map<PyObject*, PyObject*>();
            self->bound_ref_deleters = new map<PyObject*, PyObject*>();
            self->persister = nullptr;

            if (output != Py_None &&
                    (Py_TYPE(output) == &AsyncFilePersister_Type ||
                     Py_TYPE(output) == &DebugPersister_Type)) {
                SetupResult r = Py_TYPE(output) == &AsyncFilePersister_Type
                    ? AsyncFilePersister_setup(output, serializer,
                                               (size_t)queue_capacity_arg,
                                               (size_t)return_queue_capacity_arg,
                                               inflight_limit_arg,
                                               stall_timeout_arg,
                                               self->thread,
                                               self->quit_on_error)
                    : DebugPersister_setup(output, serializer,
                                           (size_t)queue_capacity_arg,
                                           (size_t)return_queue_capacity_arg,
                                           inflight_limit_arg,
                                           stall_timeout_arg,
                                           self->thread,
                                           self->quit_on_error);
                if (!r.forward_queue) return -1;
                self->queue = r.forward_queue;
                self->persister = Py_NewRef(output);
            }

            writers.push_back(self);

            return 0;
        }

        void send_thread() {
            if (!thread) return;
            if (bind_trace_enabled()) {
                fprintf(stderr,
                        "retrace-bind[%d] producer-thread-stamp tstate=%p messages=%lu\n",
                        ::pid(),
                        (void*)PyThreadState_Get(),
                        messages_written);
                fflush(stderr);
            }
            disable_if_push_failed(queue->push_thread(PyThreadState_Get()));
        }

        static int traverse(ObjectWriter* self, visitproc visit, void* arg) {
            Py_VISIT(self->persister);
            Py_VISIT(self->serializer);
            Py_VISIT(self->thread);
            Py_VISIT(self->path);
            Py_VISIT(self->normalize_path);
            if (self->bound_wrapped_refs) {
                for (auto& [_, weakref] : *self->bound_wrapped_refs) {
                    Py_VISIT(weakref);
                }
            }
            if (self->bound_ref_deleters) {
                for (auto& [_, weakref] : *self->bound_ref_deleters) {
                    Py_VISIT(weakref);
                }
            }
            return 0;
        }

        static int clear(ObjectWriter* self) {
            Py_CLEAR(self->persister);
            Py_CLEAR(self->serializer);
            Py_CLEAR(self->thread);
            Py_CLEAR(self->path);
            Py_CLEAR(self->normalize_path);
            if (self->bound_wrapped_refs) {
                for (auto& [_, weakref] : *self->bound_wrapped_refs) {
                    Py_CLEAR(weakref);
                }
                self->bound_wrapped_refs->clear();
            }
            if (self->bound_ref_deleters) {
                for (auto& [_, weakref] : *self->bound_ref_deleters) {
                    Py_CLEAR(weakref);
                }
                self->bound_ref_deleters->clear();
            }
            return 0;
        }

        static void dealloc(ObjectWriter* self) {
            if (self->queue) {
                self->disable_if_push_failed(self->queue->push_shutdown());
                self->queue = nullptr;
            }
            
            if (self->weakreflist != NULL) {
                PyObject_ClearWeakRefs((PyObject *)self);
            }

            PyObject_GC_UnTrack(self);
            clear(self);

            if (self->bindings) {
                delete self->bindings;
                self->bindings = nullptr;
            }
            if (self->bound_wrapped) {
                delete self->bound_wrapped;
                self->bound_wrapped = nullptr;
            }
            if (self->bound_wrapped_refs) {
                delete self->bound_wrapped_refs;
                self->bound_wrapped_refs = nullptr;
            }
            if (self->bound_ref_deleters) {
                delete self->bound_ref_deleters;
                self->bound_ref_deleters = nullptr;
            }

            Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));

            auto it = std::find(writers.begin(), writers.end(), self);
            if (it != writers.end()) {
                writers.erase(it);
            }
        }

        static PyObject * path_getter(ObjectWriter *self, void *closure) {
            return Py_NewRef(self->path);
        }

        static int path_setter(ObjectWriter *self, PyObject *value, void *closure) {
            if (value == nullptr) {
                PyErr_SetString(PyExc_AttributeError, "deletion of 'path' is not allowed");
                return -1;
            }

            Py_DECREF(self->path);
            self->path = Py_NewRef(value);
            return 0;
        }

        static PyObject * output_getter(ObjectWriter *self, void *closure) {
            PyObject* p = self->persister;
            return Py_NewRef(p ? p : Py_None);
        }

        static int output_setter(ObjectWriter *self, PyObject *value, void *closure) {
            if (value == nullptr) {
                PyErr_SetString(PyExc_AttributeError, "deletion of 'output' is not allowed");
                return -1;
            }
            if (value == Py_None) {
                self->queue = nullptr;
                Py_CLEAR(self->persister);
            }
            return 0;
        }

        static PyObject * bytes_written_getter(ObjectWriter *self, void *closure) {
            return PyLong_FromLong(0);
        }

        static PyObject * inflight_limit_getter(ObjectWriter *self, void *closure) {
            return PyLong_FromLongLong(self->queue ? self->queue->inflight_limit() : 0);
        }

        static int inflight_limit_setter(ObjectWriter *self, PyObject *value, void *closure) {
            if (value == nullptr) {
                PyErr_SetString(PyExc_AttributeError, "deletion of 'inflight_limit' is not allowed");
                return -1;
            }
            long long v = PyLong_AsLongLong(value);
            if (v == -1 && PyErr_Occurred()) return -1;
            if (self->queue) self->queue->set_inflight_limit((int64_t)v);
            return 0;
        }

        static PyObject * inflight_bytes_getter(ObjectWriter *self, void *closure) {
            return PyLong_FromLongLong(self->queue ? self->queue->inflight() : 0);
        }
    };

    PyObject* WeakRefCallback::call(WeakRefCallback* self,
                                    PyObject* const* args, size_t nargsf,
                                    PyObject* kwnames) {
        if (PyVectorcall_NARGS(nargsf) != 1 || kwnames) {
            PyErr_SetString(PyExc_TypeError, "WeakRefCallback takes one positional argument");
            return nullptr;
        }

        auto* w = reinterpret_cast<ObjectWriter*>(self->writer);
        if (w && !w->is_disabled()) {
            if (self->delete_ref_only) {
                w->send_thread();
                Writing writing;
                w->write_delete(reinterpret_cast<Ref>(self->handle));
            } else {
                w->object_freed(self->handle);
            }
        }
        Py_RETURN_NONE;
    }

    int WeakRefCallback::traverse(WeakRefCallback* self, visitproc visit, void* arg) {
        Py_VISIT(self->writer);
        return 0;
    }

    int WeakRefCallback::clear(WeakRefCallback* self) {
        Py_CLEAR(self->writer);
        return 0;
    }

    void WeakRefCallback::dealloc(WeakRefCallback* self) {
        PyObject_GC_UnTrack(self);
        clear(self);
        Py_TYPE(self)->tp_free((PyObject*)self);
    }

    // ── Deleter ──────────────────────────────────────────────────
    //
    // Callable returned by deleter()/new_patched(). When invoked (no args)
    // on object deallocation, pushes a delete onto the owning
    // ObjectWriter's SPSC queue.

    struct Deleter : public PyObject {
        PyObject* writer;      // strong ref to ObjectWriter (for GC)
        PyObject* addr;        // raw identity pointer for object_freed path (no refcount)
        Ref ref;              // stored ref token for ref-delete path
        bool delete_ref_only;
        vectorcallfunc vectorcall;

        static PyObject* call(Deleter* self,
                              PyObject* const* args, size_t nargsf,
                              PyObject* kwnames) {
            if (PyVectorcall_NARGS(nargsf) != 0 || kwnames) {
                PyErr_SetString(PyExc_TypeError, "Deleter takes no arguments");
                return nullptr;
            }
            auto* w = reinterpret_cast<ObjectWriter*>(self->writer);
            if (w && !w->is_disabled()) {
                if (self->delete_ref_only) {
                    w->send_thread();
                    Writing writing;
                    w->write_delete(self->ref);
                } else {
                    w->object_freed(self->addr);
                }
            }
            Py_RETURN_NONE;
        }

        static void dealloc(Deleter* self) {
            PyObject_GC_UnTrack(self);
            Py_CLEAR(self->writer);
            Py_TYPE(self)->tp_free((PyObject*)self);
        }

        static int traverse(Deleter* self, visitproc visit, void* arg) {
            Py_VISIT(self->writer);
            return 0;
        }

        static int clear(Deleter* self) {
            Py_CLEAR(self->writer);
            return 0;
        }
    };

    PyTypeObject Deleter_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "Deleter",
        .tp_basicsize = sizeof(Deleter),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)Deleter::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(Deleter, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_traverse = (traverseproc)Deleter::traverse,
        .tp_clear = (inquiry)Deleter::clear,
    };

    static PyObject * create_new_patched_deleter(ObjectWriter * self, PyObject * obj) {
        try {
            self->new_patched(obj);

            auto* d = reinterpret_cast<Deleter*>(
                Deleter_Type.tp_alloc(&Deleter_Type, 0));
            if (!d) return nullptr;

            d->writer = Py_NewRef((PyObject*)self);
            d->addr = obj;
            d->ref = nullptr;
            d->delete_ref_only = false;
            d->vectorcall = (vectorcallfunc)Deleter::call;

            return (PyObject*)d;
        } catch (...) {
            return nullptr;
        }
    }

    PyObject* ObjectWriter::py_bind(ObjectWriter* self, PyObject* obj) {
        try {
            PyTypeObject* wrapped = wrapped_type();
            if (!wrapped) return nullptr;

            bool is_wrapped = PyObject_TypeCheck(obj, wrapped);
            bool is_patched = is_retrace_patched_type(Py_TYPE(obj));

            if (is_wrapped && !is_patched) {
                self->ensure_wrapped_tracking(obj);
            }

            self->bind(obj);
            Py_RETURN_NONE;
        } catch (...) {
            return nullptr;
        }
    }

    PyObject* ObjectWriter::py_new_patched(ObjectWriter* self, PyObject* obj) {
        return create_new_patched_deleter(self, obj);
    }

    PyObject* ObjectWriter::py_ext_bind(ObjectWriter* self, PyObject* obj) {
        return py_bind(self, obj);
    }

    static PyObject * create_ref_deleter(ObjectWriter * self, PyObject * obj) {
        auto* d = reinterpret_cast<Deleter*>(
            Deleter_Type.tp_alloc(&Deleter_Type, 0));
        if (!d) return nullptr;

        d->writer = Py_NewRef((PyObject*)self);
        d->addr = nullptr;
        d->ref = Py_TYPE(obj) == &StreamHandle_Type
            ? reinterpret_cast<StreamHandle*>(obj)->handle
            : reinterpret_cast<Ref>(obj);
        d->delete_ref_only = true;
        d->vectorcall = (vectorcallfunc)Deleter::call;
        return (PyObject*)d;
    }

    PyObject* ObjectWriter::py_deleter(ObjectWriter* self, PyObject* obj) {
        return create_ref_deleter(self, obj);
    }

    static map<PyTypeObject *, freefunc> freefuncs;

    void on_free(void * obj) {
        for (ObjectWriter * writer : writers) {
            writer->object_freed((PyObject *)obj);
        }
    }

    void generic_free(void * obj) {
        auto it = freefuncs.find(Py_TYPE(obj));
        if (it != freefuncs.end()) {
            on_free(obj);
            it->second(obj);
        } else {
            // bad situation, a memory leak! Maybe print a bad warning
        }
    }

    void PyObject_GC_Del_Wrapper(void * obj) {
        on_free(obj);
        PyObject_GC_Del(obj);
    }

    void PyObject_Free_Wrapper(void * obj) {
        on_free(obj);
        PyObject_Free(obj);
    }

    bool is_patched(freefunc func) {
        return func == generic_free ||
               func == PyObject_GC_Del_Wrapper ||
               func == PyObject_Free_Wrapper;
    }

    void patch_free(PyTypeObject * cls) {
        assert(!is_patched(cls->tp_free));
        if (cls->tp_free == PyObject_Free) {
            cls->tp_free = PyObject_Free_Wrapper;
        } else if (cls->tp_free == PyObject_GC_Del) {
            cls->tp_free = PyObject_GC_Del_Wrapper;
        } else {
            freefuncs[cls] = cls->tp_free;
            cls->tp_free = generic_free;
        }
    }

    PyGetSetDef StreamHandle_getset[] = {
        {"index", (getter)StreamHandle::get_index, nullptr, "Wire-format handle index", nullptr},
        {NULL}
    };

    uint64_t StreamHandle_index(PyObject * streamhandle) {
        return index_of_handle(reinterpret_cast<StreamHandle *>(streamhandle)->handle);
    }

    // --- StreamHandle type ---

    PyTypeObject StreamHandle_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "StreamHandle",
        .tp_basicsize = sizeof(StreamHandle),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)ObjectWriter::StreamHandle_dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(StreamHandle, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)StreamHandle::traverse,
        .tp_clear = (inquiry)StreamHandle::clear,
        .tp_getset = StreamHandle_getset,
    };

    // --- ObjectWriter type ---

    static PyMethodDef methods[] = {
        {"handle", (PyCFunction)ObjectWriter::py_handle, METH_O, "Creates handle"},
        {"deleter", (PyCFunction)ObjectWriter::py_deleter, METH_O, "Returns a callable that deletes a ref"},
        {"flush", (PyCFunction)ObjectWriter::py_flush, METH_NOARGS, "Flush buffered data to the output callback"},
        {"disable", (PyCFunction)ObjectWriter::py_disable, METH_NOARGS, "Null queue pointers to prevent further writes"},
        {"heartbeat", (PyCFunction)ObjectWriter::py_heartbeat, METH_O, "Push heartbeat payload dict and flush"},
        {"bind", (PyCFunction)ObjectWriter::py_bind, METH_O, "TODO"},
        {"new_patched", (PyCFunction)ObjectWriter::py_new_patched, METH_O, "TODO"},
        {"ext_bind", (PyCFunction)ObjectWriter::py_ext_bind, METH_O, "TODO"},
        {NULL}
    };

    static PyMemberDef members[] = {
        {"messages_written", T_ULONGLONG, OFFSET_OF_MEMBER(ObjectWriter, messages_written), READONLY, "TODO"},
        {"verbose", T_BOOL, OFFSET_OF_MEMBER(ObjectWriter, verbose), 0, "TODO"},
        {"buffer_writes", T_BOOL, OFFSET_OF_MEMBER(ObjectWriter, buffer_writes), 0, "When false, flush after every write"},
        {"normalize_path", T_OBJECT, OFFSET_OF_MEMBER(ObjectWriter, normalize_path), 0, "TODO"},
        {"enable_when", T_OBJECT, OFFSET_OF_MEMBER(ObjectWriter, enable_when), 0, "TODO"},
        {NULL}
    };

    static PyGetSetDef getset[] = {
        {"bytes_written", (getter)ObjectWriter::bytes_written_getter, nullptr, "TODO", NULL},
        {"path", (getter)ObjectWriter::path_getter, (setter)ObjectWriter::path_setter, "TODO", NULL},
        {"output", (getter)ObjectWriter::output_getter, (setter)ObjectWriter::output_setter, "Output callback", NULL},
        {"inflight_limit", (getter)ObjectWriter::inflight_limit_getter, (setter)ObjectWriter::inflight_limit_setter,
         "Maximum bytes in-flight between writer and persister", NULL},
        {"inflight_bytes", (getter)ObjectWriter::inflight_bytes_getter, nullptr,
         "Current estimated bytes in-flight", NULL},
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
        .tp_new = PyType_GenericNew,
    };
}
