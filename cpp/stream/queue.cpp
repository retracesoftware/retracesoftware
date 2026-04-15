#include "queue.h"
#include "gilguard.h"
#include "queueentry.h"

#include <algorithm>
#include <chrono>
#include <new>
#include <optional>
#include <thread>

namespace retracesoftware_stream {

    class Persister;

    extern bool Persister_write_heartbeat(Persister* persister);
    extern bool Persister_write_delete_handle(Persister* persister, BindingHandle handle);
    extern bool Persister_start_collection(Persister* persister, PyObject* type, size_t len);
    extern bool Persister_write_binding_lookup(Persister* persister, BindingHandle handle);
    extern bool Persister_write_object(Persister* persister, PyObject* obj);
    extern bool Persister_intern_handle(Persister* persister, PyObject* obj, BindingHandle handle);
    extern bool Persister_bind_handle(Persister* persister, BindingHandle handle);

    namespace {
        // Keep the minimum capacity large enough that multi-entry logical
        // commands such as CMD_INTERN can be enqueued atomically.
        constexpr size_t kMinQueueCapacity = 5;

        inline size_t clamp_queue_capacity(size_t capacity) {
            return std::max(capacity, kMinQueueCapacity);
        }

        inline int64_t queue_estimate_size(PyObject* obj) {
            return queue_is_immortal(obj) ? 0 : (int64_t)approximate_size_bytes(obj);
        }

        PyObject* collection_type_object(Cmd cmd) {
            switch (cmd) {
                case CMD_LIST:
                    return reinterpret_cast<PyObject*>(&PyList_Type);
                case CMD_TUPLE:
                    return reinterpret_cast<PyObject*>(&PyTuple_Type);
                case CMD_DICT:
                    return reinterpret_cast<PyObject*>(&PyDict_Type);
                default:
                    return nullptr;
            }
        }

        PyObject* size_to_pyobject(size_t len) {
            return PyLong_FromUnsignedLongLong(static_cast<unsigned long long>(len));
        }

        PyObject* handle_to_pyobject(BindingHandle handle) {
            return PyLong_FromUnsignedLongLong(static_cast<unsigned long long>(handle));
        }

        inline Persister* native_persister(PyObject* target) {
            return reinterpret_cast<Persister*>(target);
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

        PyObject* Queue_on_target_error_getter(Queue* self, void*) {
            return Py_NewRef(self->target_error_handler());
        }

        int Queue_on_target_error_setter(Queue* self, PyObject* value, void*) {
            if (value == nullptr) {
                PyErr_SetString(PyExc_AttributeError, "deletion of 'on_target_error' is not allowed");
                return -1;
            }
            self->set_target_error_handler(value);
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
            {"on_target_error", (getter)Queue_on_target_error_getter, (setter)Queue_on_target_error_setter, (char*)"Callback invoked when a target failure shuts the queue down", nullptr},
            {nullptr}
        };
    }

