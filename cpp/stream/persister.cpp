#include "stream.h"
#include "writer.h"
#include "framed_writer.h"
#include "queueentry.h"
#include <structmember.h>
#include <thread>
#include <atomic>
#include <cerrno>
#include <cstring>
#include <string>
#include <unordered_map>

#ifndef _WIN32
    #include <unistd.h>
    #include <fcntl.h>
    #include <sys/file.h>
    #include <sys/stat.h>
    #include <sys/socket.h>
    #include <sys/un.h>
    #include <limits.h>
#endif

namespace retracesoftware_stream {

    // When quit_on_error is true, print the Python exception and
    // terminate instead of silently clearing it.  This turns silent
    // data-corruption into a visible crash during recording.
    static void handle_write_error(bool quit_on_error) {
        if (quit_on_error) {
            fprintf(stderr, "retrace: serialization error (quit_on_error is set)\n");
            PyErr_Print();
            _exit(1);
        }
        PyErr_Clear();
    }

    static void handle_debug_error(bool quit_on_error) {
        if (quit_on_error) {
            fprintf(stderr, "retrace: debug persister callback error (quit_on_error is set)\n");
            PyErr_Print();
            _exit(1);
        }
        PyErr_Print();
        PyErr_Clear();
    }

    static const char* command_name(uint32_t cmd) {
        switch (cmd) {
            case CMD_FLUSH: return "flush";
            case CMD_SHUTDOWN: return "shutdown";
            case CMD_LIST: return "list";
            case CMD_TUPLE: return "tuple";
            case CMD_DICT: return "dict";
            case CMD_HEARTBEAT: return "heartbeat";
            case CMD_EXTERNAL_WRAPPED: return "external_wrapped";
            case CMD_DELETE: return "delete";
            case CMD_THREAD: return "thread";
            case CMD_PICKLED: return "pickled";
            case CMD_NEW_HANDLE: return "new_handle";
            case CMD_NEW_PATCHED: return "new_patched";
            case CMD_BIND: return "bind";
            case CMD_SERIALIZE_ERROR: return "serialize_error";
            default: return "unknown";
        }
    }

    static PyObject* ref_as_int(Ref ref) {
        return PyLong_FromUnsignedLongLong((unsigned long long)(uintptr_t)ref);
    }

    static PyObject* ptr_as_int(void* ptr) {
        return PyLong_FromUnsignedLongLong((unsigned long long)(uintptr_t)ptr);
    }

    static PyObject* make_event_tuple(const char* tag, PyObject* payload) {
        PyObject* tuple = PyTuple_New(2);
        if (!tuple) {
            Py_XDECREF(payload);
            return nullptr;
        }

        PyObject* tag_obj = PyUnicode_FromString(tag);
        if (!tag_obj) {
            Py_DECREF(tuple);
            Py_XDECREF(payload);
            return nullptr;
        }

        PyTuple_SET_ITEM(tuple, 0, tag_obj);
        PyTuple_SET_ITEM(tuple, 1, payload ? payload : Py_NewRef(Py_None));
        return tuple;
    }

    static PyObject* make_object_event(PyObject* obj) {
        return make_event_tuple("object", Py_NewRef(obj));
    }

    static PyObject* make_command_event(const char* name, PyObject* args) {
        PyObject* payload = PyTuple_New(2);
        if (!payload) {
            Py_XDECREF(args);
            return nullptr;
        }

        PyObject* name_obj = PyUnicode_FromString(name);
        if (!name_obj) {
            Py_DECREF(payload);
            Py_XDECREF(args);
            return nullptr;
        }

        PyTuple_SET_ITEM(payload, 0, name_obj);
        PyTuple_SET_ITEM(payload, 1, args ? args : PyTuple_New(0));
        return make_event_tuple("command", payload);
    }

    static PyObject* dispatch_debug_handler(PyObject* handler, PyObject* event) {
        if (PyCallable_Check(handler)) {
            return PyObject_CallOneArg(handler, event);
        }

        PyObject* method = PyObject_GetAttrString(handler, "handle_event");
        if (!method) return nullptr;

        PyObject* result = PyObject_CallOneArg(method, event);
        Py_DECREF(method);
        return result;
    }

    // ── AsyncFilePersister ───────────────────────────────────────
    //
    // Owns the SPSC queue consumer side, a MessageStream for
    // serialization, and a background thread that processes entries.
    // ObjectWriter pushes tagged QEntry values; the persister
    // thread deserializes objects and writes PID-framed output
    // via the FramedWriter received on construction.

    struct AsyncFilePersister : PyObject {
        PyObject* framed_writer_obj;  // strong ref to PyFramedWriter
        FramedWriter* fw;             // borrowed pointer into framed_writer_obj
        std::thread writer_thread;
        std::thread return_thread;
        std::atomic<bool> shutdown_flag;
        std::atomic<bool> return_shutdown;
        bool closed;
        bool thread_started;
        bool quit_on_error;

        Queue* queue;
        MessageStream* stream;
        PyObject* writer_key;
        PyThreadState* last_tstate;
        std::unordered_map<PyThreadState*, PyObject*>* thread_cache;
        std::unordered_map<Ref, uint64_t>* ref_indices;
        uint64_t next_handle_index = 0;

        std::atomic<uint64_t> processed_cursor{0};

