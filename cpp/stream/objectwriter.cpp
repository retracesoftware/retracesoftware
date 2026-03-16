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

    static PyTypeObject* utils_type(const char* name) {
        PyObject* mod = PyImport_ImportModule("retracesoftware.utils");
        if (!mod) {
            PyErr_Clear();
            return nullptr;
        }

        PyObject* obj = PyObject_GetAttrString(mod, name);
        Py_DECREF(mod);
        if (!obj) {
            PyErr_Clear();
            return nullptr;
        }
        if (!PyType_Check(obj)) {
            Py_DECREF(obj);
            PyErr_Format(PyExc_TypeError, "retracesoftware.utils.%s is not a type", name);
            return nullptr;
        }
        return reinterpret_cast<PyTypeObject*>(obj);
    }

    static PyTypeObject* wrapped_type() {
        static PyTypeObject* wrapped = nullptr;
        if (!wrapped) wrapped = utils_type("Wrapped");
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

    struct ObjectWriter : public PyObject {

        PyObject* queue = nullptr;
        map<PyObject*, PyObject*> bound;

        size_t messages_written = 0;
        uintptr_t next_handle;
        int pid;
        bool verbose;
        bool quit_on_error;
        bool serialize_errors = true;
        PyObject * serializer = nullptr;
        PyObject* thread = nullptr;
        vectorcallfunc vectorcall = nullptr;
        PyObject *weakreflist = nullptr;

        ObjectWriter() {}

        inline bool is_disabled() const { return queue == nullptr; }

        inline bool has_native_queue() const {
            return queue && Py_TYPE(queue) == &Queue_Type;
        }

        inline Queue* native_queue() const {
            return reinterpret_cast<Queue*>(queue);
        }

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

        bool call_queue0(const char* method) {
            return queue_result_to_bool(PyObject_CallMethod(queue, const_cast<char*>(method), nullptr));
        }

        bool call_queue_obj(const char* method, PyObject* arg) {
            return queue_result_to_bool(PyObject_CallMethod(queue, const_cast<char*>(method), const_cast<char*>("O"), arg));
        }

        bool call_queue_obj_obj(const char* method, PyObject* a, PyObject* b) {
            return queue_result_to_bool(PyObject_CallMethod(queue, const_cast<char*>(method), const_cast<char*>("OO"), a, b));
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

        bool push_shutdown() {
            if (has_native_queue()) return native_queue()->push_shutdown();
            return call_queue0("push_shutdown");
        }

        bool push_flush() {
            if (has_native_queue()) return native_queue()->push_flush();
            return call_queue0("push_flush");
        }

        bool push_heartbeat() {
            if (has_native_queue()) return native_queue()->push_heartbeat();
            return call_queue0("push_heartbeat");
        }

        bool push_bind(PyObject* obj) {
            if (has_native_queue()) return native_queue()->push_bind(obj);
            return call_queue_ptr("push_bind", obj);
        }

        bool push_new_patched(PyObject* obj, PyTypeObject* type) {
            if (has_native_queue()) return native_queue()->push_new_patched(obj, type);
            return call_queue_obj_obj("push_new_patched", obj, reinterpret_cast<PyObject*>(type));
        }

        bool push_handle_delete(Ref handle) {
            if (has_native_queue()) return native_queue()->push_handle_delete(handle);
            return call_queue_ptr("push_handle_delete", handle);
        }

        bool push_delete(Ref ref) {
            if (has_native_queue()) return native_queue()->push_delete(ref);
            return call_queue_ptr("push_delete", ref);
        }

        bool push_new_handle(Ref handle, PyObject* obj) {
            if (has_native_queue()) return native_queue()->push_new_handle(handle, obj);
            return call_queue_ptr_obj("push_new_handle", handle, obj);
        }

        bool push_handle_ref(Ref ref) {
            if (has_native_queue()) return native_queue()->push_handle_ref(ref);
            return call_queue_ptr("push_handle_ref", ref);
        }

        bool push_obj_to_queue(PyObject* obj) {
            if (has_native_queue()) return native_queue()->push_obj(obj);
            return call_queue_obj("push_obj", obj);
        }

        bool push_immortal(PyObject* obj) {
            if (has_native_queue()) return native_queue()->push_immortal(obj);
            return call_queue_obj("push_immortal", obj);
        }

        bool push_bound_ref(Ref ref) {
            if (has_native_queue()) return native_queue()->push_bound_ref(ref);
            return call_queue_ptr("push_bound_ref", ref);
        }

        bool push_bound_ref_delete(Ref ref) {
            if (has_native_queue()) return native_queue()->push_bound_ref_delete(ref);
            return call_queue_ptr("push_bound_ref_delete", ref);
        }

        bool push_ext_wrapped(PyTypeObject* type) {
            if (has_native_queue()) return native_queue()->push_ext_wrapped(type);
            return call_queue_obj("push_ext_wrapped", reinterpret_cast<PyObject*>(type));
        }

        bool push_pickled(PyObject* obj) {
            if (has_native_queue()) return native_queue()->push_pickled(obj);
            return call_queue_obj("push_pickled", obj);
        }

        bool push_serialize_error() {
            if (has_native_queue()) return native_queue()->push_serialize_error();
            return call_queue0("push_serialize_error");
        }

        bool push_thread(PyThreadState* tstate) {
            if (has_native_queue()) return native_queue()->push_thread(tstate);
            return call_queue_ptr("push_thread", tstate);
        }

        bool push_list_header(size_t len) {
            if (has_native_queue()) return native_queue()->push_list_header(len);
            return call_queue_size("push_list_header", len);
        }

        bool push_tuple_header(size_t len) {
            if (has_native_queue()) return native_queue()->push_tuple_header(len);
            return call_queue_size("push_tuple_header", len);
        }

        bool push_dict_header(size_t len) {
            if (has_native_queue()) return native_queue()->push_dict_header(len);
            return call_queue_size("push_dict_header", len);
        }

        void disable_push_fail() {
            fprintf(stderr, "retrace: writer queue stalled, disabling recording\n");
            clear_queue_ref();
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

            ensure_bound_tracking(obj);

            trace_bind_event("producer-bind-enter", obj, (long)messages_written);
            send_thread();

            if (verbose) {
                debug_prefix();
                printf("BIND(%s)\n", Py_TYPE(obj)->tp_name);
            }

            disable_if_push_failed(push_bind(obj));
            messages_written++;
            trace_bind_event("producer-bind-enqueued", obj, (long)messages_written);
        }

        void new_patched(PyObject * obj) {
            if (is_disabled()) return;

            PyTypeObject* type = Py_TYPE(obj);

            ensure_bound_tracking(obj);

            send_thread();

            if (verbose) {
                debug_prefix();
                printf("NEW_PATCHED(%s)\n", Py_TYPE(obj)->tp_name);
            }

            disable_if_push_failed(push_new_patched(obj, type));
            messages_written++;
        }

        void write_delete(Ref handle) {
            if (is_disabled()) return;

            if (verbose) {
                debug_prefix();
                printf("DELETE(%p)\n", handle);
            }
            disable_if_push_failed(push_handle_delete(handle));
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

            if (!push_new_handle(handle, obj)) {
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

            disable_if_push_failed(push_handle_ref(obj->handle));
            messages_written++;
        }

        static constexpr int MAX_FLATTEN_DEPTH = 32;

        void push_obj(PyObject* obj) {
            if (!push_obj_to_queue(obj)) {
                disable_push_fail();
                return;
            }
        }

        void ensure_bound_tracking(PyObject* obj) {
            if (bound.contains(obj)) return;

            auto* callback = reinterpret_cast<WeakRefCallback*>(
                WeakRefCallback_Type.tp_alloc(&WeakRefCallback_Type, 0));
            if (!callback) throw nullptr;

            callback->handle = obj;
            callback->writer = Py_NewRef((PyObject*)this);
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

            bound[obj] = weakref;
        }

        Ref ensure_auto_handle(PyObject* obj) {
            return reinterpret_cast<Ref>(obj);
        }

        void push_value(PyObject* obj, int depth = 0) {

            if (is_immortal(obj)) {
                disable_if_push_failed(push_immortal(obj));
            } else {
                PyTypeObject* tp = Py_TYPE(obj);
                PyTypeObject* wrapped = wrapped_type();

                if (tp == &PyLong_Type) {
                    push_obj(obj);
                } else if (tp == &PyUnicode_Type) {
                    push_obj(obj);
                } else if (tp == &PyBytes_Type) {
                    push_obj(obj);
                } else if (tp == &StreamHandle_Type) {
                    disable_if_push_failed(push_handle_ref(reinterpret_cast<StreamHandle*>(obj)->handle));
                } else if (is_retrace_patched_type(tp)) {
                    disable_if_push_failed(push_bound_ref(ensure_auto_handle(obj)));
                } else if (tp == &PyList_Type) {
                    assert (depth < MAX_FLATTEN_DEPTH);
                    Py_ssize_t n = PyList_GET_SIZE(obj);
                    disable_if_push_failed(push_list_header((size_t)n));
                    for (Py_ssize_t i = 0; i < n; i++)
                        push_value(PyList_GET_ITEM(obj, i), depth + 1);

                } else if (tp == &PyTuple_Type) {
                    assert (depth < MAX_FLATTEN_DEPTH);
                    Py_ssize_t n = PyTuple_GET_SIZE(obj);
                    disable_if_push_failed(push_tuple_header((size_t)n));
                    for (Py_ssize_t i = 0; i < n; i++)
                        push_value(PyTuple_GET_ITEM(obj, i), depth + 1);
                } else if (tp == &PyDict_Type) {
                    assert (depth < MAX_FLATTEN_DEPTH);
                    Py_ssize_t n = PyDict_Size(obj);
                    disable_if_push_failed(push_dict_header((size_t)n));
                    Py_ssize_t pos = 0;
                    PyObject *key, *value;
                    while (PyDict_Next(obj, &pos, &key, &value)) {
                        push_value(key, depth + 1);
                        push_value(value, depth + 1);
                    }
                } else if (tp == &PyFloat_Type) {
                    push_obj(obj);
                } else if (tp == &PyMemoryView_Type) {
                    push_obj(obj);
                } else if (wrapped && PyObject_TypeCheck(obj, wrapped) &&
                           bound.contains(obj)) {
                    disable_if_push_failed(push_bound_ref(ensure_auto_handle(obj)));
                } else if (wrapped && PyObject_TypeCheck(obj, wrapped)) {
                    disable_if_push_failed(push_ext_wrapped(Py_TYPE(obj)));
                } else {
                    // Try the full serializer (type_serializer + pickle fallback)
                    PyObject* res = PyObject_CallOneArg(serializer, obj);
                    if (res) {
                        if (PyBytes_Check(res)) {
                            // Serializer returned pickled bytes
                            bool ok = push_pickled(res);
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

                            disable_if_push_failed(push_serialize_error());
                            push_value(error_dict, depth + 1);
                            Py_DECREF(error_dict);
                        } else {
                            PyErr_Clear();
                            disable_if_push_failed(push_obj_to_queue(Py_None));
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
                (wrapped && PyObject_TypeCheck(obj, wrapped) && bound.contains(obj));

            auto it = bound.find(obj);
            if (it != bound.end()) {
                Py_DECREF(it->second);
                bound.erase(it);
            }
            if (is_auto_ref) {
                disable_if_push_failed(push_bound_ref_delete(reinterpret_cast<Ref>(obj)));
            }
            disable_if_push_failed(push_delete(obj));
        }

        void write_all(StreamHandle * self, PyObject *const * args, size_t nargs) {
            if (!is_disabled()) {
                send_thread();
                write_root(self);
                for (size_t i = 0; i < nargs; i++) {
                    write_root(args[i]);
                }
            }
        }

        void write_all(PyObject*const * args, size_t nargs) {

            if (!is_disabled()) {
                send_thread();

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
                self->disable_if_push_failed(self->push_flush());
                Py_RETURN_NONE;
            } catch (...) {
                return nullptr;
            }
        }

        static PyObject* py_disable(ObjectWriter* self, PyObject* unused) {
            self->clear_queue_ref();
            Py_RETURN_NONE;
        }

        static PyObject* py_heartbeat(ObjectWriter* self, PyObject* payload) {
            if (self->is_disabled()) Py_RETURN_NONE;
            if (!PyDict_Check(payload)) {
                PyErr_SetString(PyExc_TypeError, "heartbeat payload must be a dict");
                return nullptr;
            }
            try {
                self->disable_if_push_failed(self->push_heartbeat());
                self->push_value(payload);
                self->disable_if_push_failed(self->push_flush());
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

        static PyObject * py_bind(ObjectWriter * self, PyObject* obj);
        static PyObject * py_new_patched(ObjectWriter * self, PyObject* obj);

        static PyObject* py_new(PyTypeObject* type, PyObject*, PyObject*) {
            ObjectWriter* self = reinterpret_cast<ObjectWriter*>(type->tp_alloc(type, 0));
            if (!self) return nullptr;
            new (self) ObjectWriter();
            return reinterpret_cast<PyObject*>(self);
        }

        static int init(ObjectWriter * self, PyObject* args, PyObject* kwds) {

            PyObject * queue_obj;
            
            PyObject * thread = nullptr;
            PyObject * serializer = nullptr;
            int verbose = 0;
            int quit_on_error = 0;
            int serialize_errors = 1;

            static const char* kwlist[] = {
                "queue",
                "serializer",
                "thread",
                "verbose",
                "quit_on_error",
                "serialize_errors",
                nullptr};

            if (!PyArg_ParseTupleAndKeywords(args, kwds, "OO|Opp", (char **)kwlist,
                &queue_obj, &serializer, &thread, &verbose,
                &quit_on_error, &serialize_errors)) {
                return -1;
            }

            self->verbose = verbose;
            self->quit_on_error = quit_on_error;
            self->serialize_errors = serialize_errors;
            self->thread = thread && thread != Py_None ? Py_NewRef(thread) : nullptr;

            self->messages_written = 0;
            self->next_handle = 0;
            
            self->vectorcall = reinterpret_cast<vectorcallfunc>(ObjectWriter::py_vectorcall);

            self->serializer = Py_NewRef(serializer);
            self->queue = queue_obj == Py_None ? nullptr : Py_NewRef(queue_obj);

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
            disable_if_push_failed(push_thread(PyThreadState_Get()));
        }

        static int traverse(ObjectWriter* self, visitproc visit, void* arg) {
            Py_VISIT(self->queue);
            Py_VISIT(self->serializer);
            Py_VISIT(self->thread);
            for (auto& [_, weakref] : self->bound) {
                Py_VISIT(weakref);
            }
            return 0;
        }

        static int clear(ObjectWriter* self) {
            self->clear_queue_ref();
            Py_CLEAR(self->serializer);
            Py_CLEAR(self->thread);
            for (auto& [_, weakref] : self->bound) {
                Py_CLEAR(weakref);
            }
            self->bound.clear();
            return 0;
        }

        static void dealloc(ObjectWriter* self) {
            if (self->queue) {
                try {
                    (void)self->push_shutdown();
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

            auto it = std::find(writers.begin(), writers.end(), self);
            if (it != writers.end()) {
                writers.erase(it);
            }
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
            Py_SETREF(self->queue, Py_NewRef(value));
            return 0;
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
            w->object_freed(self->handle);
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

    PyObject* ObjectWriter::py_bind(ObjectWriter* self, PyObject* obj) {
        try {
            self->bind(obj);
            Py_RETURN_NONE;
        } catch (...) {
            return nullptr;
        }
    }

    PyObject* ObjectWriter::py_new_patched(ObjectWriter* self, PyObject* obj) {
        try {
            self->new_patched(obj);
            Py_RETURN_NONE;
        } catch (...) {
            return nullptr;
        }
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
        {"flush", (PyCFunction)ObjectWriter::py_flush, METH_NOARGS, "Flush buffered data to the output callback"},
        {"disable", (PyCFunction)ObjectWriter::py_disable, METH_NOARGS, "Null queue pointers to prevent further writes"},
        {"heartbeat", (PyCFunction)ObjectWriter::py_heartbeat, METH_O, "Push heartbeat payload dict and flush"},
        {"bind", (PyCFunction)ObjectWriter::py_bind, METH_O, "TODO"},
        {"new_patched", (PyCFunction)ObjectWriter::py_new_patched, METH_O, "TODO"},
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
