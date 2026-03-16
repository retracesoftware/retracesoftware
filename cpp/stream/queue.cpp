#include "queue.h"

#include <chrono>
#include <new>
#include <thread>

namespace retracesoftware_stream {
    namespace {
        PyObject* Queue_inflight_bytes_getter(Queue* self, void*) {
            return PyLong_FromLongLong(self->inflight());
        }

        PyObject* Queue_inflight_limit_getter(Queue* self, void*) {
            return PyLong_FromLongLong(self->inflight_limit());
        }

        int Queue_inflight_limit_setter(Queue* self, PyObject* value, void*) {
            if (value == nullptr) {
                PyErr_SetString(PyExc_AttributeError, "deletion of 'inflight_limit' is not allowed");
                return -1;
            }
            long long limit = PyLong_AsLongLong(value);
            if (limit == -1 && PyErr_Occurred()) return -1;
            self->set_inflight_limit(limit);
            return 0;
        }

        PyObject* Queue_persister_getter(Queue* self, void*) {
            return Py_NewRef(self->persister());
        }

        PyObject* Queue_py_close(Queue* self, PyObject*) {
            self->close();
            Py_RETURN_NONE;
        }

        PyObject* Queue_py_drain(Queue* self, PyObject*) {
            self->drain();
            Py_RETURN_NONE;
        }

        PyObject* Queue_py_resume(Queue* self, PyObject*) {
            self->resume();
            Py_RETURN_NONE;
        }

        PyObject* Queue_tp_new(PyTypeObject* type, PyObject*, PyObject*) {
            Queue* self = reinterpret_cast<Queue*>(type->tp_alloc(type, 0));
            if (!self) return nullptr;
            new (self) Queue();
            return reinterpret_cast<PyObject*>(self);
        }

        int Queue_init(Queue* self, PyObject* args, PyObject* kwds) {
            PyObject* persister = Py_None;
            Py_ssize_t queue_capacity = 65536;
            Py_ssize_t return_queue_capacity = 131072;
            long long inflight_limit = 128LL * 1024 * 1024;
            int stall_timeout = 5;

            static const char* kwlist[] = {
                "persister",
                "queue_capacity",
                "return_queue_capacity",
                "inflight_limit",
                "stall_timeout",
                nullptr
            };

            if (!PyArg_ParseTupleAndKeywords(args, kwds, "|OnnLi", (char**)kwlist,
                                             &persister,
                                             &queue_capacity,
                                             &return_queue_capacity,
                                             &inflight_limit,
                                             &stall_timeout)) {
                return -1;
            }

            if (queue_capacity <= 0 || return_queue_capacity <= 0) {
                PyErr_SetString(PyExc_ValueError, "queue capacities must be positive");
                return -1;
            }
            if (stall_timeout < 0) {
                PyErr_SetString(PyExc_ValueError, "stall_timeout must be non-negative");
                return -1;
            }

            self->~Queue();
            new (self) Queue((size_t)queue_capacity,
                             (size_t)return_queue_capacity,
                             (int64_t)inflight_limit,
                             stall_timeout);
            if (persister != Py_None) {
                self->persister_obj = Py_NewRef(persister);
                try {
                    self->consumer = new Consumer(*self, self->persister_obj);
                } catch (...) {
                    Py_CLEAR(self->persister_obj);
                    PyErr_SetString(PyExc_RuntimeError, "failed to create queue consumer");
                    return -1;
                }
            }
            return 0;
        }

        void Queue_dealloc(Queue* self) {
            self->~Queue();
            Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
        }

        PyMethodDef Queue_methods[] = {
            {"close", (PyCFunction)Queue_py_close, METH_NOARGS, (char*)"Stop queue workers and release the persister"},
            {"drain", (PyCFunction)Queue_py_drain, METH_NOARGS, (char*)"Drain queue workers without closing the queue"},
            {"resume", (PyCFunction)Queue_py_resume, METH_NOARGS, (char*)"Resume queue workers for the configured persister"},
            {nullptr}
        };

        PyGetSetDef Queue_getset[] = {
            {"inflight_bytes", (getter)Queue_inflight_bytes_getter, nullptr, (char*)"Current estimated bytes in flight", nullptr},
            {"inflight_limit", (getter)Queue_inflight_limit_getter, (setter)Queue_inflight_limit_setter, (char*)"Maximum bytes allowed in flight", nullptr},
            {"persister", (getter)Queue_persister_getter, nullptr, (char*)"Attached persister sink", nullptr},
            {nullptr}
        };
    }