        struct WriterConsumer : QueueConsumer {
            AsyncFilePersister& self;
            bool saw_shutdown = false;

            explicit WriterConsumer(AsyncFilePersister& persister) : self(persister) {}

            void consume_object(PyObject* obj) override {
                try { self.stream->write(obj); } catch (...) { handle_write_error(self.quit_on_error); }
            }

            void consume_handle_ref(Ref ref) override {
                try { self.stream->write_handle_ref_by_index(self.handle_index(ref)); }
                catch (...) { handle_write_error(self.quit_on_error); }
            }

            void consume_handle_delete(Ref ref) override {
                uint64_t delta;
                if (self.handle_delete_delta(ref, &delta)) {
                    try { self.stream->write_handle_delete(delta); }
                    catch (...) { handle_write_error(self.quit_on_error); }
                }
            }

            void consume_flush() override {
                try { self.stream->flush(); } catch (...) { handle_write_error(self.quit_on_error); }
            }

            void consume_shutdown() override {
                consume_flush();
                saw_shutdown = true;
            }

            void consume_list(Queue& queue, uint32_t len) override {
                try { self.stream->write_list_header(len); } catch (...) { handle_write_error(self.quit_on_error); }
                for (uint32_t i = 0; i < len; i++) queue.consume(*this);
            }

            void consume_tuple(Queue& queue, uint32_t len) override {
                try { self.stream->write_tuple_header(len); } catch (...) { handle_write_error(self.quit_on_error); }
                for (uint32_t i = 0; i < len; i++) queue.consume(*this);
            }

            void consume_dict(Queue& queue, uint32_t len) override {
                try { self.stream->write_dict_header(len); } catch (...) { handle_write_error(self.quit_on_error); }
                for (uint32_t i = 0; i < len; i++) {
                    queue.consume(*this);
                    queue.consume(*this);
                }
            }

            void consume_heartbeat(Queue& queue) override {
                try { self.stream->write_control(Heartbeat); } catch (...) { handle_write_error(self.quit_on_error); }
                queue.consume(*this);
            }

            void consume_external_wrapped() override {}

            void consume_delete(PyObject* obj) override {
                try { self.stream->object_freed(obj); } catch (...) { handle_write_error(self.quit_on_error); }
            }

            void consume_thread(PyThreadState* tstate) override {
                if (bind_trace_enabled()) {
                    fprintf(stderr,
                            "retrace-bind persister-thread-stamp tstate=%p last=%p processed=%llu\n",
                            (void*)tstate,
                            (void*)self.last_tstate,
                            (unsigned long long)self.processed_cursor.load(std::memory_order_relaxed));
                    fflush(stderr);
                }
                if (tstate != self.last_tstate) {
                    self.last_tstate = tstate;
                    auto& cache = *self.thread_cache;
                    auto it = cache.find(tstate);
                    PyObject* handle;
                    if (it != cache.end()) {
                        handle = it->second;
                    } else {
                        handle = tstate->dict
                            ? PyDict_GetItem(tstate->dict, self.writer_key)
                            : nullptr;
                        if (handle) {
                            Py_INCREF(handle);
                            cache[tstate] = handle;
                        }
                    }
                    if (handle) {
                        try { self.stream->write_thread_switch(handle); }
                        catch (...) { handle_write_error(self.quit_on_error); }
                    }
                }
            }

            void consume_pickled(PyObject* obj) override {
                try { self.stream->write_pre_pickled(obj); } catch (...) { handle_write_error(self.quit_on_error); }
            }

            void consume_new_handle(Ref ref, PyObject* obj) override {
                try { self.stream->write_new_handle(obj); } catch (...) { handle_write_error(self.quit_on_error); }
                auto [it, inserted] = self.ref_indices->emplace(ref, self.next_handle_index);
                if (inserted) self.next_handle_index++;
            }

            void consume_new_patched(PyObject* obj, PyObject* type) override {
                try { self.stream->new_patched(obj, type); } catch (...) { handle_write_error(self.quit_on_error); }
            }

            void consume_bind(PyObject* obj) override {
                if (bind_trace_enabled()) {
                    fprintf(stderr,
                            "retrace-bind persister-bind obj=%p label=%s processed=%llu\n",
                            (void*)obj,
                            bind_label(obj),
                            (unsigned long long)self.processed_cursor.load(std::memory_order_relaxed));
                    fflush(stderr);
                }
                try { self.stream->bind(obj); } catch (...) { handle_write_error(self.quit_on_error); }
            }

            void consume_serialize_error(Queue& queue) override {
                try { self.stream->write_control(SerializeError); } catch (...) { handle_write_error(self.quit_on_error); }
                queue.consume(*this);
            }
        };

        struct ReleaseConsumer : QueueConsumer {
            bool return_consumed_objects() const override { return false; }

