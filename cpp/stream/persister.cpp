#include "stream.h"
#include "writer.h"
#include "framed_writer.h"
#include "queueentry.h"
#include "vendor/SPSCQueue.h"
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

        rigtorp::SPSCQueue<QEntry>* queue;
        rigtorp::SPSCQueue<PyObject*>* return_queue;
        MessageStream* stream;
        std::atomic<int64_t>* total_removed_ptr;
        PyObject* writer_key;
        PyThreadState* last_tstate;
        std::unordered_map<PyThreadState*, PyObject*>* thread_cache;

        std::atomic<uint64_t> processed_cursor{0};

        QEntry consume_next() {
            QEntry* ep = queue->front();
            if (!ep) {
                // Queue empty while mid-compound-value: release the GIL so the
                // return thread can drain its queue and update total_removed,
                // which in turn unblocks the producer's wait_for_inflight().
                PyThreadState* _save = PyEval_SaveThread();
                while (!(ep = queue->front())) std::this_thread::yield();
                PyEval_RestoreThread(_save);
            }
            QEntry v = *ep;
            queue->pop();
            return v;
        }

        PyObject* consume_ptr() {
            return as_ptr(consume_next());
        }

        PyObject* consume_borrowed_ptr() {
            return as_ptr(consume_next());
        }

        void return_obj(PyObject* obj) {
            if (is_immortal(obj)) return;
            if (!return_queue->try_push(obj)) {
                Py_DECREF(obj);
            }
        }

        void consume_and_write_value() {
            QEntry e = consume_next();
            switch (tag_of(e)) {
                case TAG_OBJECT: {
                    PyObject* obj = as_ptr(e);
                    try { stream->write(obj); } catch (...) { handle_write_error(quit_on_error); }
                    return_obj(obj);
                    break;
                }
#if SIZEOF_VOID_P >= 8
                case TAG_PICKLED: {
                    PyObject* obj = as_ptr(e);
                    try { stream->write_pre_pickled(obj); } catch (...) { handle_write_error(quit_on_error); }
                    return_obj(obj);
                    break;
                }
#endif
                case TAG_COMMAND:
                    switch (cmd_of(e)) {
                        case CMD_LIST: {
                            uint32_t n = len_of(e);
                            try { stream->write_list_header(n); } catch (...) { handle_write_error(quit_on_error); }
                            for (uint32_t i = 0; i < n; i++) consume_and_write_value();
                            break;
                        }
                        case CMD_TUPLE: {
                            uint32_t n = len_of(e);
                            try { stream->write_tuple_header(n); } catch (...) { handle_write_error(quit_on_error); }
                            for (uint32_t i = 0; i < n; i++) consume_and_write_value();
                            break;
                        }
                        case CMD_DICT: {
                            uint32_t n = len_of(e);
                            try { stream->write_dict_header(n); } catch (...) { handle_write_error(quit_on_error); }
                            for (uint32_t i = 0; i < n; i++) {
                                consume_and_write_value();
                                consume_and_write_value();
                            }
                            break;
                        }
                        case CMD_PICKLED: {
                            PyObject* obj = consume_ptr();
                            try { stream->write_pre_pickled(obj); } catch (...) { handle_write_error(quit_on_error); }
                            return_obj(obj);
                            break;
                        }
                        case CMD_SERIALIZE_ERROR:
                            try { stream->write_control(SerializeError); } catch (...) { handle_write_error(quit_on_error); }
                            consume_and_write_value();
                            break;
                        default: break;
                    }
                    break;
                default: break;
            }
        }

        static void writer_loop(AsyncFilePersister* self) {
            bool quit_on_error = self->quit_on_error;
            while (true) {
                QEntry* ep;
                while (!(ep = self->queue->front())) {
                    if (self->shutdown_flag.load(std::memory_order_acquire)) return;
                    std::this_thread::yield();
                }

                PyGILState_STATE gstate = PyGILState_Ensure();

                while ((ep = self->queue->front())) {
                    QEntry e = *ep;
                    self->queue->pop();

                    switch (tag_of(e)) {
                        case TAG_OBJECT: {
                            PyObject* obj = as_ptr(e);
                            try { self->stream->write(obj); } catch (...) { handle_write_error(quit_on_error); }
                            self->return_obj(obj);
                            break;
                        }
#if SIZEOF_VOID_P >= 8
                        case TAG_PICKLED: {
                            PyObject* obj = as_ptr(e);
                            try { self->stream->write_pre_pickled(obj); } catch (...) { handle_write_error(quit_on_error); }
                            self->return_obj(obj);
                            break;
                        }
                        case TAG_NEW_HANDLE: {
                            PyObject* obj = as_ptr(e);
                            try { self->stream->write_new_handle(obj); } catch (...) { handle_write_error(quit_on_error); }
                            self->return_obj(obj);
                            break;
                        }
                        case TAG_NEW_PATCHED: {
                            PyObject* obj = as_ptr(e);
                            PyObject* type = self->consume_borrowed_ptr();
                            try { self->stream->new_patched(obj, type); } catch (...) { handle_write_error(quit_on_error); }
                            break;
                        }
                        case TAG_BIND: {
                            PyObject* obj = as_ptr(e);
                            try { self->stream->bind(obj); } catch (...) { handle_write_error(quit_on_error); }
                            break;
                        }
#endif
                        case TAG_DELETE: {
                            try { self->stream->object_freed(as_ptr(e)); } catch (...) { handle_write_error(quit_on_error); }
                            break;
                        }
                        case TAG_THREAD: {
                            PyThreadState* tstate = as_tstate(e);
                            if (tstate != self->last_tstate) {
                                self->last_tstate = tstate;
                                auto& cache = *self->thread_cache;
                                auto it = cache.find(tstate);
                                PyObject* handle;
                                if (it != cache.end()) {
                                    handle = it->second;
                                } else {
                                    handle = tstate->dict
                                        ? PyDict_GetItem(tstate->dict, self->writer_key)
                                        : nullptr;
                                    if (handle) {
                                        Py_INCREF(handle);
                                        cache[tstate] = handle;
                                    }
                                }
                                if (handle) {
                                    try { self->stream->write_thread_switch(handle); }
                                    catch (...) { handle_write_error(quit_on_error); }
                                }
                            }
                            break;
                        }
                        case TAG_COMMAND: {
                            switch (cmd_of(e)) {
                                case CMD_HANDLE_REF:
                                    try { self->stream->write_handle_ref_by_index(len_of(e)); } catch (...) { handle_write_error(quit_on_error); }
                                    break;
                                case CMD_HANDLE_DELETE:
                                    try { self->stream->write_handle_delete(len_of(e)); } catch (...) { handle_write_error(quit_on_error); }
                                    break;
                                case CMD_FLUSH:
                                    try { self->stream->flush(); } catch (...) { handle_write_error(quit_on_error); }
                                    break;
                                case CMD_LIST: {
                                    uint32_t n = len_of(e);
                                    try { self->stream->write_list_header(n); } catch (...) { handle_write_error(quit_on_error); }
                                    for (uint32_t i = 0; i < n; i++) self->consume_and_write_value();
                                    break;
                                }
                                case CMD_TUPLE: {
                                    uint32_t n = len_of(e);
                                    try { self->stream->write_tuple_header(n); } catch (...) { handle_write_error(quit_on_error); }
                                    for (uint32_t i = 0; i < n; i++) self->consume_and_write_value();
                                    break;
                                }
                                case CMD_DICT: {
                                    uint32_t n = len_of(e);
                                    try { self->stream->write_dict_header(n); } catch (...) { handle_write_error(quit_on_error); }
                                    for (uint32_t i = 0; i < n; i++) {
                                        self->consume_and_write_value();
                                        self->consume_and_write_value();
                                    }
                                    break;
                                }
                                case CMD_HEARTBEAT:
                                    try { self->stream->write_control(Heartbeat); } catch (...) { handle_write_error(quit_on_error); }
                                    self->consume_and_write_value();
                                    break;
                                case CMD_SERIALIZE_ERROR:
                                    try { self->stream->write_control(SerializeError); } catch (...) { handle_write_error(quit_on_error); }
                                    self->consume_and_write_value();
                                    break;
                                case CMD_PICKLED: {
                                    PyObject* obj = self->consume_ptr();
                                    try { self->stream->write_pre_pickled(obj); } catch (...) { handle_write_error(quit_on_error); }
                                    self->return_obj(obj);
                                    break;
                                }
                                case CMD_NEW_HANDLE: {
                                    PyObject* obj = self->consume_ptr();
                                    try { self->stream->write_new_handle(obj); } catch (...) { handle_write_error(quit_on_error); }
                                    self->return_obj(obj);
                                    break;
                                }
                                case CMD_NEW_PATCHED: {
                                    PyObject* obj = self->consume_borrowed_ptr();
                                    PyObject* type = self->consume_borrowed_ptr();
                                    try { self->stream->new_patched(obj, type); } catch (...) { handle_write_error(quit_on_error); }
                                    break;
                                }
                                case CMD_BIND: {
                                    PyObject* obj = self->consume_borrowed_ptr();
                                    try { self->stream->bind(obj); } catch (...) { handle_write_error(quit_on_error); }
                                    break;
                                }
                                case CMD_SHUTDOWN:
                                    try { self->stream->flush(); } catch (...) { handle_write_error(quit_on_error); }
                                    self->processed_cursor.fetch_add(1, std::memory_order_release);
                                    PyGILState_Release(gstate);
                                    return;
                            }
                            break;
                        }
                    }

                    self->processed_cursor.fetch_add(1, std::memory_order_release);
                }

                try { self->stream->flush(); } catch (...) { handle_write_error(quit_on_error); }

                PyGILState_Release(gstate);
            }
        }

        static void drain_loop(AsyncFilePersister* self) {
            while (true) {
                PyObject** ep;
                while (!(ep = self->return_queue->front())) {
                    if (self->return_shutdown.load(std::memory_order_acquire))
                        return;
                    std::this_thread::yield();
                }

                PyGILState_STATE gstate = PyGILState_Ensure();
                auto batch_start = std::chrono::steady_clock::now();
                int deallocs = 0;

                while ((ep = self->return_queue->front())) {
                    PyObject* obj = *ep;
                    self->return_queue->pop();
                    if (self->total_removed_ptr)
                        self->total_removed_ptr->fetch_add(estimate_size(obj), std::memory_order_relaxed);
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
                         size_t return_queue_capacity, std::atomic<int64_t>* total_removed,
                         PyObject* wkey, bool quit_on_error_arg) {
            if (queue) return {queue, return_queue};
            quit_on_error = quit_on_error_arg;

            queue = new rigtorp::SPSCQueue<QEntry>(queue_capacity);
            return_queue = new rigtorp::SPSCQueue<PyObject*>(return_queue_capacity);
            total_removed_ptr = total_removed;
            writer_key = wkey;
            last_tstate = nullptr;
            thread_cache = new std::unordered_map<PyThreadState*, PyObject*>();

            stream = new MessageStream(*fw, serializer, quit_on_error);

            shutdown_flag.store(false, std::memory_order_release);
            return_shutdown.store(false, std::memory_order_release);
            writer_thread = std::thread(writer_loop, this);
            return_thread = std::thread(drain_loop, this);
            thread_started = true;

            return {queue, return_queue};
        }

        void drain_value() {
            while (!queue->front()) std::this_thread::yield();
            QEntry e = *queue->front();
            queue->pop();
            switch (tag_of(e)) {
                case TAG_OBJECT:
#if SIZEOF_VOID_P >= 8
                case TAG_PICKLED:
                case TAG_NEW_HANDLE:
#endif
                {
                    PyObject* obj = as_ptr(e);
                    if (!is_immortal(obj)) Py_DECREF(obj);
                    break;
                }
#if SIZEOF_VOID_P >= 8
                case TAG_BIND:
                    break;
#endif
#if SIZEOF_VOID_P >= 8
                case TAG_NEW_PATCHED: {
                    (void)consume_borrowed_ptr();
                    break;
                }
#endif
                case TAG_COMMAND:
                    switch (cmd_of(e)) {
                        case CMD_LIST:
                        case CMD_TUPLE:
                            for (uint32_t i = 0, n = len_of(e); i < n; i++) drain_value();
                            break;
                        case CMD_DICT:
                            for (uint32_t i = 0, n = len_of(e) * 2; i < n; i++) drain_value();
                            break;
                        case CMD_HEARTBEAT:
                        case CMD_SERIALIZE_ERROR:
                            drain_value();
                            break;
                        case CMD_PICKLED:
                        case CMD_NEW_HANDLE:
                            drain_value();
                            break;
                        case CMD_BIND:
                            (void)consume_borrowed_ptr();
                            break;
                        case CMD_NEW_PATCHED:
                            (void)consume_borrowed_ptr();
                            (void)consume_borrowed_ptr();
                            break;
                        default: break;
                    }
                    break;
                default: break;
            }
        }

        void drain_queue_entries() {
            if (!queue) return;
            while (auto* ep = queue->front()) {
                QEntry e = *ep;
                queue->pop();
                switch (tag_of(e)) {
                    case TAG_OBJECT:
#if SIZEOF_VOID_P >= 8
                    case TAG_PICKLED:
                    case TAG_NEW_HANDLE:
#endif
                    {
                        PyObject* obj = as_ptr(e);
                        if (!is_immortal(obj)) Py_DECREF(obj);
                        break;
                    }
#if SIZEOF_VOID_P >= 8
                    case TAG_BIND:
                        break;
#endif
#if SIZEOF_VOID_P >= 8
                    case TAG_NEW_PATCHED: {
                        (void)consume_borrowed_ptr();
                        break;
                    }
#endif
                    case TAG_DELETE:
                    case TAG_THREAD:
                        break;
                    case TAG_COMMAND:
                        switch (cmd_of(e)) {
                            case CMD_LIST:
                            case CMD_TUPLE:
                                for (uint32_t i = 0, n = len_of(e); i < n; i++) drain_value();
                                break;
                            case CMD_DICT:
                                for (uint32_t i = 0, n = len_of(e) * 2; i < n; i++) drain_value();
                                break;
                            case CMD_HEARTBEAT:
                            case CMD_SERIALIZE_ERROR:
                                drain_value();
                                break;
                            case CMD_PICKLED:
                            case CMD_NEW_HANDLE:
                                drain_value();
                                break;
                            case CMD_BIND:
                                (void)consume_borrowed_ptr();
                                break;
                            case CMD_NEW_PATCHED:
                                (void)consume_borrowed_ptr();
                                (void)consume_borrowed_ptr();
                                break;
                            default: break;
                        }
                        break;
                    default: break;
                }
            }
        }

        void drain_return_queue() {
            if (!return_queue) return;
            while (auto* ep = return_queue->front()) {
                PyObject* obj = *ep;
                return_queue->pop();
                if (total_removed_ptr)
                    total_removed_ptr->fetch_add(estimate_size(obj), std::memory_order_relaxed);
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

            if (queue) {
                delete queue;
                queue = nullptr;
            }
            if (return_queue) {
                delete return_queue;
                return_queue = nullptr;
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
                self->return_queue = nullptr;
                self->stream = nullptr;
                self->total_removed_ptr = nullptr;
                self->writer_key = nullptr;
                self->last_tstate = nullptr;
                self->thread_cache = nullptr;
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
                                         std::atomic<int64_t>* total_removed,
                                         PyObject* writer_key,
                                         bool quit_on_error) {
        if (Py_TYPE(persister) != &AsyncFilePersister_Type) {
            PyErr_SetString(PyExc_TypeError, "expected AsyncFilePersister");
            return {nullptr, nullptr};
        }
        return ((AsyncFilePersister*)persister)->setup(serializer, queue_capacity,
                                                       return_queue_capacity, total_removed,
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
}