    PyTypeObject Queue_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = "retracesoftware_stream.Queue",
        .tp_basicsize = sizeof(Queue),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)Queue_dealloc,
        .tp_flags = Py_TPFLAGS_DEFAULT,
        .tp_doc = "Stream queue object",
        .tp_methods = Queue_methods,
        .tp_getset = Queue_getset,
        .tp_init = (initproc)Queue_init,
        .tp_new = Queue_tp_new,
    };

    Queue* Queue_get(PyObject* obj) {
        if (!obj || !PyObject_TypeCheck(obj, &Queue_Type)) {
            PyErr_SetString(PyExc_TypeError, "expected Queue");
            return nullptr;
        }
        return reinterpret_cast<Queue*>(obj);
    }

    Queue::Queue()
        : entries(1),
          returned(1),
          inflight_limit_bytes(0),
          stall_timeout_seconds_value(0) {}

    Queue::Queue(size_t capacity, size_t return_capacity,
                 int64_t inflight_limit, int stall_timeout_seconds)
        : entries(capacity),
          returned(return_capacity),
          inflight_limit_bytes(inflight_limit),
          stall_timeout_seconds_value(stall_timeout_seconds) {}

    Queue::~Queue() {
        close();

        while (auto* ep = returned.front()) {
            PyObject* obj = *ep;
            returned.pop();
            note_removed(estimate_size(obj));
            Py_DECREF(obj);
        }

        release_entries();

        Py_CLEAR(persister_obj);
        delete this->consumer;
        this->consumer = nullptr;
    }

    bool Queue::push_command(Cmd cmd, uint32_t len) {
        return push_entry(cmd_entry(cmd, len));
    }

    bool Queue::push_flush() {
        return push_command(CMD_FLUSH);
    }

    bool Queue::push_shutdown() {
        return push_command(CMD_SHUTDOWN);
    }

    bool Queue::push_immortal(PyObject* obj) {
        return push_pointer_entry(obj, PTR_IMMORTAL);
    }

    bool Queue::push_bind(Ref ref) {
        return push_pointer_entry(ref, PTR_BIND);
    }

    bool Queue::push_delete(Ref ref) {
        return push_command(CMD_DELETE) &&
               push_raw_ptr_payload(ref);
    }

    bool Queue::push_thread(PyThreadState* tstate) {
        return push_command(CMD_THREAD) &&
               push_raw_ptr_payload(tstate);
    }

    bool Queue::push_new_patched(PyObject* obj, PyTypeObject* type) {
        return push_command(CMD_NEW_PATCHED) &&
               push_owned_payload(obj, estimate_size(obj)) &&
               push_raw_ptr_payload(type);
    }

    bool Queue::push_ext_wrapped(PyTypeObject* type) {
        return push_pointer_entry(type, PTR_NEW_EXT_WRAPPED);
    }

    bool Queue::push_new_handle(Ref handle, PyObject* obj) {
        return push_command(CMD_NEW_HANDLE) &&
               push_raw_ptr_payload(handle) &&
               push_owned_payload(obj, estimate_size(obj));
    }

    bool Queue::push_heartbeat() {
        return push_command(CMD_HEARTBEAT);
    }

    bool Queue::push_serialize_error() {
        return push_command(CMD_SERIALIZE_ERROR);
    }

    bool Queue::push_pickled(PyObject* bytes_obj) {
        return push_command(CMD_PICKLED) &&
               push_owned_payload(bytes_obj, estimate_size(bytes_obj));
    }

    bool Queue::push_list_header(size_t len) {
        return push_command(CMD_LIST, (uint32_t)len);
    }

    bool Queue::push_tuple_header(size_t len) {
        return push_command(CMD_TUPLE, (uint32_t)len);
    }

    bool Queue::push_dict_header(size_t len) {
        return push_command(CMD_DICT, (uint32_t)len);
    }

    bool Queue::push_handle_ref(Ref ref) {
        return push_pointer_entry(ref, PTR_HANDLE_REF);
    }

    bool Queue::push_handle_delete(Ref ref) {
        return push_command(CMD_HANDLE_DELETE) &&
               push_raw_ptr_payload(ref);
    }

    bool Queue::push_bound_ref(Ref ref) {
        return push_pointer_entry(ref, PTR_BOUND_REF);
    }

    bool Queue::push_bound_ref_delete(Ref ref) {
        return push_pointer_entry(ref, PTR_BOUND_REF_DELETE);
    }

    bool Queue::has_entries() {
        return entries.front() != nullptr;
    }

    bool Queue::try_consume(Consumer& consumer) {
        QEntry entry;
        if (!try_pop_entry(entry)) return false;
        dispatch_entry(consumer, entry);
        return true;
    }

    void Queue::consume(Consumer& consumer) {
        dispatch_entry(consumer, pop_entry());
    }

    bool Queue::try_push_return(PyObject* obj) {
        return returned.try_push(obj);
    }

    PyObject** Queue::return_front() {
        return returned.front();
    }

    void Queue::return_pop() {
        returned.pop();
    }

    bool Queue::push_obj(PyObject* obj) {
        return push_owned_payload(obj, estimate_size(obj));
    }

    void Queue::note_removed(int64_t size) {
        total_removed.fetch_add(size, std::memory_order_relaxed);
    }

    int64_t Queue::inflight() const {
        return total_added - total_removed.load(std::memory_order_relaxed);
    }

    int64_t Queue::inflight_limit() const {
        return inflight_limit_bytes;
    }

    void Queue::set_inflight_limit(int64_t value) {
        inflight_limit_bytes = value;
    }

    void Queue::close() {
        if (consumer) consumer->close();
    }

    void Queue::drain() {
        if (consumer) consumer->drain();
    }

    void Queue::resume() {
        if (consumer) consumer->resume();
    }

    PyObject* Queue::persister() const {
        return persister_obj ? persister_obj : Py_None;
    }

    bool Queue::push_pointer_entry(void* ptr, PointerKind kind) {
        if (supports_inline_pointer_kind(ptr)) {
            switch (kind) {
                case PTR_OWNED_OBJECT:
                    return push_entry(owned_obj_entry(reinterpret_cast<PyObject*>(ptr)));
                case PTR_HANDLE_REF:
                    return push_entry(handle_ref_entry(reinterpret_cast<Ref>(ptr)));
                case PTR_BIND:
                    return push_entry(bind_entry(reinterpret_cast<PyObject*>(ptr)));
                case PTR_IMMORTAL:
                    break;
                case PTR_BOUND_REF:
                    return push_entry(bound_ref_entry(reinterpret_cast<Ref>(ptr)));
                case PTR_BOUND_REF_DELETE:
                    return push_entry(bound_ref_delete_entry(reinterpret_cast<Ref>(ptr)));
                case PTR_NEW_EXT_WRAPPED:
                    return push_entry(reinterpret_cast<QEntry>(ptr) | PTR_NEW_EXT_WRAPPED);
            }
        }

        return push_entry(escaped_ptr_entry(kind)) &&
               push_entry(payload_ptr_entry(ptr));
    }

    bool Queue::push_owned_payload(PyObject* obj, int64_t estimated_size) {
        if (!reserve_inflight(estimated_size)) return false;
        Py_INCREF(obj);
        if (push_pointer_entry(obj, PTR_OWNED_OBJECT)) return true;
        Py_DECREF(obj);
        release_reserved_inflight(estimated_size);
        return false;
    }

    bool Queue::push_raw_ptr_payload(void* ptr) {
        return push_entry(payload_ptr_entry(ptr));
    }

    bool Queue::try_push_entry(QEntry entry) {
        return entries.try_push(entry);
    }

    bool Queue::push_entry(QEntry entry) {
        if (try_push_entry(entry)) return true;

        bool ok = false;
        Py_BEGIN_ALLOW_THREADS
        auto deadline = std::chrono::steady_clock::now()
                      + std::chrono::seconds(stall_timeout_seconds_value);
        while (true) {
            if (try_push_entry(entry)) { ok = true; break; }
            if (std::chrono::steady_clock::now() >= deadline) break;
            std::this_thread::yield();
        }
        Py_END_ALLOW_THREADS
        return ok;
    }

    bool Queue::try_pop_entry(QEntry& entry) {
        QEntry* ep = entries.front();
        if (!ep) return false;
        entry = *ep;
        entries.pop();
        return true;
    }

    QEntry Queue::pop_entry() {
        QEntry entry;
        if (try_pop_entry(entry)) return entry;

        // Queue empty while mid-compound-value: release the GIL so the
        // return thread can drain its queue and update total_removed,
        // which in turn unblocks the producer's wait_for_inflight().
        PyThreadState* _save = PyEval_SaveThread();
        while (!try_pop_entry(entry)) std::this_thread::yield();
        PyEval_RestoreThread(_save);
        return entry;
    }

    void* Queue::consume_raw_ptr_payload() {
        return as_payload_raw_ptr(pop_entry());
    }

    PyObject* Queue::consume_owned_payload() {
        QEntry entry = pop_entry();
        if (is_escaped_pointer_entry(entry)) {
            if (escaped_pointer_kind_of(entry) != PTR_OWNED_OBJECT) {
                PyErr_SetString(PyExc_RuntimeError, "expected owned object payload");
                throw nullptr;
            }
            return as_payload_obj(pop_entry());
        }
        if (is_command_entry(entry)) {
            PyErr_SetString(PyExc_RuntimeError, "expected owned object payload");
            throw nullptr;
        }
        if (pointer_kind_of(entry) != PTR_OWNED_OBJECT) {
            PyErr_SetString(PyExc_RuntimeError, "expected inline owned object payload");
            throw nullptr;
        }
        return as_owned_obj(entry);
    }

    PyThreadState* Queue::consume_tstate() {
        return reinterpret_cast<PyThreadState*>(consume_raw_ptr_payload());
    }

    Ref Queue::consume_ref() {
        return reinterpret_cast<Ref>(consume_raw_ptr_payload());
    }

    void Queue::release_consumed_obj(PyObject* obj) {
        if (is_immortal(obj)) return;
        note_removed(estimate_size(obj));
        Py_DECREF(obj);
    }

    void Queue::finish_consumed_obj(Consumer& consumer, PyObject* obj) {
        if (!consumer.return_consumed_objects()) {
            release_consumed_obj(obj);
            return;
        }
        if (is_immortal(obj)) return;
        if (!returned.try_push(obj)) {
            release_consumed_obj(obj);
        }
    }

    void Queue::dispatch_entry(Consumer& consumer, QEntry entry) {
        if (!is_extended_entry(entry)) {
            switch (pointer_kind_of(entry)) {
                case PTR_OWNED_OBJECT: {
                    PyObject* obj = as_owned_obj(entry);
                    consumer.consume_object(obj);
                    finish_consumed_obj(consumer, obj);
                    break;
                }
                case PTR_HANDLE_REF:
                    consumer.consume_handle_ref(as_handle_ref(entry));
                    break;
                case PTR_BIND: {
                    consumer.consume_bind(reinterpret_cast<Ref>(as_bind_obj(entry)));
                    break;
                }
                case PTR_IMMORTAL:
                    PyErr_SetString(PyExc_RuntimeError, "unexpected inline immortal payload");
                    throw nullptr;
                case PTR_BOUND_REF:
                    consumer.consume_bound_ref(as_bound_ref(entry));
                    break;
                case PTR_BOUND_REF_DELETE:
                    consumer.consume_bound_ref_delete(as_bound_ref_delete(entry));
                    break;
                case PTR_NEW_EXT_WRAPPED:
                    consumer.consume_new_ext_wrapped(reinterpret_cast<PyTypeObject*>(entry & ~POINTER_KIND_MASK));
                    break;
            }
            return;
        }

        if (!is_command_entry(entry)) {
            PointerKind kind = escaped_pointer_kind_of(entry);
            void* ptr = as_payload_raw_ptr(pop_entry());
            switch (kind) {
                case PTR_OWNED_OBJECT: {
                    PyObject* obj = reinterpret_cast<PyObject*>(ptr);
                    consumer.consume_object(obj);
                    finish_consumed_obj(consumer, obj);
                    break;
                }
                case PTR_HANDLE_REF:
                    consumer.consume_handle_ref(reinterpret_cast<Ref>(ptr));
                    break;
                case PTR_BIND: {
                    consumer.consume_bind(reinterpret_cast<Ref>(ptr));
                    break;
                }
                case PTR_IMMORTAL:
                    consumer.consume_object(reinterpret_cast<PyObject*>(ptr));
                    break;
                case PTR_BOUND_REF:
                    consumer.consume_bound_ref(reinterpret_cast<Ref>(ptr));
                    break;
                case PTR_BOUND_REF_DELETE:
                    consumer.consume_bound_ref_delete(reinterpret_cast<Ref>(ptr));
                    break;
                case PTR_NEW_EXT_WRAPPED:
                    consumer.consume_new_ext_wrapped(reinterpret_cast<PyTypeObject*>(ptr));
                    break;
            }
            return;
        }

        switch (cmd_of(entry)) {
            case CMD_FLUSH:
                consumer.consume_flush();
                break;
            case CMD_SHUTDOWN:
                consumer.consume_shutdown();
                break;
            case CMD_LIST:
                consumer.consume_list(*this, len_of(entry));
                break;
            case CMD_TUPLE:
                consumer.consume_tuple(*this, len_of(entry));
                break;
            case CMD_DICT:
                consumer.consume_dict(*this, len_of(entry));
                break;
            case CMD_HEARTBEAT:
                consumer.consume_heartbeat(*this);
                break;
            case CMD_HANDLE_DELETE:
                consumer.consume_handle_delete(consume_ref());
                break;
            case CMD_DELETE: {
                consumer.consume_delete(consume_ref());
                break;
            }
            case CMD_THREAD:
                consumer.consume_thread(consume_tstate());
                break;
            case CMD_PICKLED: {
                PyObject* obj = consume_owned_payload();
                consumer.consume_pickled(obj);
                finish_consumed_obj(consumer, obj);
                break;
            }
            case CMD_NEW_HANDLE: {
                Ref ref = consume_ref();
                PyObject* obj = consume_owned_payload();
                consumer.consume_new_handle(ref, obj);
                finish_consumed_obj(consumer, obj);
                break;
            }
            case CMD_NEW_PATCHED: {
                PyObject* obj = consume_owned_payload();
                PyObject* type = reinterpret_cast<PyObject*>(consume_raw_ptr_payload());
                consumer.consume_new_patched(obj, type);
                finish_consumed_obj(consumer, obj);
                break;
            }
            case CMD_SERIALIZE_ERROR:
                consumer.consume_serialize_error(*this);
                break;
        }
    }

    void Queue::dispatch_release(QEntry entry) {
        if (!is_extended_entry(entry)) {
            switch (pointer_kind_of(entry)) {
                case PTR_OWNED_OBJECT:
                    release_consumed_obj(as_owned_obj(entry));
                    break;
                case PTR_HANDLE_REF:
                case PTR_BIND:
                case PTR_BOUND_REF:
                case PTR_BOUND_REF_DELETE:
                case PTR_NEW_EXT_WRAPPED:
                    break;
                case PTR_IMMORTAL:
                    PyErr_SetString(PyExc_RuntimeError, "unexpected inline immortal payload");
                    throw nullptr;
            }
            return;
        }

        if (!is_command_entry(entry)) {
            PointerKind kind = escaped_pointer_kind_of(entry);
            void* ptr = as_payload_raw_ptr(pop_entry());
            switch (kind) {
                case PTR_OWNED_OBJECT:
                    release_consumed_obj(reinterpret_cast<PyObject*>(ptr));
                    break;
                case PTR_IMMORTAL:
                case PTR_HANDLE_REF:
                case PTR_BIND:
                case PTR_BOUND_REF:
                case PTR_BOUND_REF_DELETE:
                case PTR_NEW_EXT_WRAPPED:
                    break;
            }
            return;
        }

        switch (cmd_of(entry)) {
            case CMD_FLUSH:
            case CMD_SHUTDOWN:
                break;
            case CMD_LIST:
                for (uint32_t i = 0; i < len_of(entry); i++) dispatch_release(pop_entry());
                break;
            case CMD_TUPLE:
                for (uint32_t i = 0; i < len_of(entry); i++) dispatch_release(pop_entry());
                break;
            case CMD_DICT:
                for (uint32_t i = 0; i < len_of(entry); i++) {
                    dispatch_release(pop_entry());
                    dispatch_release(pop_entry());
                }
                break;
            case CMD_HEARTBEAT:
                dispatch_release(pop_entry());
                break;
            case CMD_HANDLE_DELETE:
            case CMD_DELETE:
                consume_ref();
                break;
            case CMD_THREAD:
                consume_tstate();
                break;
            case CMD_PICKLED:
                release_consumed_obj(consume_owned_payload());
                break;
            case CMD_NEW_HANDLE:
                consume_ref();
                release_consumed_obj(consume_owned_payload());
                break;
            case CMD_NEW_PATCHED:
                release_consumed_obj(consume_owned_payload());
                consume_raw_ptr_payload();
                break;
            case CMD_SERIALIZE_ERROR:
                dispatch_release(pop_entry());
                break;
        }
    }

    void Queue::release_entries() {
        QEntry entry;
        while (try_pop_entry(entry)) {
            dispatch_release(entry);
        }
    }

    bool Queue::reserve_inflight(int64_t size) {
        if (!wait_for_inflight()) return false;
        total_added += size;
        return true;
    }

    void Queue::release_reserved_inflight(int64_t size) {
        total_added -= size;
    }

    bool Queue::wait_for_inflight() {
        if (inflight() <= inflight_limit_bytes) return true;

        bool ok = false;
        Py_BEGIN_ALLOW_THREADS
        auto deadline = std::chrono::steady_clock::now()
                    + std::chrono::seconds(stall_timeout_seconds_value);
        while (true) {
            if (total_added - total_removed.load(std::memory_order_relaxed) <= inflight_limit_bytes)
                { ok = true; break; }
            if (std::chrono::steady_clock::now() >= deadline) break;
            std::this_thread::yield();
        }
        Py_END_ALLOW_THREADS
        return ok;
    }
}