            void consume_object(PyObject*) override {}
            void consume_handle_ref(Ref) override {}
            void consume_handle_delete(Ref) override {}
            void consume_flush() override {}
            void consume_shutdown() override {}
            void consume_list(Queue& queue, uint32_t len) override {
                for (uint32_t i = 0; i < len; i++) queue.consume(*this);
            }
            void consume_tuple(Queue& queue, uint32_t len) override {
                for (uint32_t i = 0; i < len; i++) queue.consume(*this);
            }
            void consume_dict(Queue& queue, uint32_t len) override {
                for (uint32_t i = 0; i < len; i++) {
                    queue.consume(*this);
                    queue.consume(*this);
                }
            }
            void consume_heartbeat(Queue& queue) override { queue.consume(*this); }
            void consume_external_wrapped() override {}
            void consume_delete(PyObject*) override {}
            void consume_thread(PyThreadState*) override {}
            void consume_pickled(PyObject*) override {}
            void consume_new_handle(Ref, PyObject*) override {}
            void consume_new_patched(PyObject*, PyObject*) override {}
            void consume_bind(PyObject*) override {}
            void consume_serialize_error(Queue& queue) override { queue.consume(*this); }
        };

        uint64_t handle_index(Ref handle) {
            if (ref_indices) {
                auto it = ref_indices->find(handle);
                if (it != ref_indices->end()) {
                    return it->second;
                }
                uint64_t index = next_handle_index++;
                (*ref_indices)[handle] = index;
                return index;
            }
            return index_of_handle(handle);
        }

        bool handle_delete_delta(Ref handle, uint64_t* delta) {
            if (ref_indices) {
                auto it = ref_indices->find(handle);
                if (it != ref_indices->end()) {
                    uint64_t index = it->second;
                    ref_indices->erase(it);
                    assert(next_handle_index > index);
                    *delta = next_handle_index - index - 1;
                    return true;
                }
                return false;
            }
            uint64_t index = handle_index(handle);
            assert(next_handle_index > index);
            *delta = next_handle_index - index - 1;
            return true;
        }

        static void writer_loop(AsyncFilePersister* self) {
            WriterConsumer consumer(*self);
            while (true) {
                while (!self->queue->has_entries()) {
                    if (self->shutdown_flag.load(std::memory_order_acquire)) return;
                    std::this_thread::yield();
                }

                PyGILState_STATE gstate = PyGILState_Ensure();

                while (self->queue->try_consume(consumer)) {
                    self->processed_cursor.fetch_add(1, std::memory_order_release);
                    if (consumer.saw_shutdown) {
                        PyGILState_Release(gstate);
                        return;
                    }
                }

                try { self->stream->flush(); } catch (...) { handle_write_error(self->quit_on_error); }

                PyGILState_Release(gstate);
            }
        }

        static void drain_loop(AsyncFilePersister* self) {
            while (true) {
                PyObject** ep;
                while (!(ep = self->queue->return_front())) {
                    if (self->return_shutdown.load(std::memory_order_acquire))
                        return;
                    std::this_thread::yield();
                }

                PyGILState_STATE gstate = PyGILState_Ensure();
                auto batch_start = std::chrono::steady_clock::now();
                int deallocs = 0;

                while ((ep = self->queue->return_front())) {
                    PyObject* obj = *ep;
                    self->queue->return_pop();
                    self->queue->note_removed(estimate_size(obj));
                    if (Py_REFCNT(obj) == 1) deallocs++;
                    Py_DECREF(obj);
                    if (deallocs >= 32) {
                        deallocs = 0;
                        auto now = std::chrono::steady_clock::now();
                        if (now - batch_start > std::chrono::microseconds(100)) {
                            PyGILState_Release(gstate);
                            std::this_thread::yield();
                            gstate = PyGILState_Ensure();
                            batch_start = std::chrono::steady_clock::now();
                        }
                    }
                }

                PyGILState_Release(gstate);
            }
        }

        SetupResult setup(PyObject* serializer, size_t queue_capacity,
                         size_t return_queue_capacity, int64_t inflight_limit,
                         int stall_timeout_seconds, PyObject* wkey, bool quit_on_error_arg) {
            if (queue) return {queue};
            quit_on_error = quit_on_error_arg;

            queue = new Queue(queue_capacity, return_queue_capacity,
                              inflight_limit, stall_timeout_seconds);
            writer_key = wkey;
            last_tstate = nullptr;
            thread_cache = new std::unordered_map<PyThreadState*, PyObject*>();
            ref_indices = new std::unordered_map<Ref, uint64_t>();

            stream = new MessageStream(*fw, serializer, quit_on_error);

            shutdown_flag.store(false, std::memory_order_release);
            return_shutdown.store(false, std::memory_order_release);
            writer_thread = std::thread(writer_loop, this);
            return_thread = std::thread(drain_loop, this);
            thread_started = true;

            return {queue};
        }

        void drain_queue_entries() {
            if (!queue) return;
            ReleaseConsumer consumer;
            while (queue->try_consume(consumer)) {}
        }

        void drain_return_queue() {
            if (!queue) return;
            while (auto* ep = queue->return_front()) {
                PyObject* obj = *ep;
                queue->return_pop();
                queue->note_removed(estimate_size(obj));
                Py_DECREF(obj);
            }
        }

        void clear_thread_cache() {
            if (!thread_cache) return;
            for (auto& kv : *thread_cache)
                Py_DECREF(kv.second);
            thread_cache->clear();
        }

