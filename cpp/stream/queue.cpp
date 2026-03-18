#include "queue.h"
#include "consumer.h"
#include "queueentry.h"

#include <algorithm>
#include <chrono>
#include <new>
#include <thread>

namespace retracesoftware_stream {


    namespace {
        inline int64_t queue_estimate_size(PyObject* obj) {
            return queue_is_immortal(obj) ? 0 : (int64_t)approximate_size_bytes(obj);
        }

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

        PyObject* Queue_push_fail_callback_getter(Queue* self, void*) {
            return Py_NewRef(self->push_fail_handler());
        }

        int Queue_push_fail_callback_setter(Queue* self, PyObject* value, void*) {
            if (value == nullptr) {
                PyErr_SetString(PyExc_AttributeError, "deletion of 'push_fail_callback' is not allowed");
                return -1;
            }
            self->set_push_fail_handler(value);
            return PyErr_Occurred() ? -1 : 0;
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
            {"push_fail_callback", (getter)Queue_push_fail_callback_getter, (setter)Queue_push_fail_callback_setter, (char*)"Retry callback used when a logical push stalls", nullptr},
            {nullptr}
        };
    }

    int Queue_init(Queue* self, PyObject* args, PyObject* kwds) {
        PyObject* persister = Py_None;
        PyObject* thread = Py_None;
        PyObject* push_fail_callback = Py_None;
        Py_ssize_t queue_capacity = 65536;
        long long inflight_limit = 128LL * 1024 * 1024;
        int consumer_wait_timeout_ms = 100;

        static const char* kwlist[] = {
            "persister",
            "thread",
            "push_fail_callback",
            "queue_capacity",
            "inflight_limit",
            "consumer_wait_timeout_ms",
            nullptr
        };

        if (!PyArg_ParseTupleAndKeywords(args, kwds, "|OOOnLi", (char**)kwlist,
                                         &persister,
                                         &thread,
                                         &push_fail_callback,
                                         &queue_capacity,
                                         &inflight_limit,
                                         &consumer_wait_timeout_ms)) {
            return -1;
        }

        if (queue_capacity <= 0) {
            PyErr_SetString(PyExc_ValueError, "queue_capacity must be positive");
            return -1;
        }
        if (consumer_wait_timeout_ms < 0) {
            PyErr_SetString(PyExc_ValueError, "consumer_wait_timeout_ms must be non-negative");
            return -1;
        }
        if (thread != Py_None && !PyCallable_Check(thread)) {
            PyErr_SetString(PyExc_TypeError, "thread must be callable");
            return -1;
        }
        if (push_fail_callback != Py_None && !PyCallable_Check(push_fail_callback)) {
            PyErr_SetString(PyExc_TypeError, "push_fail_callback must be callable or None");
            return -1;
        }

        self->~Queue();
        new (self) Queue((size_t)queue_capacity,
                         (int64_t)inflight_limit,
                         consumer_wait_timeout_ms);
        if (thread != Py_None) self->thread_id_callback = Py_NewRef(thread);
        if (push_fail_callback != Py_None) self->push_fail_callback = Py_NewRef(push_fail_callback);
        if (persister != Py_None) {
            self->consumer = make_consumer(persister);
            self->resume();
        }
        return 0;
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
          return_notify_threshold_bytes(1),
          consumer_wait_timeout_ms_value(100),
          notify_threshold_entries(1) {}

    Queue::Queue(size_t capacity, int64_t inflight_limit, int consumer_wait_timeout_ms)
        : entries(capacity),
          returned(capacity),
          inflight_limit_bytes(inflight_limit),
          return_notify_threshold_bytes(
              inflight_limit > 0 ? std::max<int64_t>(1, inflight_limit / 4) : 1),
          consumer_wait_timeout_ms_value(consumer_wait_timeout_ms),
          notify_threshold_entries(std::max<size_t>(1, capacity / 2)) {}

    Queue::~Queue() {
        close();
        Py_CLEAR(thread_id_callback);
        Py_CLEAR(push_fail_callback);
        delete consumer;
    }

    bool Queue::push_command(Cmd cmd, uint32_t len) {
        return push(cmd_entry(cmd, len));
    }

    void Queue::prepare_consumer_resume() {
        if (consumer) consumer->prepare_resume();
    }

    void Queue::reset_consumer_state() {
        if (consumer) consumer->reset_state();
    }

    void Queue::flush_consumer() {
        if (consumer) consumer->flush_background();
    }

    bool Queue::has_entry_slots(size_t needed) const {
        return entries.capacity() - entries.size() >= needed;
    }

    bool Queue::wait_for_slots(size_t needed) {
        while (!has_entry_slots(needed)) {
            if (!wait_with_push_backoff()) return false;
        }
        return true;
    }

    bool Queue::wait_for_inflight() {
        while (inflight() >= inflight_limit_bytes) {
            drain_returned_with_gil(0);
            if (inflight() < inflight_limit_bytes) return true;
            maybe_notify_return_thread(false);
            if (!wait_with_push_backoff()) return false;
        }
        return true;
    }

    bool Queue::try_pop_returned(PyObject*& obj) {
        PyObject** slot = returned.front();
        if (!slot) return false;
        obj = *slot;
        returned.pop();
        return true;
    }

    bool Queue::has_returned_entries() const {
        return !returned.empty();
    }

    void Queue::maybe_notify_return_thread(bool force) {
        if (!return_thread_started) return;
        if (!return_thread_waiting.load(std::memory_order_acquire)) return;
        if (!force && inflight_limit_bytes > 0 && inflight() < return_notify_threshold_bytes) return;
        return_wake_cv.notify_one();
    }

    void Queue::drain_returned_all_with_gil() {
        PyObject* obj = nullptr;
        while (try_pop_returned(obj)) {
            note_removed(queue_estimate_size(obj));
            Py_DECREF(obj);
        }
    }

    bool Queue::drain_returned_with_gil(int64_t needed_size) {
        if (!PyGILState_Check()) return false;
        if (inflight_limit_bytes <= 0) return true;

        PyObject* obj = nullptr;
        while (inflight() + needed_size >= inflight_limit_bytes && try_pop_returned(obj)) {
            note_removed(queue_estimate_size(obj));
            Py_DECREF(obj);
        }
        return inflight() + needed_size < inflight_limit_bytes;
    }

    bool Queue::wait_with_push_backoff() {
        if (!push_fail_callback) return false;

        PyGILState_STATE gstate = PyGILState_Ensure();
        PyObject* result = PyObject_CallNoArgs(push_fail_callback);
        if (!result) {
            PyErr_Clear();
            PyGILState_Release(gstate);
            return false;
        }

        if (result == Py_None) {
            Py_DECREF(result);
            PyGILState_Release(gstate);
            return false;
        }

        double delay = PyFloat_AsDouble(result);
        Py_DECREF(result);
        if (delay < 0.0 && PyErr_Occurred()) {
            PyErr_Clear();
            PyGILState_Release(gstate);
            return false;
        }

        if (delay < 0.0) delay = 0.0;
        Py_BEGIN_ALLOW_THREADS
        std::this_thread::sleep_for(std::chrono::duration<double>(delay));
        Py_END_ALLOW_THREADS
        PyGILState_Release(gstate);
        return true;
    }

    void Queue::return_loop() {
        while (true) {
            if (!has_returned_entries()) {
                if (shutdown_flag.load(std::memory_order_acquire)) return;
                std::unique_lock<std::mutex> lock(return_wake_mutex);
                return_thread_waiting.store(true, std::memory_order_release);
                return_wake_cv.wait_for(lock,
                                        std::chrono::milliseconds(consumer_wait_timeout_ms_value),
                                        [this] {
                                            return shutdown_flag.load(std::memory_order_acquire)
                                                || has_returned_entries();
                                        });
                return_thread_waiting.store(false, std::memory_order_release);
                if (shutdown_flag.load(std::memory_order_acquire) && !has_returned_entries()) return;
                continue;
            }

            PyGILState_STATE gstate = PyGILState_Ensure();
            drain_returned_all_with_gil();
            PyGILState_Release(gstate);
        }
    }


    bool Queue::has_entries() {
        return entries.front() != nullptr;
    }

    bool Queue::try_consume() {
        QEntry entry;
        if (!try_pop_entry(entry)) return false;
        dispatch_entry(entry);
        return true;
    }

    void Queue::consume() {
        dispatch_entry(pop_entry());
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
        return_notify_threshold_bytes =
            value > 0 ? std::max<int64_t>(1, value / 4) : 1;
    }

    void Queue::close() {
        if (closed) return;
        closed = true;

        shutdown_flag.store(true, std::memory_order_release);
        wake_cv.notify_one();
        return_wake_cv.notify_one();
        if (writer_thread.joinable()) {
            Py_BEGIN_ALLOW_THREADS
            writer_thread.join();
            Py_END_ALLOW_THREADS
        }
        if (return_thread.joinable()) {
            Py_BEGIN_ALLOW_THREADS
            return_thread.join();
            Py_END_ALLOW_THREADS
        }

        thread_started = false;
        return_thread_started = false;
        drain_returned();
        release_entries();
        drain_returned();
        clear_thread_state();
        reset_consumer_state();
    }

    void Queue::drain() {
        if (closed || !thread_started) return;

        shutdown_flag.store(true, std::memory_order_release);
        wake_cv.notify_one();
        return_wake_cv.notify_one();
        if (writer_thread.joinable()) {
            Py_BEGIN_ALLOW_THREADS
            writer_thread.join();
            Py_END_ALLOW_THREADS
        }
        if (return_thread.joinable()) {
            Py_BEGIN_ALLOW_THREADS
            return_thread.join();
            Py_END_ALLOW_THREADS
        }

        drain_returned();
        thread_started = false;
        return_thread_started = false;
        shutdown_flag.store(false, std::memory_order_release);
        clear_thread_state();
    }

    void Queue::resume() {
        if (closed || thread_started || !consumer) return;
        clear_thread_state();
        prepare_consumer_resume();
        saw_shutdown = false;
        shutdown_flag.store(false, std::memory_order_release);
        consumer_waiting.store(false, std::memory_order_release);
        return_thread_waiting.store(false, std::memory_order_release);
        return_thread_started = true;
        thread_started = true;
        return_thread = std::thread(&Queue::return_loop, this);
        writer_thread = std::thread(&Queue::worker_loop, this);
    }

    PyObject* Queue::persister() const {
        return consumer ? consumer->target() : Py_None;
    }

    PyObject* Queue::push_fail_handler() const {
        return push_fail_callback ? push_fail_callback : Py_None;
    }

    void Queue::set_push_fail_handler(PyObject* callback) {
        if (callback != Py_None && !PyCallable_Check(callback)) {
            PyErr_SetString(PyExc_TypeError, "push_fail_callback must be callable or None");
            return;
        }
        PyObject* value = callback == Py_None ? nullptr : Py_NewRef(callback);
        Py_XSETREF(push_fail_callback, value);
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
        // which in turn unblocks the producer's inflight wait.
        if (PyGILState_Check()) {
            PyThreadState* _save = PyEval_SaveThread();
            while (!try_pop_entry(entry)) std::this_thread::yield();
            PyEval_RestoreThread(_save);
        } else {
            while (!try_pop_entry(entry)) std::this_thread::yield();
        }
        return entry;
    }

    void* Queue::consume_raw_ptr_payload() {
        QEntry entry = pop_entry();
        if (is_command_entry(entry)) {
            PyErr_SetString(PyExc_RuntimeError, "expected raw pointer payload");
            throw nullptr;
        }
        return as_payload_raw_ptr(entry);
    }

    PyObject* Queue::consume_owned_payload() {
        QEntry entry = pop_entry();
        if (is_command_entry(entry)) {
            PyErr_SetString(PyExc_RuntimeError, "expected owned object payload");
            throw nullptr;
        }
        return as_payload_obj(entry);
    }

    Ref Queue::consume_ref() {
        return reinterpret_cast<Ref>(consume_raw_ptr_payload());
    }

    void Queue::release_consumed_obj(PyObject* obj) {
        if (queue_is_immortal(obj)) return;
        note_removed(queue_estimate_size(obj));
        Py_DECREF(obj);
    }

    void Queue::clear_thread_state() {
        last_thread_tstate = nullptr;
        Py_CLEAR(last_thread_id);
    }

    void Queue::finish_consumed_obj(PyObject* obj) {
        if (queue_is_immortal(obj)) return;
        while (!returned.try_push(obj)) {
            maybe_notify_return_thread(true);
            std::this_thread::yield();
        }
        maybe_notify_return_thread(false);
    }

    void Queue::dispatch_command(QEntry entry) {
        switch (cmd_of(entry)) {
            case CMD_BIND:
                consumer->consume_bind(reinterpret_cast<Ref>(consume_raw_ptr_payload()));
                break;
            case CMD_INTERN: {
                PyObject* obj = consume_owned_payload();
                consumer->consume_intern(obj);
                finish_consumed_obj(obj);
                break;
            }
            case CMD_DELETE:
                consumer->consume_delete(reinterpret_cast<Ref>(consume_raw_ptr_payload()));
                break;
            case CMD_NEW_EXT_WRAPPED:
                consumer->consume_new_ext_wrapped(reinterpret_cast<PyTypeObject*>(consume_raw_ptr_payload()));
                break;
            case CMD_NEW_PATCHED: {
                PyObject* obj = consume_owned_payload();
                consumer->consume_new_patched(obj, Py_TYPE(obj));
                finish_consumed_obj(obj);
                break;
            }
            case CMD_THREAD_SWITCH: {
                PyObject* obj = consume_owned_payload();
                consumer->consume_thread_switch(obj);
                finish_consumed_obj(obj);
                break;
            }
            case CMD_FLUSH:
                consumer->consume_flush();
                break;
            case CMD_SHUTDOWN:
                saw_shutdown = true;
                consumer->consume_shutdown();
                break;
            case CMD_LIST:
                consumer->consume_list(len_of(entry));
                for (uint32_t i = 0; i < len_of(entry); i++) consume();
                break;
            case CMD_TUPLE:
                consumer->consume_tuple(len_of(entry));
                for (uint32_t i = 0; i < len_of(entry); i++) consume();
                break;
            case CMD_DICT:
                consumer->consume_dict(len_of(entry));
                for (uint32_t i = 0; i < len_of(entry); i++) {
                    consume();
                    consume();
                }
                break;
            case CMD_HEARTBEAT:
                consumer->consume_heartbeat();
                break;
            default:
                PyErr_SetString(PyExc_RuntimeError, "unexpected command entry");
                throw nullptr;
        }
    }

    void Queue::dispatch_entry(QEntry entry) {
        if (!is_command_entry(entry)) {
            switch (pointer_kind_of(entry)) {
                case PTR_OBJECT: {
                    PyObject* obj = as_object(entry);
                    consumer->consume_object(obj);
                    finish_consumed_obj(obj);
                    break;
                }
                case PTR_REF:
                    consumer->consume_ref(as_ref(entry));
                    break;
                case PTR_IMMORTAL:
                    consumer->consume_object(as_object(entry));
                    break;
                case PTR_ESCAPED:
                    PyErr_SetString(PyExc_RuntimeError, "unexpected escaped pointer entry");
                    throw nullptr;
            }
            return;
        }
        dispatch_command(entry);
    }

    void Queue::dispatch_release(QEntry entry) {
        if (!is_command_entry(entry)) {
            switch (pointer_kind_of(entry)) {
                case PTR_OBJECT:
                    release_consumed_obj(as_object(entry));
                    break;
                case PTR_REF:
                case PTR_IMMORTAL:
                    break;
                case PTR_ESCAPED:
                    PyErr_SetString(PyExc_RuntimeError, "unexpected escaped pointer entry");
                    throw nullptr;
            }
            return;
        }

        switch (cmd_of(entry)) {
            case CMD_BIND:
            case CMD_DELETE:
            case CMD_NEW_EXT_WRAPPED:
                (void)consume_raw_ptr_payload();
                break;
            case CMD_INTERN:
            case CMD_NEW_PATCHED:
            case CMD_THREAD_SWITCH:
                release_consumed_obj(consume_owned_payload());
                break;
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
                break;
            default:
                PyErr_SetString(PyExc_RuntimeError, "unexpected command entry");
                throw nullptr;
        }
    }

    void Queue::worker_loop() {
        while (true) {
            if (!has_entries()) {
                if (shutdown_flag.load(std::memory_order_acquire)) return;
                std::unique_lock<std::mutex> lock(wake_mutex);
                consumer_waiting.store(true, std::memory_order_release);
                wake_cv.wait_for(lock,
                                 std::chrono::milliseconds(consumer_wait_timeout_ms_value),
                                 [this] {
                                     return shutdown_flag.load(std::memory_order_acquire) || has_entries();
                                 });
                consumer_waiting.store(false, std::memory_order_release);
                if (shutdown_flag.load(std::memory_order_acquire) && !has_entries()) return;
                continue;
            }

            while (try_consume()) {
                if (saw_shutdown) {
                    maybe_notify_return_thread(true);
                    return;
                }
            }
            flush_consumer();
            maybe_notify_return_thread(false);
        }
    }

    void Queue::drain_returned() {
        PyGILState_STATE gstate = PyGILState_Ensure();
        drain_returned_all_with_gil();
        PyGILState_Release(gstate);
    }

    void Queue::release_entries() {
        QEntry entry;
        while (try_pop_entry(entry)) {
            dispatch_release(entry);
        }
    }

}
