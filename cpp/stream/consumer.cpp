#include "persister.h"

#include "queue.h"

#include <chrono>
#include <stdexcept>

namespace retracesoftware_stream {
    uint64_t Consumer::handle_index(Ref handle) {
        auto it = handle_indices.find(handle);
        if (it != handle_indices.end()) return it->second;
        uint64_t index = next_handle_index++;
        handle_indices[handle] = index;
        return index;
    }

    bool Consumer::handle_delete_delta(Ref handle, uint64_t* delta) {
        auto it = handle_indices.find(handle);
        if (it == handle_indices.end()) return false;
        uint64_t index = it->second;
        handle_indices.erase(it);
        *delta = next_handle_index - index - 1;
        return true;
    }

    int Consumer::bind_index(Ref ref) const {
        auto it = bound_ref_indices.find(ref);
        if (it != bound_ref_indices.end()) return it->second;
        return next_bound_ref_index;
    }

    int Consumer::bound_ref_index(Ref ref) {
        auto it = bound_ref_indices.find(ref);
        if (it != bound_ref_indices.end()) return it->second;
        throw std::out_of_range("unknown bound ref");
    }

    bool Consumer::bound_ref_delete_index(Ref ref, int* index) {
        auto it = bound_ref_indices.find(ref);
        if (it == bound_ref_indices.end()) return false;
        *index = it->second;
        bound_ref_indices.erase(it);
        return true;
    }

    void Consumer::remember_bound_ref(Ref ref, int index) {
        if (index < 0) return;
        bound_ref_indices[ref] = index;
        if (next_bound_ref_index <= index) next_bound_ref_index = index + 1;
    }

    bool Consumer::has_bound_ref(Ref ref) const {
        return bound_ref_indices.contains(ref);
    }

    bool Consumer::is_native_async() const {
        return Py_TYPE(persister) == &AsyncFilePersister_Type;
    }

    bool Consumer::python_quit_on_error() const {
        return python_quit_on_error_value;
    }

    bool Consumer::call0(const char* name) {
        PyObject* result = PyObject_CallMethod(persister, name, nullptr);
        if (!result) return false;
        Py_DECREF(result);
        return true;
    }

    bool Consumer::call_obj(const char* name, PyObject* obj) {
        PyObject* result = PyObject_CallMethod(persister, name, "O", obj);
        if (!result) return false;
        Py_DECREF(result);
        return true;
    }

    bool Consumer::call_obj_obj(const char* name, PyObject* a, PyObject* b) {
        PyObject* result = PyObject_CallMethod(persister, name, "OO", a, b);
        if (!result) return false;
        Py_DECREF(result);
        return true;
    }

    bool Consumer::call_u64(const char* name, uint64_t value) {
        PyObject* result = PyObject_CallMethod(persister, name, "K",
                                               (unsigned long long)value);
        if (!result) return false;
        Py_DECREF(result);
        return true;
    }

    bool Consumer::call_u32(const char* name, uint32_t value) {
        return call_u64(name, value);
    }

    bool Consumer::call_int(const char* name, int value) {
        PyObject* result = PyObject_CallMethod(persister, name, "i", value);
        if (!result) return false;
        Py_DECREF(result);
        return true;
    }

    bool Consumer::call_u64_obj(const char* name, uint64_t value, PyObject* obj) {
        PyObject* result = PyObject_CallMethod(persister, name, "KO",
                                               (unsigned long long)value, obj);
        if (!result) return false;
        Py_DECREF(result);
        return true;
    }

    void Consumer::python_error() {
        handle_debug_error(python_quit_on_error());
    }

    void Consumer::clear_ref_state() {
        handle_indices.clear();
        bound_ref_indices.clear();
        next_handle_index = 0;
        next_bound_ref_index = 0;
    }

    void Consumer::reset_native_sink_state() {
        if (!is_native_async()) return;
        auto& sink = *reinterpret_cast<AsyncFilePersister*>(persister);
        sink.last_tstate = nullptr;
        sink.clear_thread_cache();
    }