        void do_close() {
            if (closed) return;
            closed = true;

            shutdown_flag.store(true, std::memory_order_release);

            if (writer_thread.joinable()) {
                Py_BEGIN_ALLOW_THREADS
                writer_thread.join();
                Py_END_ALLOW_THREADS
            }

            return_shutdown.store(true, std::memory_order_release);

            if (return_thread.joinable()) {
                Py_BEGIN_ALLOW_THREADS
                return_thread.join();
                Py_END_ALLOW_THREADS
            }

            thread_started = false;

            drain_return_queue();
            drain_queue_entries();

            if (stream) {
                delete stream;
                stream = nullptr;
            }

            clear_thread_cache();
            if (thread_cache) {
                delete thread_cache;
                thread_cache = nullptr;
            }
            if (ref_indices) {
                delete ref_indices;
                ref_indices = nullptr;
            }

            if (queue) {
                delete queue;
                queue = nullptr;
            }
        }

        static PyObject* py_close(AsyncFilePersister* self, PyObject* unused) {
            self->do_close();
            Py_RETURN_NONE;
        }

        void do_drain() {
            if (closed || !thread_started) return;

            shutdown_flag.store(true, std::memory_order_release);

            if (writer_thread.joinable()) {
                Py_BEGIN_ALLOW_THREADS
                writer_thread.join();
                Py_END_ALLOW_THREADS
            }

            return_shutdown.store(true, std::memory_order_release);

            if (return_thread.joinable()) {
                Py_BEGIN_ALLOW_THREADS
                return_thread.join();
                Py_END_ALLOW_THREADS
            }

            drain_return_queue();
            thread_started = false;

            shutdown_flag.store(false, std::memory_order_release);
            return_shutdown.store(false, std::memory_order_release);
        }

        void do_resume() {
            if (closed || !fw || !queue) return;
            fw->stamp_pid();
            last_tstate = nullptr;
            clear_thread_cache();
            shutdown_flag.store(false, std::memory_order_release);
            return_shutdown.store(false, std::memory_order_release);
            writer_thread = std::thread(writer_loop, this);
            return_thread = std::thread(drain_loop, this);
            thread_started = true;
        }

        static PyObject* py_drain(AsyncFilePersister* self, PyObject* unused) {
            self->do_drain();
            Py_RETURN_NONE;
        }

        static PyObject* py_resume(AsyncFilePersister* self, PyObject* unused) {
            self->do_resume();
            Py_RETURN_NONE;
        }

        static PyObject* tp_new(PyTypeObject* type, PyObject* args, PyObject* kwds) {
            AsyncFilePersister* self = (AsyncFilePersister*)type->tp_alloc(type, 0);
            if (self) {
                self->framed_writer_obj = nullptr;
                self->fw = nullptr;
                self->shutdown_flag.store(false);
                self->return_shutdown.store(false);
                self->closed = true;
                self->thread_started = false;
                self->quit_on_error = false;
                self->queue = nullptr;
                self->stream = nullptr;
                self->writer_key = nullptr;
                self->last_tstate = nullptr;
                self->thread_cache = nullptr;
                self->ref_indices = nullptr;
                self->processed_cursor.store(0);
                new (&self->writer_thread) std::thread();
                new (&self->return_thread) std::thread();
            }
            return (PyObject*)self;
        }

        static int init(AsyncFilePersister* self, PyObject* args, PyObject* kwds) {
            PyObject* writer_obj;

            static const char* kwlist[] = {"writer", nullptr};
            if (!PyArg_ParseTupleAndKeywords(args, kwds, "O", (char**)kwlist, &writer_obj))
                return -1;

            FramedWriter* fw_ptr = FramedWriter_get(writer_obj);
            if (!fw_ptr) return -1;

            self->framed_writer_obj = Py_NewRef(writer_obj);
            self->fw = fw_ptr;
            self->closed = false;

            return 0;
        }

        static int traverse(AsyncFilePersister* self, visitproc visit, void* arg) {
            Py_VISIT(self->framed_writer_obj);
            return 0;
        }

        static int clear(AsyncFilePersister* self) {
            Py_CLEAR(self->framed_writer_obj);
            return 0;
        }

        static void dealloc(AsyncFilePersister* self) {
            self->do_close();

            PyObject_GC_UnTrack(self);
            clear(self);

            self->writer_thread.~thread();
            self->return_thread.~thread();

            Py_TYPE(self)->tp_free((PyObject*)self);
        }
    };

    struct DebugPersister : PyObject {
        PyObject* handler;
        std::thread writer_thread;
        std::thread return_thread;
        std::atomic<bool> shutdown_flag;
        std::atomic<bool> return_shutdown;
        bool closed;
        bool thread_started;
        bool quit_on_error;

        Queue* queue;

        std::atomic<uint64_t> processed_cursor{0};

        struct EventConsumer : QueueConsumer {
            DebugPersister& self;
            PyObject* event = nullptr;
            bool saw_shutdown = false;

            explicit EventConsumer(DebugPersister& persister) : self(persister) {}

            ~EventConsumer() override {
                Py_XDECREF(event);
            }

            PyObject* take_event() {
                PyObject* out = event;
                event = nullptr;
                return out;
            }

            PyObject* consume_nested(Queue& queue) {
                Py_XDECREF(event);
                event = nullptr;
                queue.consume(*this);
                return take_event();
            }