    int Queue_init(Queue* self, PyObject* args, PyObject* kwds) {
        PyObject* persister = Py_None;
        PyObject* thread = Py_None;
        PyObject* push_fail_callback = Py_None;
        PyObject* on_target_error = Py_None;
        Py_ssize_t queue_capacity = 65536;
        long long inflight_limit = 128LL * 1024 * 1024;
        int worker_wait_timeout_ms = 100;

        static const char* kwlist[] = {
            "persister",
            "thread",
            "push_fail_callback",
            "on_target_error",
            "queue_capacity",
            "inflight_limit",
            "worker_wait_timeout_ms",
            nullptr
        };

        if (!PyArg_ParseTupleAndKeywords(args, kwds, "|OOOOnLi", (char**)kwlist,
                                         &persister,
                                         &thread,
                                         &push_fail_callback,
                                         &on_target_error,
                                         &queue_capacity,
                                         &inflight_limit,
                                         &worker_wait_timeout_ms)) {
            return -1;
        }

        if (queue_capacity <= 0) {
            PyErr_SetString(PyExc_ValueError, "queue_capacity must be positive");
            return -1;
        }
        if (worker_wait_timeout_ms < 0) {
            PyErr_SetString(PyExc_ValueError, "worker_wait_timeout_ms must be non-negative");
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
        if (on_target_error != Py_None && !PyCallable_Check(on_target_error)) {
            PyErr_SetString(PyExc_TypeError, "on_target_error must be callable or None");
            return -1;
        }

        self->~Queue();
        new (self) Queue((size_t)queue_capacity,
                         (int64_t)inflight_limit,
                         worker_wait_timeout_ms);
        if (push_fail_callback != Py_None) self->push_fail_callback = Py_NewRef(push_fail_callback);
        if (on_target_error != Py_None) self->on_target_error = Py_NewRef(on_target_error);
        if (persister != Py_None) {
            if (!self->bind_target(persister)) {
                return -1;
            }
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
        : Queue(kMinQueueCapacity, 0, 100) {}

    Queue::Queue(size_t capacity, int64_t inflight_limit, int worker_wait_timeout_ms)
        : entries(clamp_queue_capacity(capacity)),
          returned(clamp_queue_capacity(capacity)),
          inflight_limit_bytes(inflight_limit),
          return_notify_threshold_bytes(
              inflight_limit > 0 ? std::max<int64_t>(1, inflight_limit / 4) : 1),
          worker_wait_timeout_ms_value(worker_wait_timeout_ms),
          notify_threshold_entries(std::max<size_t>(1, clamp_queue_capacity(capacity) / 2)) {}

    bool Queue::is_persister() const {
        return target_obj && PyObject_TypeCheck(target_obj, &Persister_Type);
    }

    Queue::~Queue() {
        close();
        Py_CLEAR(push_fail_callback);
        Py_CLEAR(on_target_error);
        clear_target();
    }

    bool Queue::push_command(Cmd cmd, uint32_t len) {
        return push(cmd_entry(cmd, len));
    }

    bool Queue::bind_target(PyObject* target) {
        clear_target();
        if (!target || target == Py_None) return true;

        target_obj = Py_NewRef(target);
        return true;
    }

    void Queue::clear_target() {
        Py_CLEAR(target_obj);
        has_target_error_message = false;
        last_target_error_message.clear();
    }

    void Queue::capture_current_target_error() {
        has_target_error_message = true;
        last_target_error_message = take_pending_error_message();
    }

    std::string Queue::take_target_error_message() {
        has_target_error_message = false;
        std::string message = last_target_error_message.empty() ? "target error" : last_target_error_message;
        last_target_error_message.clear();
        return message;
    }

    bool Queue::has_target_error() const {
        return has_target_error_message;
    }

    namespace {
        PyObject* lookup_target_method(PyObject* target, const char* name) {
            PyObject* method = PyObject_GetAttrString(target, name);
            if (!method) {
                PyErr_Clear();
                return nullptr;
            }
            return method;
        }
    }

    bool Queue::call_target_bind(BindingHandle handle) {
        if (is_persister()) {
            return Persister_bind_handle(native_persister(target_obj), handle);
        }
        retracesoftware::GILGuard gstate;
        PyObject* method = lookup_target_method(target_obj, "bind");
        if (!method) {
            PyErr_SetString(PyExc_AttributeError, "target is missing 'bind'");
            capture_current_target_error();
            return false;
        }
        PyObject* handle_obj = handle_to_pyobject(handle);
        if (!handle_obj) {
            Py_DECREF(method);
            capture_current_target_error();
            return false;
        }
        PyObject* result = PyObject_CallFunctionObjArgs(method, handle_obj, nullptr);
        Py_DECREF(handle_obj);
        Py_DECREF(method);
        if (!result) {
            capture_current_target_error();
            return false;
        }
        Py_DECREF(result);
        return true;
    }

    bool Queue::call_target_delete(BindingHandle handle) {
        if (is_persister()) {
            return Persister_write_delete_handle(native_persister(target_obj), handle);
        }
        retracesoftware::GILGuard gstate;
        PyObject* method = lookup_target_method(target_obj, "write_delete");
        if (!method) {
            PyErr_SetString(PyExc_AttributeError, "target is missing 'write_delete'");
            capture_current_target_error();
            return false;
        }
        PyObject* handle_obj = handle_to_pyobject(handle);
        if (!handle_obj) {
            Py_DECREF(method);
            capture_current_target_error();
            return false;
        }
        PyObject* result = PyObject_CallFunctionObjArgs(method, handle_obj, nullptr);
        Py_DECREF(handle_obj);
        Py_DECREF(method);
        if (!result) {
            capture_current_target_error();
            return false;
        }
        Py_DECREF(result);
        return true;
    }

    bool Queue::call_target_intern(PyObject* obj, BindingHandle handle) {
        if (is_persister()) {
            return Persister_intern_handle(native_persister(target_obj), obj, handle);
        }
        retracesoftware::GILGuard gstate;
        PyObject* method = lookup_target_method(target_obj, "intern");
        if (!method) {
            PyErr_SetString(PyExc_AttributeError, "target is missing 'intern'");
            capture_current_target_error();
            return false;
        }
        PyObject* handle_obj = handle_to_pyobject(handle);
        if (!handle_obj) {
            Py_DECREF(method);
            capture_current_target_error();
            return false;
        }
        PyObject* result = PyObject_CallFunctionObjArgs(method, obj, handle_obj, nullptr);
        Py_DECREF(handle_obj);
        Py_DECREF(method);
        if (!result) {
            capture_current_target_error();
            return false;
        }
        Py_DECREF(result);
        return true;
    }

    bool Queue::call_target_write_handle_ref(BindingHandle handle) {
        if (is_persister()) {
            return Persister_write_binding_lookup(native_persister(target_obj), handle);
        }
        retracesoftware::GILGuard gstate;
        PyObject* method = lookup_target_method(target_obj, "write_handle_ref");
        if (!method) {
            PyErr_SetString(PyExc_AttributeError, "target is missing 'write_handle_ref'");
            capture_current_target_error();
            return false;
        }
        PyObject* handle_obj = handle_to_pyobject(handle);
        if (!handle_obj) {
            Py_DECREF(method);
            capture_current_target_error();
            return false;
        }
        PyObject* result = PyObject_CallFunctionObjArgs(method, handle_obj, nullptr);
        Py_DECREF(handle_obj);
        Py_DECREF(method);
        if (!result) {
            capture_current_target_error();
            return false;
        }
        Py_DECREF(result);
        return true;
    }

    bool Queue::call_target_collection(PyObject* type, size_t len) {
        if (is_persister()) {
            return Persister_start_collection(native_persister(target_obj), type, len);
        }
        retracesoftware::GILGuard gstate;
        PyObject* len_obj = size_to_pyobject(len);
        if (!len_obj) {
            capture_current_target_error();
            return false;
        }
        PyObject* method = lookup_target_method(target_obj, "start_collection");
        if (!method) {
            Py_DECREF(len_obj);
            PyErr_SetString(PyExc_AttributeError, "target is missing 'start_collection'");
            capture_current_target_error();
            return false;
        }
        PyObject* result = PyObject_CallFunctionObjArgs(method, type, len_obj, nullptr);
        Py_DECREF(len_obj);
        Py_DECREF(method);
        if (!result) {
            capture_current_target_error();
            return false;
        }
        Py_DECREF(result);
        return true;
    }

    bool Queue::call_target_write_object(PyObject* obj) {
        if (is_persister()) {
            return Persister_write_object(native_persister(target_obj), obj);
        }
        retracesoftware::GILGuard gstate;
        PyObject* method = lookup_target_method(target_obj, "write_object");
        if (!method) {
            PyErr_SetString(PyExc_AttributeError, "target is missing 'write_object'");
            capture_current_target_error();
            return false;
        }
        PyObject* result = PyObject_CallFunctionObjArgs(method, obj, nullptr);
        Py_DECREF(method);
        if (!result) {
            capture_current_target_error();
            return false;
        }
        Py_DECREF(result);
        return true;
    }

    void Queue::prepare_target_resume() {
        if (!target_obj) return;
        retracesoftware::GILGuard gstate;
        PyObject* method = lookup_target_method(target_obj, "prepare_resume");
        if (!method) {
            discard_pending_error();
            return;
        }
        PyObject* result = PyObject_CallNoArgs(method);
        Py_DECREF(method);
        if (!result) {
            discard_pending_error();
            return;
        }
        Py_DECREF(result);
    }

    void Queue::reset_target_state() {
        if (!target_obj) return;
        retracesoftware::GILGuard gstate;
        PyObject* method = lookup_target_method(target_obj, "reset_state");
        if (!method) {
            discard_pending_error();
            return;
        }
        PyObject* result = PyObject_CallNoArgs(method);
        Py_DECREF(method);
        if (!result) {
            discard_pending_error();
            return;
        }
        Py_DECREF(result);
    }

    bool Queue::flush_target_background() {
        if (!target_obj) return true;
        retracesoftware::GILGuard gstate;
        PyObject* method = lookup_target_method(target_obj, "flush_background");
        if (!method) return true;
        PyObject* result = PyObject_CallNoArgs(method);
        Py_DECREF(method);
        if (!result) {
            capture_current_target_error();
            return false;
        }
        Py_DECREF(result);
        return true;
    }

    bool Queue::target_shutdown() {
        if (!target_obj) return true;
        retracesoftware::GILGuard gstate;
        PyObject* method = lookup_target_method(target_obj, "shutdown");
        if (!method) return true;
        PyObject* result = PyObject_CallNoArgs(method);
        Py_DECREF(method);
        if (!result) {
            capture_current_target_error();
            return false;
        }
        Py_DECREF(result);
        return true;
    }

    bool Queue::has_entry_slots(size_t needed) const {
        return entries.capacity() - entries.size() >= needed;
    }

    bool Queue::wait_for_slots(size_t needed) {
        if (reject_push()) return false;
        while (!has_entry_slots(needed)) {
            if (!wait_with_push_backoff()) return false;
        }
        return true;
    }

    bool Queue::wait_for_inflight() {
        if (reject_push()) return false;
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
            if (obj == nullptr) return;
            note_removed(queue_estimate_size(obj));
            Py_DECREF(obj);
        }
    }

    bool Queue::drain_returned_with_gil(int64_t needed_size) {
        if (!PyGILState_Check()) return false;
        if (inflight_limit_bytes <= 0) return true;

        PyObject* obj = nullptr;
        while (inflight() + needed_size >= inflight_limit_bytes && try_pop_returned(obj)) {
            if (obj == nullptr) return false;
            note_removed(queue_estimate_size(obj));
            Py_DECREF(obj);
        }
        return inflight() + needed_size < inflight_limit_bytes;
    }

    bool Queue::wait_with_push_backoff() {
        if (reject_push()) return false;
        if (!push_fail_callback) {
            maybe_notify_worker();
            maybe_notify_return_thread(true);
            if (PyGILState_Check()) {
                retracesoftware::GILReleaseGuard release_gil;
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
            } else {
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
            }
            return true;
        }

        retracesoftware::GILGuard gstate;
        PyObject* result = PyObject_CallNoArgs(push_fail_callback);
        if (!result) {
            PyErr_Clear();
            return false;
        }

        if (result == Py_None) {
            Py_DECREF(result);
            return false;
        }

        double delay = PyFloat_AsDouble(result);
        Py_DECREF(result);
        if (delay < 0.0 && PyErr_Occurred()) {
            PyErr_Clear();
            return false;
        }

        if (delay < 0.0) delay = 0.0;
        Py_BEGIN_ALLOW_THREADS
        std::this_thread::sleep_for(std::chrono::duration<double>(delay));
        Py_END_ALLOW_THREADS
        return true;
    }

    void Queue::return_loop() {
        while (true) {
            if (!has_returned_entries()) {
                if (shutdown_flag.load(std::memory_order_acquire)) return;
                std::unique_lock<std::mutex> lock(return_wake_mutex);
                return_thread_waiting.store(true, std::memory_order_release);
                return_wake_cv.wait_for(lock,
                                        std::chrono::milliseconds(worker_wait_timeout_ms_value),
                                        [this] {
                                            return shutdown_flag.load(std::memory_order_acquire)
                                                || has_returned_entries();
                                        });
                return_thread_waiting.store(false, std::memory_order_release);
                if (shutdown_flag.load(std::memory_order_acquire) && !has_returned_entries()) return;
                continue;
            }

            retracesoftware::GILGuard gstate;
            PyObject* obj = nullptr;
            while (try_pop_returned(obj)) {
                if (obj == nullptr) {
                    return;
                }
                note_removed(queue_estimate_size(obj));
                Py_DECREF(obj);
            }
        }
    }


    bool Queue::has_entries() {
        return entries.front() != nullptr;
    }

    bool Queue::try_consume() {
        QEntry entry;
        if (!try_pop_entry(entry)) return false;
        return dispatch_entry(entry);
    }

    bool Queue::consume() {
        return dispatch_entry(pop_entry());
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

    bool Queue::accepting_pushes() const {
        return !closed && state == QueueState::ACTIVE;
    }

    void Queue::set_inflight_limit(int64_t value) {
        inflight_limit_bytes = value;
        return_notify_threshold_bytes =
            value > 0 ? std::max<int64_t>(1, value / 4) : 1;
    }

    void Queue::disable() {
        if (closed || state == QueueState::STOPPED) return;
        state = QueueState::STOPPED;
        shutdown_flag.store(true, std::memory_order_release);
        wake_cv.notify_one();
        return_wake_cv.notify_one();
    }

    void Queue::close() {
        if (closed) return;
        closed = true;
        state = QueueState::STOPPED;

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
        reset_target_state();
    }

    void Queue::drain() {
        if (closed || !thread_started) return;
        if (state == QueueState::ACTIVE) {
            state = QueueState::PAUSED;
        }

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
    }

    void Queue::resume() {
        if (closed || thread_started || !target_obj || state == QueueState::STOPPED) return;
        prepare_target_resume();
        state = QueueState::ACTIVE;
        saw_shutdown = false;
        shutdown_flag.store(false, std::memory_order_release);
        worker_waiting.store(false, std::memory_order_release);
        return_thread_waiting.store(false, std::memory_order_release);
        return_thread_started = true;
        thread_started = true;
        return_thread = std::thread(&Queue::return_loop, this);
        writer_thread = std::thread(&Queue::worker_loop, this);
    }

    PyObject* Queue::persister() const {
        return target_obj ? target_obj : Py_None;
    }

    PyObject* Queue::target_error_handler() const {
        return on_target_error ? on_target_error : Py_None;
    }

    PyObject* Queue::push_fail_handler() const {
        return push_fail_callback ? push_fail_callback : Py_None;
    }

    bool Queue::reject_push() const {
        return !accepting_pushes();
    }

    void Queue::set_push_fail_handler(PyObject* callback) {
        if (callback != Py_None && !PyCallable_Check(callback)) {
            PyErr_SetString(PyExc_TypeError, "push_fail_callback must be callable or None");
            return;
        }
        PyObject* value = callback == Py_None ? nullptr : Py_NewRef(callback);
        Py_XSETREF(push_fail_callback, value);
    }

    void Queue::set_target_error_handler(PyObject* callback) {
        if (callback != Py_None && !PyCallable_Check(callback)) {
            PyErr_SetString(PyExc_TypeError, "on_target_error must be callable or None");
            return;
        }
        PyObject* value = callback == Py_None ? nullptr : Py_NewRef(callback);
        Py_XSETREF(on_target_error, value);
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
            while (!try_pop_entry(entry)) {
                if (shutdown_flag.load(std::memory_order_acquire)) {
                    PyEval_RestoreThread(_save);
                    retracesoftware::GILGuard gstate;
                    PyErr_SetString(PyExc_RuntimeError, "queue shutdown while awaiting payload");
                    throw nullptr;
                }
                std::this_thread::yield();
            }
            PyEval_RestoreThread(_save);
        } else {
            while (!try_pop_entry(entry)) {
                if (shutdown_flag.load(std::memory_order_acquire)) {
                    retracesoftware::GILGuard gstate;
                    PyErr_SetString(PyExc_RuntimeError, "queue shutdown while awaiting payload");
                    throw nullptr;
                }
                std::this_thread::yield();
            }
        }
        return entry;
    }

    void* Queue::consume_raw_ptr_payload() {
        QEntry entry = pop_entry();
        if (is_command_entry(entry)) {
            retracesoftware::GILGuard gstate;
            PyErr_SetString(PyExc_RuntimeError, "expected raw pointer payload");
            throw nullptr;
        }
        return as_payload_raw_ptr(entry);
    }

    PyObject* Queue::consume_owned_payload() {
        QEntry entry = pop_entry();
        if (is_command_entry(entry)) {
            retracesoftware::GILGuard gstate;
            PyErr_SetString(PyExc_RuntimeError, "expected owned object payload");
            throw nullptr;
        }
        return as_payload_obj(entry);
    }

    BindingHandle Queue::consume_binding_handle(EntryKind expected_kind) {
        QEntry entry = pop_entry();
        if (!is_tagged_entry(entry) || kind_of(entry) != expected_kind) {
            retracesoftware::GILGuard gstate;
            PyErr_SetString(PyExc_RuntimeError, "expected binding handle payload");
            throw nullptr;
        }
        return as_binding_handle(entry);
    }

    void Queue::release_consumed_obj(PyObject* obj) {
        if (queue_is_immortal(obj)) return;
        const bool had_gil = PyGILState_Check();
        std::optional<retracesoftware::GILGuard> gstate;
        if (!had_gil) {
            gstate.emplace();
        }
        note_removed(queue_estimate_size(obj));
        Py_DECREF(obj);
    }

    void Queue::finish_consumed_obj(PyObject* obj) {
        if (queue_is_immortal(obj)) return;
        while (!returned.try_push(obj)) {
            maybe_notify_return_thread(true);
            std::this_thread::yield();
        }
        maybe_notify_return_thread(true);
    }

    void Queue::poison_returned_queue() {
        while (!returned.try_push(nullptr)) {
            maybe_notify_return_thread(true);
            std::this_thread::yield();
        }
        maybe_notify_return_thread(true);
    }

    void Queue::push_shutdown_sentinel() {
        if (saw_shutdown) return;
        const bool pushed = try_push_entry(cmd_entry(CMD_SHUTDOWN, 0));
        assert(pushed);
        (void)pushed;
        maybe_notify_worker();
    }

    void Queue::discard_pending_error() {
        if (!PyErr_Occurred()) return;
        PyErr_Clear();
    }

    std::string Queue::take_pending_error_message() {
        if (!PyErr_Occurred()) return "target error";

        PyObject* exc_type = nullptr;
        PyObject* exc_value = nullptr;
        PyObject* exc_tb = nullptr;
        PyErr_Fetch(&exc_type, &exc_value, &exc_tb);
        PyErr_NormalizeException(&exc_type, &exc_value, &exc_tb);

        std::string message = "target error";
        PyObject* type_name = exc_type ? PyObject_GetAttrString(exc_type, "__name__") : nullptr;
        PyObject* value_str = exc_value ? PyObject_Str(exc_value) : nullptr;
        const char* type_cstr = type_name ? PyUnicode_AsUTF8(type_name) : nullptr;
        const char* value_cstr = value_str ? PyUnicode_AsUTF8(value_str) : nullptr;

        if (type_cstr && value_cstr && value_cstr[0]) {
            message = std::string(type_cstr) + ": " + value_cstr;
        } else if (type_cstr) {
            message = type_cstr;
        } else if (value_cstr && value_cstr[0]) {
            message = value_cstr;
        }

        if (PyErr_Occurred()) {
            PyErr_Clear();
        }

        Py_XDECREF(type_name);
        Py_XDECREF(value_str);
        Py_XDECREF(exc_type);
        Py_XDECREF(exc_value);
        Py_XDECREF(exc_tb);
        return message;
    }

    void Queue::notify_target_error(const std::string& message) {
        if (!on_target_error) return;

        PyObject* arg = PyUnicode_FromString(message.c_str());
        if (!arg) {
            PyErr_Clear();
            return;
        }

        PyObject* result = PyObject_CallOneArg(on_target_error, arg);
        Py_DECREF(arg);
        if (!result) {
            PyErr_Clear();
            return;
        }
        Py_DECREF(result);
    }

    void Queue::handle_target_failure(const std::string& message) {
        state = QueueState::DRAINING;
        shutdown_flag.store(true, std::memory_order_release);
        notify_target_error(message);
        if (target_obj && !target_shutdown()) {
            discard_pending_error();
        }
        poison_returned_queue();
        push_shutdown_sentinel();
        while (true) {
            QEntry entry = pop_entry();
            if (is_command_entry(entry) && cmd_of(entry) == CMD_SHUTDOWN) break;
            dispatch_release(entry);
        }
        state = QueueState::STOPPED;
    }

    bool Queue::dispatch_command(QEntry entry) {
        switch (cmd_of(entry)) {
            case CMD_INTERN: {
                PyObject* obj = consume_owned_payload();
                BindingHandle handle = consume_binding_handle(ENTRY_BIND);
                if (!call_target_intern(obj, handle)) {
                    release_consumed_obj(obj);
                    return false;
                }
                finish_consumed_obj(obj);
                return true;
            }
            case CMD_FLUSH:
            {
                retracesoftware::GILGuard gstate;
                PyObject* method = lookup_target_method(target_obj, "flush");
                if (!method) return true;
                PyObject* result = PyObject_CallNoArgs(method);
                Py_DECREF(method);
                if (!result) {
                    capture_current_target_error();
                    return false;
                }
                Py_DECREF(result);
                return true;
            }
            case CMD_SHUTDOWN:
                saw_shutdown = true;
                return target_shutdown();
            case CMD_LIST: {
                PyObject* type = collection_type_object(CMD_LIST);
                if (!type) return false;
                const uint32_t count = len_of(entry);
                if (!call_target_collection(type, count)) {
                    for (uint32_t i = 0; i < count; i++) dispatch_release(pop_entry());
                    return false;
                }
                for (uint32_t i = 0; i < count; i++) {
                    if (!consume()) {
                        for (uint32_t j = i + 1; j < count; j++) dispatch_release(pop_entry());
                        return false;
                    }
                }
                return true;
            }
            case CMD_TUPLE: {
                PyObject* type = collection_type_object(CMD_TUPLE);
                if (!type) return false;
                const uint32_t count = len_of(entry);
                if (!call_target_collection(type, count)) {
                    for (uint32_t i = 0; i < count; i++) dispatch_release(pop_entry());
                    return false;
                }
                for (uint32_t i = 0; i < count; i++) {
                    if (!consume()) {
                        for (uint32_t j = i + 1; j < count; j++) dispatch_release(pop_entry());
                        return false;
                    }
                }
                return true;
            }
            case CMD_DICT: {
                PyObject* type = collection_type_object(CMD_DICT);
                if (!type) return false;
                const uint32_t count = len_of(entry);
                if (!call_target_collection(type, count)) {
                    for (uint32_t i = 0; i < count; i++) {
                        dispatch_release(pop_entry());
                        dispatch_release(pop_entry());
                    }
                    return false;
                }
                for (uint32_t i = 0; i < count; i++) {
                    if (!consume()) {
                        dispatch_release(pop_entry());
                        for (uint32_t j = i + 1; j < count; j++) {
                            dispatch_release(pop_entry());
                            dispatch_release(pop_entry());
                        }
                        return false;
                    }
                    if (!consume()) {
                        for (uint32_t j = i + 1; j < count; j++) {
                            dispatch_release(pop_entry());
                            dispatch_release(pop_entry());
                        }
                        return false;
                    }
                }
                return true;
            }
            case CMD_HEARTBEAT:
            {
                if (is_persister()) {
                    return Persister_write_heartbeat(native_persister(target_obj));
                }
                retracesoftware::GILGuard gstate;
                PyObject* method = lookup_target_method(target_obj, "write_heartbeat");
                if (!method) return true;
                PyObject* result = PyObject_CallNoArgs(method);
                Py_DECREF(method);
                if (!result) {
                    capture_current_target_error();
                    return false;
                }
                Py_DECREF(result);
                return true;
            }
            default:
                retracesoftware::GILGuard gstate;
                PyErr_SetString(PyExc_RuntimeError, "unexpected command entry");
                throw nullptr;
        }
    }

    bool Queue::dispatch_entry(QEntry entry) {
        if (is_pointer_entry(entry)) {
            PyObject* obj = as_object(entry);
            if (!call_target_write_object(obj)) {
                release_consumed_obj(obj);
                return false;
            }
            finish_consumed_obj(obj);
            return true;
        }
        if (is_ref_entry(entry)) {
            return call_target_write_handle_ref(as_binding_handle(entry));
        }
        if (is_bind_entry(entry)) {
            return call_target_bind(as_binding_handle(entry));
        }
        if (is_delete_entry(entry)) {
            return call_target_delete(as_binding_handle(entry));
        }
        return dispatch_command(entry);
    }

    void Queue::dispatch_release(QEntry entry) {
        if (is_pointer_entry(entry)) {
            release_consumed_obj(as_object(entry));
            return;
        }

        if (is_ref_entry(entry) || is_bind_entry(entry) || is_delete_entry(entry)) {
            return;
        }

        switch (cmd_of(entry)) {
            case CMD_INTERN:
                release_consumed_obj(consume_owned_payload());
                (void)consume_binding_handle(ENTRY_BIND);
                break;
            case CMD_FLUSH:
            case CMD_SHUTDOWN:
                break;
            case CMD_LIST: {
                const uint32_t count = len_of(entry);
                for (uint32_t i = 0; i < count; i++) dispatch_release(pop_entry());
                break;
            }
            case CMD_TUPLE: {
                const uint32_t count = len_of(entry);
                for (uint32_t i = 0; i < count; i++) dispatch_release(pop_entry());
                break;
            }
            case CMD_DICT: {
                const uint32_t count = len_of(entry);
                for (uint32_t i = 0; i < count; i++) {
                    dispatch_release(pop_entry());
                    dispatch_release(pop_entry());
                }
                break;
            }
            case CMD_HEARTBEAT:
                break;
            default:
                retracesoftware::GILGuard gstate;
                PyErr_SetString(PyExc_RuntimeError, "unexpected command entry");
                throw nullptr;
        }
    }

    void Queue::worker_loop() {
        while (true) {
            if (!has_entries()) {
                if (shutdown_flag.load(std::memory_order_acquire)) return;
                std::unique_lock<std::mutex> lock(wake_mutex);
                worker_waiting.store(true, std::memory_order_release);
                wake_cv.wait_for(lock,
                                 std::chrono::milliseconds(worker_wait_timeout_ms_value),
                                 [this] {
                                     return shutdown_flag.load(std::memory_order_acquire) || has_entries();
                                 });
                worker_waiting.store(false, std::memory_order_release);
                if (shutdown_flag.load(std::memory_order_acquire) && !has_entries()) return;
                continue;
            }

            while (true) {
                try {
                    if (!try_consume()) {
                        if (has_target_error()) {
                            retracesoftware::GILGuard gstate;
                            handle_target_failure(take_target_error_message());
                            maybe_notify_return_thread(true);
                            return;
                        }
                        break;
                    }
                    if (saw_shutdown) {
                        retracesoftware::GILGuard gstate;
                        state = QueueState::STOPPED;
                        maybe_notify_return_thread(true);
                        return;
                    }
                } catch (...) {
                    retracesoftware::GILGuard gstate;
                    if (!PyErr_Occurred()) {
                        set_python_error_from_current_exception();
                    }
                    handle_target_failure(take_pending_error_message());
                    maybe_notify_return_thread(true);
                    return;
                }
            }
            if (!flush_target_background()) {
                retracesoftware::GILGuard gstate;
                const std::string message = has_target_error()
                    ? take_target_error_message()
                    : take_pending_error_message();
                handle_target_failure(message);
                maybe_notify_return_thread(true);
                return;
            }
            maybe_notify_return_thread(false);
        }
    }

    void Queue::drain_returned() {
        retracesoftware::GILGuard gstate;
        drain_returned_all_with_gil();
    }

    void Queue::release_entries() {
        QEntry entry;
        while (try_pop_entry(entry)) {
            dispatch_release(entry);
        }
    }
}
