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
    class Persister;

    static std::vector<ObjectWriter *> writers;
    
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

    struct ObjectWriter : public PyObject {

        Queue * queue = nullptr;
        map<PyObject*, PyObject*> bound;

        size_t messages_written = 0;
        int pid;
        bool verbose;
        bool quit_on_error;
        PyTypeObject * ext_wrapped_type = nullptr;
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

        void bind(PyObject * obj) {
            if (is_disabled()) return;

            ensure_bound_tracking(obj);

            // trace_bind_event("producer-bind-enter", obj, (long)messages_written);

            if (verbose) {
                debug_prefix();
                printf("BIND(%s)\n", Py_TYPE(obj)->tp_name);
            }

            if (!disable_if_push_failed(queue->push_bind(binding_ref(obj)))) return;
            messages_written++;
            // trace_bind_event("producer-bind-enqueued", obj, (long)messages_written);
        }

        void intern(PyObject* obj) {
            if (is_disabled()) return;

            if (is_bound(obj)) {
                write_root(obj);
                return;
            }

            ensure_bound_tracking(obj);

            if (verbose) {
                debug_prefix();
                printf("INTERN(%s)\n", Py_TYPE(obj)->tp_name);
            }

            if (!disable_if_push_failed(queue->push_intern(obj, binding_ref(obj)))) return;
            messages_written++;
        }

        void new_patched(PyObject * obj) {
            if (is_disabled()) return;

            ensure_bound_tracking(obj);
            PyObject* type = reinterpret_cast<PyObject*>(Py_TYPE(obj));
            if (!is_bound(type)) {
                intern(type);
                if (is_disabled()) return;
            }
            assert(is_bound(type));

            if (verbose) {
                debug_prefix();
                printf("NEW_PATCHED(%s)\n", Py_TYPE(obj)->tp_name);
            }

            if (!disable_if_push_failed(queue->push_new_patched(binding_ref(type), binding_ref(obj)))) return;
            messages_written++;
        }

        static constexpr int MAX_FLATTEN_DEPTH = 32;
        
        bool is_bound(PyObject* obj) {
            return bound.contains(obj);
        }

        PyObject* binding_token(PyObject* obj) {
            auto it = bound.find(obj);
            if (it == bound.end()) return nullptr;
            return it->second ? it->second : it->first;
        }

        Ref binding_ref(PyObject* obj) {
            PyObject* token = binding_token(obj);
            assert(token);
            return reinterpret_cast<Ref>(token);
        }

        void ensure_bound_tracking(PyObject* obj) {
            if (!is_bound(obj)) {
                if (Py_TYPE(obj)->tp_weaklistoffset) {
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
                } else {
                    bound[Py_NewRef(obj)] = nullptr;
                }
            }
        }

        void push_value(PyObject* obj, int depth = 0) {
            if (is_disabled()) return;

            if (is_bound(obj)) {
                disable_if_push_failed(queue->push_ref(binding_ref(obj)));
            }
            else if (is_immortal(obj)) {
                disable_if_push_failed(queue->push_obj(obj));
            }
            else if (Py_TYPE(obj)->tp_base == ext_wrapped_type && is_bound(reinterpret_cast<PyObject*>(Py_TYPE(obj))))
            {
                disable_if_push_failed(queue->push_obj(reinterpret_cast<PyObject*>(Py_TYPE(obj))));
            } 
            else {
                PyTypeObject* tp = Py_TYPE(obj);
                
                if (PyMemoryView_Check(obj)) {
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
                else if (tp == &PyUnicode_Type && is_interned_unicode(obj)) {
                    intern(obj);
                    disable_if_push_failed(queue->push_ref(binding_ref(obj)));
                }
                else if (tp == &PyList_Type) {
                    assert (depth < MAX_FLATTEN_DEPTH);
                    Py_ssize_t n = PyList_GET_SIZE(obj);
                    if (!disable_if_push_failed(queue->push_list_header((size_t)n))) return;
                    for (Py_ssize_t i = 0; i < n; i++)
                        push_value(PyList_GET_ITEM(obj, i), depth + 1);
                } else if (tp == &PyTuple_Type) {
                    assert (depth < MAX_FLATTEN_DEPTH);
                    Py_ssize_t n = PyTuple_GET_SIZE(obj);
                    if (!disable_if_push_failed(queue->push_tuple_header((size_t)n))) return;
                    for (Py_ssize_t i = 0; i < n; i++)
                        push_value(PyTuple_GET_ITEM(obj, i), depth + 1);
                } else if (tp == &PyDict_Type) {
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
            // if (is_immortal(obj)) {
            //     disable_if_push_failed(push_immortal(obj));
            // } else {
            //     PyTypeObject* tp = Py_TYPE(obj);
            //     PyTypeObject* wrapped = wrapped_type();

            //     if (tp == &PyLong_Type) {
            //         push_obj(obj);
            //     } else if (tp == &PyUnicode_Type) {
            //         push_obj(obj);
            //     } else if (tp == &PyBytes_Type) {
            //         push_obj(obj);
            //     } else if (is_retrace_patched_type(tp)) {
            //         disable_if_push_failed(push_bound_ref(ensure_auto_handle(obj)));
            //     } else if (tp == &PyList_Type) {
            //         assert (depth < MAX_FLATTEN_DEPTH);
            //         Py_ssize_t n = PyList_GET_SIZE(obj);
            //         disable_if_push_failed(push_list_header((size_t)n));
            //         for (Py_ssize_t i = 0; i < n; i++)
            //             push_value(PyList_GET_ITEM(obj, i), depth + 1);

            //     } else if (tp == &PyTuple_Type) {
            //         assert (depth < MAX_FLATTEN_DEPTH);
            //         Py_ssize_t n = PyTuple_GET_SIZE(obj);
            //         disable_if_push_failed(push_tuple_header((size_t)n));
            //         for (Py_ssize_t i = 0; i < n; i++)
            //             push_value(PyTuple_GET_ITEM(obj, i), depth + 1);
            //     } else if (tp == &PyDict_Type) {
            //         assert (depth < MAX_FLATTEN_DEPTH);
            //         Py_ssize_t n = PyDict_Size(obj);
            //         disable_if_push_failed(push_dict_header((size_t)n));
            //         Py_ssize_t pos = 0;
            //         PyObject *key, *value;
            //         while (PyDict_Next(obj, &pos, &key, &value)) {
            //             push_value(key, depth + 1);
            //             push_value(value, depth + 1);
            //         }
            //     } else if (tp == &PyFloat_Type) {
            //         push_obj(obj);
            //     } else if (tp == &PyMemoryView_Type) {
            //         push_obj(obj);
            //     } else if (wrapped && PyObject_TypeCheck(obj, wrapped) &&
            //                bound.contains(obj)) {
            //         disable_if_push_failed(push_bound_ref(ensure_auto_handle(obj)));
            //     } else if (wrapped && PyObject_TypeCheck(obj, wrapped)) {
            //         disable_if_push_failed(push_obj(Py_TYPE(obj)));
            //     } else {
            //         // Try the full serializer (type_serializer + pickle fallback)
            //         PyObject* res = PyObject_CallOneArg(serializer, obj);
            //         if (res) {
            //             if (PyBytes_Check(res)) {
            //                 // Serializer returned pickled bytes
            //                 bool ok = push_pickled(res);
            //                 Py_DECREF(res);
            //                 if (!ok) {
            //                     disable_push_fail();
            //                     return;
            //                 }
            //             } else {
            //                 // Serializer returned a converted object (e.g. Stack → tuple)
            //                 push_value(res, depth + 1);
            //                 Py_DECREF(res);
            //             }
            //         } else {
            //             // Serialization failed — push SERIALIZE_ERROR tag + error info dict
            //             PyObject *ptype, *pvalue, *ptb;
            //             PyErr_Fetch(&ptype, &pvalue, &ptb);

            //             PyObject* error_dict = PyDict_New();
            //             if (error_dict) {
            //                 PyObject* obj_type_str = PyUnicode_FromString(Py_TYPE(obj)->tp_name);
            //                 if (obj_type_str) { PyDict_SetItemString(error_dict, "object_type", obj_type_str); Py_DECREF(obj_type_str); }

            //                 if (ptype) {
            //                     PyObject* name = PyObject_GetAttrString(ptype, "__name__");
            //                     if (name) { PyDict_SetItemString(error_dict, "error_type", name); Py_DECREF(name); }
            //                     else PyErr_Clear();
            //                 }
            //                 if (pvalue) {
            //                     PyObject* msg = PyObject_Str(pvalue);
            //                     if (msg) { PyDict_SetItemString(error_dict, "error", msg); Py_DECREF(msg); }
            //                     else PyErr_Clear();
            //                 }

            //                 disable_if_push_failed(push_serialize_error());
            //                 push_value(error_dict, depth + 1);
            //                 Py_DECREF(error_dict);
            //             } else {
            //                 PyErr_Clear();
            //                 disable_if_push_failed(push_obj_to_queue(Py_None));
            //             }

            //             if (!serialize_errors) {
            //                 PyErr_Restore(ptype, pvalue, ptb);
            //                 throw nullptr;
            //             } else {
            //                 Py_XDECREF(ptype);
            //                 Py_XDECREF(pvalue);
            //                 Py_XDECREF(ptb);
            //             }
            //         }
            //     }
        //     }
        // }

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

            auto it = bound.find(obj);
            if (it != bound.end()) {
                PyObject* token = it->second ? it->second : it->first;
                disable_if_push_failed(queue->push_delete(reinterpret_cast<Ref>(token)));
                if (it->second) {
                    Py_DECREF(it->second);
                } else {
                    Py_DECREF(it->first);
                }
                bound.erase(it);
            }
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

        static PyObject * py_bind(ObjectWriter * self, PyObject* obj);
        static PyObject * py_intern(ObjectWriter * self, PyObject* obj);
        static PyObject * py_new_patched(ObjectWriter * self, PyObject* obj);

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
            PyTypeObject * ext_wrapped_type;

            static const char* kwlist[] = {
                "queue",
                "ext_wrapped_type",
                "verbose",
                "quit_on_error",
                nullptr};

            if (!PyArg_ParseTupleAndKeywords(args, kwds, "O!O!|pp", (char **)kwlist,
                &Queue_Type, &queue_obj, &PyType_Type, &ext_wrapped_type, &verbose, &quit_on_error)) {
                return -1;
            }

            self->verbose = verbose;
            self->quit_on_error = quit_on_error;

            Py_INCREF(ext_wrapped_type);
            self->ext_wrapped_type = ext_wrapped_type;
            
            self->messages_written = 0;
            
            self->vectorcall = reinterpret_cast<vectorcallfunc>(ObjectWriter::py_vectorcall);

            self->queue = reinterpret_cast<Queue*>(Py_NewRef(queue_obj));

            writers.push_back(self);

            return 0;
        }

        static int traverse(ObjectWriter* self, visitproc visit, void* arg) {
            Py_VISIT(self->queue);
            Py_VISIT(self->ext_wrapped_type);
            for (auto& [obj, weakref] : self->bound) {
                if (weakref)
                    Py_VISIT(weakref);
                else
                    Py_VISIT(obj);
            }
            return 0;
        }

        static int clear(ObjectWriter* self) {
            self->clear_queue_ref();
            Py_CLEAR(self->ext_wrapped_type);
            for (auto& [obj, weakref] : self->bound) {
                if (weakref)
                    Py_CLEAR(weakref);
                else
                    Py_DECREF(obj);
            }
            self->bound.clear();
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
            if (!PyObject_TypeCheck(value, &Queue_Type)) {
                PyErr_SetString(PyExc_TypeError, "output must be a Queue or None");
                return -1;
            }
            self->clear_queue_ref();
            self->queue = reinterpret_cast<Queue*>(Py_NewRef(value));
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

    PyObject* ObjectWriter::py_intern(ObjectWriter* self, PyObject* obj) {
        try {
            self->intern(obj);
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
        {"bind", (PyCFunction)ObjectWriter::py_bind, METH_O, "TODO"},
        {"intern", (PyCFunction)ObjectWriter::py_intern, METH_O, "TODO"},
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