            PyObject* consume_event_list(Queue& queue, uint32_t n) {
                PyObject* list = PyList_New(n);
                if (!list) return nullptr;

                for (uint32_t i = 0; i < n; i++) {
                    PyObject* item = consume_nested(queue);
                    if (!item) {
                        Py_DECREF(list);
                        return nullptr;
                    }
                    PyList_SET_ITEM(list, i, item);
                }
                return list;
            }

            void set_event(PyObject* value) {
                Py_XDECREF(event);
                event = value;
            }

            void consume_object(PyObject* obj) override {
                set_event(make_object_event(obj));
            }

            void consume_handle_ref(Ref ref) override {
                set_event(make_event_tuple("handle_ref", ref_as_int(ref)));
            }

            void consume_handle_delete(Ref ref) override {
                set_event(make_event_tuple("handle_delete", ref_as_int(ref)));
            }

            void consume_flush() override {
                set_event(make_command_event("flush", PyTuple_New(0)));
            }

            void consume_shutdown() override {
                saw_shutdown = true;
                set_event(make_command_event("shutdown", PyTuple_New(0)));
            }

            void consume_list(Queue& queue, uint32_t len) override {
                PyObject* items = consume_event_list(queue, len);
                if (!items) {
                    set_event(nullptr);
                    return;
                }
                PyObject* args = PyTuple_New(2);
                PyObject* len_obj = args ? PyLong_FromUnsignedLong(len) : nullptr;
                if (!args || !len_obj) {
                    Py_XDECREF(args);
                    Py_DECREF(items);
                    Py_XDECREF(len_obj);
                    set_event(nullptr);
                    return;
                }
                PyTuple_SET_ITEM(args, 0, len_obj);
                PyTuple_SET_ITEM(args, 1, items);
                set_event(make_command_event("list", args));
            }

            void consume_tuple(Queue& queue, uint32_t len) override {
                PyObject* items = consume_event_list(queue, len);
                if (!items) {
                    set_event(nullptr);
                    return;
                }
                PyObject* args = PyTuple_New(2);
                PyObject* len_obj = args ? PyLong_FromUnsignedLong(len) : nullptr;
                if (!args || !len_obj) {
                    Py_XDECREF(args);
                    Py_DECREF(items);
                    Py_XDECREF(len_obj);
                    set_event(nullptr);
                    return;
                }
                PyTuple_SET_ITEM(args, 0, len_obj);
                PyTuple_SET_ITEM(args, 1, items);
                set_event(make_command_event("tuple", args));
            }

            void consume_dict(Queue& queue, uint32_t len) override {
                PyObject* items = consume_event_list(queue, len * 2);
                if (!items) {
                    set_event(nullptr);
                    return;
                }
                PyObject* args = PyTuple_New(2);
                PyObject* len_obj = args ? PyLong_FromUnsignedLong(len) : nullptr;
                if (!args || !len_obj) {
                    Py_XDECREF(args);
                    Py_DECREF(items);
                    Py_XDECREF(len_obj);
                    set_event(nullptr);
                    return;
                }
                PyTuple_SET_ITEM(args, 0, len_obj);
                PyTuple_SET_ITEM(args, 1, items);
                set_event(make_command_event("dict", args));
            }

            void consume_heartbeat(Queue& queue) override {
                PyObject* item = consume_nested(queue);
                if (!item) {
                    set_event(nullptr);
                    return;
                }
                PyObject* args = PyTuple_New(1);
                if (!args) {
                    Py_DECREF(item);
                    set_event(nullptr);
                    return;
                }
                PyTuple_SET_ITEM(args, 0, item);
                set_event(make_command_event("heartbeat", args));
            }

            void consume_external_wrapped() override {
                set_event(make_command_event("external_wrapped", PyTuple_New(0)));
            }

            void consume_delete(PyObject* obj) override {
                PyObject* item = make_object_event(obj);
                if (!item) {
                    set_event(nullptr);
                    return;
                }
                PyObject* args = PyTuple_New(1);
                if (!args) {
                    Py_DECREF(item);
                    set_event(nullptr);
                    return;
                }
                PyTuple_SET_ITEM(args, 0, item);
                set_event(make_command_event("delete", args));
            }

            void consume_thread(PyThreadState* tstate) override {
                PyObject* tstate_obj = ptr_as_int((void*)tstate);
                if (!tstate_obj) {
                    set_event(nullptr);
                    return;
                }
                PyObject* args = PyTuple_New(1);
                if (!args) {
                    Py_DECREF(tstate_obj);
                    set_event(nullptr);
                    return;
                }
                PyTuple_SET_ITEM(args, 0, tstate_obj);
                set_event(make_command_event("thread", args));
            }

            void consume_pickled(PyObject* obj) override {
                PyObject* item = make_object_event(obj);
                if (!item) {
                    set_event(nullptr);
                    return;
                }
                PyObject* args = PyTuple_New(1);
                if (!args) {
                    Py_DECREF(item);
                    set_event(nullptr);
                    return;
                }
                PyTuple_SET_ITEM(args, 0, item);
                set_event(make_command_event("pickled", args));
            }

            void consume_new_handle(Ref ref, PyObject* obj) override {
                PyObject* ref_obj = ref_as_int(ref);
                PyObject* obj_event = make_object_event(obj);
                if (!ref_obj || !obj_event) {
                    Py_XDECREF(ref_obj);
                    Py_XDECREF(obj_event);
                    set_event(nullptr);
                    return;
                }
                PyObject* args = PyTuple_New(2);
                if (!args) {
                    Py_DECREF(ref_obj);
                    Py_DECREF(obj_event);
                    set_event(nullptr);
                    return;
                }
                PyTuple_SET_ITEM(args, 0, ref_obj);
                PyTuple_SET_ITEM(args, 1, obj_event);
                set_event(make_command_event("new_handle", args));
            }