    void Consumer::drain_queue_entries() {
        queue.release_entries();
    }

    void Consumer::drain_return_queue() {
        while (auto* ep = queue.return_front()) {
            PyObject* obj = *ep;
            queue.return_pop();
            queue.note_removed(estimate_size(obj));
            Py_DECREF(obj);
        }
    }

    void Consumer::return_loop() {
        while (true) {
            PyObject** ep;
            while (!(ep = queue.return_front())) {
                if (return_shutdown.load(std::memory_order_acquire)) return;
                std::this_thread::yield();
            }

            PyGILState_STATE gstate = PyGILState_Ensure();
            auto batch_start = std::chrono::steady_clock::now();
            int deallocs = 0;

            while ((ep = queue.return_front())) {
                PyObject* obj = *ep;
                queue.return_pop();
                queue.note_removed(estimate_size(obj));
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

    template <typename T>
    void Consumer::run_loop(T& consumer) {
        while (true) {
            while (!queue.has_entries()) {
                if (shutdown_flag.load(std::memory_order_acquire)) return;
                std::this_thread::yield();
            }

            PyGILState_STATE gstate = PyGILState_Ensure();
            while (queue.try_consume(consumer)) {
                if (consumer.saw_shutdown) {
                    PyGILState_Release(gstate);
                    return;
                }
            }
            if (consumer.is_native_async()) {
                auto& sink = *reinterpret_cast<AsyncFilePersister*>(consumer.persister);
                try { sink.stream->flush(); }
                catch (...) { handle_write_error(sink.quit_on_error); }
            }
            PyGILState_Release(gstate);
        }
    }

    Consumer::Consumer(Queue& q, PyObject* sink)
        : queue(q), persister(sink) {
        PyObject* attr = PyObject_GetAttrString(persister, "quit_on_error");
        if (attr) {
            int truthy = PyObject_IsTrue(attr);
            if (truthy >= 0) python_quit_on_error_value = truthy != 0;
            else PyErr_Clear();
            Py_DECREF(attr);
        } else {
            PyErr_Clear();
        }
        resume();
    }

    Consumer::~Consumer() {
        close();
    }

    bool Consumer::return_consumed_objects() const {
        return true;
    }

    void Consumer::consume_object(PyObject* obj) {
        if (is_native_async()) {
            auto& sink = *reinterpret_cast<AsyncFilePersister*>(persister);
            try { sink.stream->write(obj); }
            catch (...) { handle_write_error(sink.quit_on_error); }
            return;
        }
        if (!call_obj("consume_object", obj)) python_error();
    }

    void Consumer::consume_handle_ref(Ref ref) {
        uint64_t index = handle_index(ref);
        if (is_native_async()) {
            auto& sink = *reinterpret_cast<AsyncFilePersister*>(persister);
            try { sink.stream->write_handle_ref_by_index(index); }
            catch (...) { handle_write_error(sink.quit_on_error); }
            return;
        }
        if (!call_u64("consume_handle_ref", index)) python_error();
    }

    void Consumer::consume_handle_delete(Ref ref) {
        uint64_t delta;
        if (!handle_delete_delta(ref, &delta)) return;
        if (is_native_async()) {
            auto& sink = *reinterpret_cast<AsyncFilePersister*>(persister);
            try { sink.stream->write_handle_delete(delta); }
            catch (...) { handle_write_error(sink.quit_on_error); }
            return;
        }
        if (!call_u64("consume_handle_delete", delta)) python_error();
    }

    void Consumer::consume_bound_ref(Ref ref) {
        int index = bound_ref_index(ref);
        if (is_native_async()) {
            auto& sink = *reinterpret_cast<AsyncFilePersister*>(persister);
            try { sink.stream->write_bound_ref_by_index(index); }
            catch (...) { handle_write_error(sink.quit_on_error); }
            return;
        }
        if (!call_int("consume_bound_ref", index)) python_error();
    }

    void Consumer::consume_bound_ref_delete(Ref ref) {
        int index;
        if (!bound_ref_delete_index(ref, &index)) return;
        if (is_native_async()) {
            auto& sink = *reinterpret_cast<AsyncFilePersister*>(persister);
            try { sink.stream->delete_bound_ref_by_index(index); }
            catch (...) { handle_write_error(sink.quit_on_error); }
            return;
        }
        if (!call_int("consume_bound_ref_delete", index)) python_error();
    }

    void Consumer::consume_flush() {
        if (is_native_async()) {
            auto& sink = *reinterpret_cast<AsyncFilePersister*>(persister);
            try { sink.stream->flush(); }
            catch (...) { handle_write_error(sink.quit_on_error); }
            return;
        }
        if (!call0("consume_flush")) python_error();
    }

    void Consumer::consume_shutdown() {
        saw_shutdown = true;
        if (is_native_async()) {
            consume_flush();
            return;
        }
        if (!call0("consume_shutdown")) python_error();
    }

    void Consumer::consume_list(Queue& queue, uint32_t len) {
        if (is_native_async()) {
            auto& sink = *reinterpret_cast<AsyncFilePersister*>(persister);
            try { sink.stream->write_list_header(len); }
            catch (...) { handle_write_error(sink.quit_on_error); }
        } else if (!call_u32("consume_list", len)) {
            python_error();
        }
        for (uint32_t i = 0; i < len; i++) queue.consume(*this);
    }

    void Consumer::consume_tuple(Queue& queue, uint32_t len) {
        if (is_native_async()) {
            auto& sink = *reinterpret_cast<AsyncFilePersister*>(persister);
            try { sink.stream->write_tuple_header(len); }
            catch (...) { handle_write_error(sink.quit_on_error); }
        } else if (!call_u32("consume_tuple", len)) {
            python_error();
        }
        for (uint32_t i = 0; i < len; i++) queue.consume(*this);
    }

    void Consumer::consume_dict(Queue& queue, uint32_t len) {
        if (is_native_async()) {
            auto& sink = *reinterpret_cast<AsyncFilePersister*>(persister);
            try { sink.stream->write_dict_header(len); }
            catch (...) { handle_write_error(sink.quit_on_error); }
        } else if (!call_u32("consume_dict", len)) {
            python_error();
        }
        for (uint32_t i = 0; i < len; i++) {
            queue.consume(*this);
            queue.consume(*this);
        }
    }

    void Consumer::consume_heartbeat(Queue& queue) {
        if (is_native_async()) {
            auto& sink = *reinterpret_cast<AsyncFilePersister*>(persister);
            try { sink.stream->write_control(Heartbeat); }
            catch (...) { handle_write_error(sink.quit_on_error); }
        } else if (!call0("consume_heartbeat")) {
            python_error();
        }
        queue.consume(*this);
    }

    void Consumer::consume_new_ext_wrapped(PyTypeObject* type) {
        if (is_native_async()) {
            auto& sink = *reinterpret_cast<AsyncFilePersister*>(persister);
            try { sink.stream->write(reinterpret_cast<PyObject*>(type)); }
            catch (...) { handle_write_error(sink.quit_on_error); }
            return;
        }
        if (!call_obj("consume_new_ext_wrapped", reinterpret_cast<PyObject*>(type))) python_error();
    }

    void Consumer::consume_delete(Ref ref) {
        if (has_bound_ref(ref)) return;
        if (is_native_async()) {
            auto& sink = *reinterpret_cast<AsyncFilePersister*>(persister);
            try { sink.stream->object_freed(reinterpret_cast<PyObject*>(ref)); }
            catch (...) { handle_write_error(sink.quit_on_error); }
            return;
        }
        if (!call_u64("consume_delete", (uint64_t)(uintptr_t)ref)) python_error();
    }

    void Consumer::consume_thread(PyThreadState* tstate) {
        if (is_native_async()) {
            auto& sink = *reinterpret_cast<AsyncFilePersister*>(persister);
            if (tstate == sink.last_tstate) return;
            sink.last_tstate = tstate;
            auto& cache = *sink.thread_cache;
            auto it = cache.find(tstate);
            PyObject* handle = nullptr;
            if (it != cache.end()) {
                handle = it->second;
            } else {
                handle = tstate->dict ? PyDict_GetItem(tstate->dict, sink.writer_key) : nullptr;
                if (handle) {
                    Py_INCREF(handle);
                    cache[tstate] = handle;
                }
            }
            if (handle) {
                try { sink.stream->write_thread_switch(handle); }
                catch (...) { handle_write_error(sink.quit_on_error); }
            }
            return;
        }
        if (!call_u64("consume_thread", (uint64_t)(uintptr_t)tstate)) python_error();
    }

    void Consumer::consume_pickled(PyObject* obj) {
        if (is_native_async()) {
            auto& sink = *reinterpret_cast<AsyncFilePersister*>(persister);
            try { sink.stream->write_pre_pickled(obj); }
            catch (...) { handle_write_error(sink.quit_on_error); }
            return;
        }
        if (!call_obj("consume_pickled", obj)) python_error();
    }

    void Consumer::consume_new_handle(Ref ref, PyObject* obj) {
        uint64_t index = handle_index(ref);
        if (is_native_async()) {
            auto& sink = *reinterpret_cast<AsyncFilePersister*>(persister);
            try { sink.stream->write_new_handle(obj); }
            catch (...) { handle_write_error(sink.quit_on_error); }
            return;
        }
        if (!call_u64_obj("consume_new_handle", index, obj)) python_error();
    }

    void Consumer::consume_new_patched(PyObject* obj, PyObject* type) {
        if (is_native_async()) {
            auto& sink = *reinterpret_cast<AsyncFilePersister*>(persister);
            try { sink.stream->new_patched(obj, type); }
            catch (...) { handle_write_error(sink.quit_on_error); }
        } else if (!call_obj_obj("consume_new_patched", obj, type)) {
            python_error();
        }
        remember_bound_ref(reinterpret_cast<Ref>(obj), bind_index(reinterpret_cast<Ref>(obj)));
    }

    void Consumer::consume_bind(Ref ref) {
        int index = bind_index(ref);
        if (is_native_async()) {
            auto& sink = *reinterpret_cast<AsyncFilePersister*>(persister);
            try { sink.stream->bind(reinterpret_cast<PyObject*>(ref)); }
            catch (...) { handle_write_error(sink.quit_on_error); }
        } else if (!call_int("consume_bind", index)) {
            python_error();
        }
        remember_bound_ref(ref, index);
    }

    void Consumer::consume_serialize_error(Queue& queue) {
        if (is_native_async()) {
            auto& sink = *reinterpret_cast<AsyncFilePersister*>(persister);
            try { sink.stream->write_control(SerializeError); }
            catch (...) { handle_write_error(sink.quit_on_error); }
        } else if (!call0("consume_serialize_error")) {
            python_error();
        }
        queue.consume(*this);
    }

    void Consumer::operator()() {
        saw_shutdown = false;
        run_loop(*this);
    }

    void Consumer::close() {
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
        clear_ref_state();
        reset_native_sink_state();
    }

    void Consumer::drain() {
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

    void Consumer::resume() {
        if (closed || thread_started || !persister) return;
        reset_native_sink_state();
        if (is_native_async()) {
            auto& sink = *reinterpret_cast<AsyncFilePersister*>(persister);
            if (sink.fw) sink.fw->stamp_pid();
        }
        saw_shutdown = false;
        shutdown_flag.store(false, std::memory_order_release);
        return_shutdown.store(false, std::memory_order_release);
        writer_thread = std::thread(std::ref(*this));
        return_thread = std::thread(&Consumer::return_loop, this);
        thread_started = true;
    }
}