            void consume_new_patched(PyObject* obj, PyObject* type) override {
                PyObject* obj_event = make_object_event(obj);
                PyObject* type_event = make_object_event(type);
                if (!obj_event || !type_event) {
                    Py_XDECREF(obj_event);
                    Py_XDECREF(type_event);
                    set_event(nullptr);
                    return;
                }
                PyObject* args = PyTuple_New(2);
                if (!args) {
                    Py_DECREF(obj_event);
                    Py_DECREF(type_event);
                    set_event(nullptr);
                    return;
                }
                PyTuple_SET_ITEM(args, 0, obj_event);
                PyTuple_SET_ITEM(args, 1, type_event);
                set_event(make_command_event("new_patched", args));
            }

            void consume_bind(PyObject* obj) override {
                PyObject* item = make_object_event(obj);
                if (!item) {
                    set_event(nullptr);
                    return;
                }
                PyObject* args = PyTuple_New(1);
                if (!args) {
                    Py_DECREF(item);
                    set_event(nullptr);
                    return;
                }
                PyTuple_SET_ITEM(args, 0, item);
                set_event(make_command_event("bind", args));
            }

            void consume_serialize_error(Queue& queue) override {
                PyObject* item = consume_nested(queue);
                if (!item) {
                    set_event(nullptr);
                    return;
                }
                PyObject* args = PyTuple_New(1);
                if (!args) {
                    Py_DECREF(item);
                    set_event(nullptr);
                    return;
                }
                PyTuple_SET_ITEM(args, 0, item);
                set_event(make_command_event("serialize_error", args));
            }
        };

        struct ReleaseConsumer : QueueConsumer {
            bool return_consumed_objects() const override { return false; }

            void consume_object(PyObject*) override {}
            void consume_handle_ref(Ref) override {}
            void consume_handle_delete(Ref) override {}
            void consume_flush() override {}
            void consume_shutdown() override {}
            void consume_list(Queue& queue, uint32_t len) override {
                for (uint32_t i = 0; i < len; i++) queue.consume(*this);
            }
            void consume_tuple(Queue& queue, uint32_t len) override {
                for (uint32_t i = 0; i < len; i++) queue.consume(*this);
            }
            void consume_dict(Queue& queue, uint32_t len) override {
                for (uint32_t i = 0; i < len; i++) {
                    queue.consume(*this);
                    queue.consume(*this);
                }
            }
            void consume_heartbeat(Queue& queue) override { queue.consume(*this); }
            void consume_external_wrapped() override {}
            void consume_delete(PyObject*) override {}
            void consume_thread(PyThreadState*) override {}
            void consume_pickled(PyObject*) override {}
            void consume_new_handle(Ref, PyObject*) override {}
            void consume_new_patched(PyObject*, PyObject*) override {}
            void consume_bind(PyObject*) override {}
            void consume_serialize_error(Queue& queue) override { queue.consume(*this); }
        };

        static void writer_loop(DebugPersister* self) {
            EventConsumer consumer(*self);
            while (true) {
                while (!self->queue->has_entries()) {
                    if (self->shutdown_flag.load(std::memory_order_acquire)) return;
                    std::this_thread::yield();
                }

                PyGILState_STATE gstate = PyGILState_Ensure();

                while (self->queue->try_consume(consumer)) {
                    PyObject* event = consumer.take_event();
                    if (!event) {
                        handle_debug_error(self->quit_on_error);
                    } else {
                        PyObject* result = dispatch_debug_handler(self->handler, event);
                        Py_DECREF(event);
                        if (!result) {
                            handle_debug_error(self->quit_on_error);
                        } else {
                            Py_DECREF(result);
                        }
                    }

                    self->processed_cursor.fetch_add(1, std::memory_order_release);
                    if (consumer.saw_shutdown) {
                        PyGILState_Release(gstate);
                        return;
                    }
                }

                PyGILState_Release(gstate);
            }
        }

        static void drain_loop(DebugPersister* self) {
            while (true) {
                PyObject** ep;
                while (!(ep = self->queue->return_front())) {
                    if (self->return_shutdown.load(std::memory_order_acquire))
                        return;
                    std::this_thread::yield();
                }

                PyGILState_STATE gstate = PyGILState_Ensure();
                auto batch_start = std::chrono::steady_clock::now();
                int deallocs = 0;

                while ((ep = self->queue->return_front())) {
                    PyObject* obj = *ep;
                    self->queue->return_pop();
                    self->queue->note_removed(estimate_size(obj));
                    if (Py_REFCNT(obj) == 1) deallocs++;
                    Py_DECREF(obj);
                    if (deallocs >= 32) {
                        deallocs = 0;
                        auto now = std::chrono::steady_clock::now();
                        if (now - batch_start > std::chrono::microseconds(100)) {
                            PyGILState_Release(gstate);
                            std::this_thread::yield();
                            gstate = PyGILState_Ensure();
                            batch_start = std::chrono::steady_clock::now();
                        }
                    }
                }

                PyGILState_Release(gstate);
            }
        }

        SetupResult setup(PyObject*, size_t queue_capacity,
                          size_t return_queue_capacity, int64_t inflight_limit,
                          int stall_timeout_seconds, PyObject*, bool quit_on_error_arg) {
            if (queue) return {queue};
            quit_on_error = quit_on_error_arg;

            queue = new Queue(queue_capacity, return_queue_capacity,
                              inflight_limit, stall_timeout_seconds);

            shutdown_flag.store(false, std::memory_order_release);
            return_shutdown.store(false, std::memory_order_release);
            writer_thread = std::thread(writer_loop, this);
            return_thread = std::thread(drain_loop, this);
            thread_started = true;

            return {queue};
        }

        void drain_queue_entries() {
            if (!queue) return;
            ReleaseConsumer consumer;
            while (queue->try_consume(consumer)) {}
        }

        void drain_return_queue() {
            if (!queue) return;
            while (auto* ep = queue->return_front()) {
                PyObject* obj = *ep;
                queue->return_pop();
                queue->note_removed(estimate_size(obj));
                Py_DECREF(obj);
            }
        }

        void do_close() {
            if (closed) return;
            closed = true;

            shutdown_flag.store(true, std::memory_order_release);

            if (writer_thread.joinable()) {
                Py_BEGIN_ALLOW_THREADS
                writer_thread.join();
                Py_END_ALLOW_THREADS
            }

            return_shutdown.store(true, std::memory_order_release);

            if (return_thread.joinable()) {
                Py_BEGIN_ALLOW_THREADS
                return_thread.join();
                Py_END_ALLOW_THREADS
            }

            thread_started = false;

            drain_return_queue();
            drain_queue_entries();

            if (queue) {
                delete queue;
                queue = nullptr;
            }
        }

        static PyObject* py_close(DebugPersister* self, PyObject*) {
            self->do_close();
            Py_RETURN_NONE;
        }

        void do_drain() {
            if (closed || !thread_started) return;

            shutdown_flag.store(true, std::memory_order_release);

            if (writer_thread.joinable()) {
                Py_BEGIN_ALLOW_THREADS
                writer_thread.join();
                Py_END_ALLOW_THREADS
            }

            return_shutdown.store(true, std::memory_order_release);

            if (return_thread.joinable()) {
                Py_BEGIN_ALLOW_THREADS
                return_thread.join();
                Py_END_ALLOW_THREADS
            }

            drain_return_queue();
            thread_started = false;
            shutdown_flag.store(false, std::memory_order_release);
            return_shutdown.store(false, std::memory_order_release);
        }

        void do_resume() {
            if (closed || !queue) return;
            shutdown_flag.store(false, std::memory_order_release);
            return_shutdown.store(false, std::memory_order_release);
            writer_thread = std::thread(writer_loop, this);
            return_thread = std::thread(drain_loop, this);
            thread_started = true;
        }

        static PyObject* py_drain(DebugPersister* self, PyObject*) {
            self->do_drain();
            Py_RETURN_NONE;
        }

        static PyObject* py_resume(DebugPersister* self, PyObject*) {
            self->do_resume();
            Py_RETURN_NONE;
        }

        static PyObject* tp_new(PyTypeObject* type, PyObject*, PyObject*) {
            DebugPersister* self = (DebugPersister*)type->tp_alloc(type, 0);
            if (self) {
                self->handler = nullptr;
                self->shutdown_flag.store(false);
                self->return_shutdown.store(false);
                self->closed = true;
                self->thread_started = false;
                self->quit_on_error = false;
                self->queue = nullptr;
                self->processed_cursor.store(0);
                new (&self->writer_thread) std::thread();
                new (&self->return_thread) std::thread();
            }
            return (PyObject*)self;
        }

        static int init(DebugPersister* self, PyObject* args, PyObject* kwds) {
            PyObject* handler_obj;

            static const char* kwlist[] = {"handler", nullptr};
            if (!PyArg_ParseTupleAndKeywords(args, kwds, "O", (char**)kwlist, &handler_obj))
                return -1;

            if (!PyCallable_Check(handler_obj)) {
                PyObject* method = PyObject_GetAttrString(handler_obj, "handle_event");
                if (!method) {
                    PyErr_SetString(PyExc_TypeError,
                                    "DebugPersister handler must be callable or define handle_event(event)");
                    return -1;
                }
                Py_DECREF(method);
            }

            self->handler = Py_NewRef(handler_obj);
            self->closed = false;
            return 0;
        }

        static int traverse(DebugPersister* self, visitproc visit, void* arg) {
            Py_VISIT(self->handler);
            return 0;
        }

        static int clear(DebugPersister* self) {
            Py_CLEAR(self->handler);
            return 0;
        }

        static void dealloc(DebugPersister* self) {
            self->do_close();

            PyObject_GC_UnTrack(self);
            clear(self);

            self->writer_thread.~thread();
            self->return_thread.~thread();

            Py_TYPE(self)->tp_free((PyObject*)self);
        }
    };

    static PyObject* AsyncFilePersister_path_getter(PyObject* obj, void*) {
        AsyncFilePersister* self = (AsyncFilePersister*)obj;
        if (self->framed_writer_obj) {
            return PyObject_GetAttrString(self->framed_writer_obj, "path");
        }
        return PyUnicode_FromString("");
    }

    static PyObject* AsyncFilePersister_fd_getter(PyObject* obj, void*) {
        AsyncFilePersister* self = (AsyncFilePersister*)obj;
        return PyLong_FromLong(self->fw ? self->fw->fd() : -1);
    }

    static PyObject* AsyncFilePersister_is_fifo_getter(PyObject* obj, void*) {
        AsyncFilePersister* self = (AsyncFilePersister*)obj;
        if (self->framed_writer_obj) {
            return PyObject_GetAttrString(self->framed_writer_obj, "is_fifo");
        }
        return PyBool_FromLong(0);
    }

    static PyMethodDef AsyncFilePersister_methods[] = {
        {"close", (PyCFunction)AsyncFilePersister::py_close, METH_NOARGS,
         "Flush pending writes, join writer thread, close file"},
        {"drain", (PyCFunction)AsyncFilePersister::py_drain, METH_NOARGS,
         "Drain queue and stop writer thread, keeping the fd open"},
        {"resume", (PyCFunction)AsyncFilePersister::py_resume, METH_NOARGS,
         "Start a new writer thread on the existing fd"},
        {NULL}
    };

    static PyGetSetDef AsyncFilePersister_getset[] = {
        {"path", AsyncFilePersister_path_getter, nullptr, "File path", NULL},
        {"fd", AsyncFilePersister_fd_getter, nullptr, "Underlying file descriptor", NULL},
        {"is_fifo", AsyncFilePersister_is_fifo_getter, nullptr, "True if the output is a named pipe", NULL},
        {NULL}
    };

    SetupResult AsyncFilePersister_setup(PyObject* persister, PyObject* serializer,
                                         size_t queue_capacity,
                                         size_t return_queue_capacity,
                                         int64_t inflight_limit,
                                         int stall_timeout_seconds,
                                         PyObject* writer_key,
                                         bool quit_on_error) {
        if (Py_TYPE(persister) != &AsyncFilePersister_Type) {
            PyErr_SetString(PyExc_TypeError, "expected AsyncFilePersister");
            return {nullptr};
        }
        return ((AsyncFilePersister*)persister)->setup(serializer, queue_capacity,
                                                       return_queue_capacity,
                                                       inflight_limit,
                                                       stall_timeout_seconds,
                                                       writer_key, quit_on_error);
    }

    PyTypeObject AsyncFilePersister_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "AsyncFilePersister",
        .tp_basicsize = sizeof(AsyncFilePersister),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)AsyncFilePersister::dealloc,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
        .tp_doc = "Async file persister -- serializes and writes to file on a background thread",
        .tp_traverse = (traverseproc)AsyncFilePersister::traverse,
        .tp_clear = (inquiry)AsyncFilePersister::clear,
        .tp_methods = AsyncFilePersister_methods,
        .tp_getset = AsyncFilePersister_getset,
        .tp_init = (initproc)AsyncFilePersister::init,
        .tp_new = AsyncFilePersister::tp_new,
    };

    static PyObject* DebugPersister_handler_getter(PyObject* obj, void*) {
        DebugPersister* self = (DebugPersister*)obj;
        return Py_NewRef(self->handler ? self->handler : Py_None);
    }

    static PyMethodDef DebugPersister_methods[] = {
        {"close", (PyCFunction)DebugPersister::py_close, METH_NOARGS,
         "Join worker threads and stop debug event delivery"},
        {"drain", (PyCFunction)DebugPersister::py_drain, METH_NOARGS,
         "Drain queued objects and stop worker threads"},
        {"resume", (PyCFunction)DebugPersister::py_resume, METH_NOARGS,
         "Restart worker threads on the existing queue"},
        {NULL}
    };

    static PyGetSetDef DebugPersister_getset[] = {
        {"handler", DebugPersister_handler_getter, nullptr, "Python event handler", NULL},
        {NULL}
    };

    SetupResult DebugPersister_setup(PyObject* persister, PyObject* serializer,
                                     size_t queue_capacity,
                                     size_t return_queue_capacity,
                                     int64_t inflight_limit,
                                     int stall_timeout_seconds,
                                     PyObject* writer_key,
                                     bool quit_on_error) {
        if (Py_TYPE(persister) != &DebugPersister_Type) {
            PyErr_SetString(PyExc_TypeError, "expected DebugPersister");
            return {nullptr};
        }
        return ((DebugPersister*)persister)->setup(serializer, queue_capacity,
                                                   return_queue_capacity,
                                                   inflight_limit,
                                                   stall_timeout_seconds,
                                                   writer_key, quit_on_error);
    }

    PyTypeObject DebugPersister_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "DebugPersister",
        .tp_basicsize = sizeof(DebugPersister),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)DebugPersister::dealloc,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
        .tp_doc = "Debug persister -- decodes queue entries and forwards them to a Python handler",
        .tp_traverse = (traverseproc)DebugPersister::traverse,
        .tp_clear = (inquiry)DebugPersister::clear,
        .tp_methods = DebugPersister_methods,
        .tp_getset = DebugPersister_getset,
        .tp_init = (initproc)DebugPersister::init,
        .tp_new = DebugPersister::tp_new,
    };
}
