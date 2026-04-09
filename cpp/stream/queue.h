#pragma once

#include "stream.h"
#include <Python.h>
#include <atomic>
#include <cassert>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace retracesoftware_stream {
    extern PyTypeObject Queue_Type;
}

#include "queueentry.h"
#include "size.h"
#include "vendor/SPSCQueue.h"

namespace retracesoftware_stream {
    class Queue;
    enum class QueueState {
        ACTIVE,
        PAUSED,
        DRAINING,
        STOPPED,
    };
    Queue* Queue_get(PyObject* obj);
    int Queue_init(Queue* self, PyObject* args, PyObject* kwds);

#if PY_VERSION_HEX >= 0x030C0000
    inline bool queue_is_immortal(PyObject* obj) { return _Py_IsImmortal(obj); }
#else
    inline bool queue_is_immortal(PyObject* obj) { return obj == Py_None || obj == Py_True || obj == Py_False; }
#endif

    class Queue : public PyObject {
        rigtorp::SPSCQueue<QEntry> entries;
        rigtorp::SPSCQueue<PyObject*> returned;
        PyObject* target_obj = nullptr;
        PyObject* thread_id_callback = nullptr;
        PyObject* push_fail_callback = nullptr;
        PyObject* on_target_error = nullptr;
        PyThreadState* last_thread_tstate = nullptr;
        PyObject* last_thread_id = nullptr;
        QueueState state = QueueState::ACTIVE;
        int64_t total_added = 0;
        std::atomic<int64_t> total_removed{0};
        int64_t inflight_limit_bytes;
        int64_t return_notify_threshold_bytes;
        int worker_wait_timeout_ms_value;
        size_t notify_threshold_entries;
        std::thread writer_thread;
        std::thread return_thread;
        std::mutex wake_mutex;
        std::condition_variable wake_cv;
        std::mutex return_wake_mutex;
        std::condition_variable return_wake_cv;
        std::atomic<bool> shutdown_flag{false};
        std::atomic<bool> worker_waiting{false};
        std::atomic<bool> return_thread_waiting{false};
        bool closed = false;
        bool thread_started = false;
        bool return_thread_started = false;
        bool saw_shutdown = false;
        bool has_target_error_message = false;
        std::string last_target_error_message;

        bool is_persister() const;

        bool push_command(Cmd cmd, uint32_t len = 0);
        bool bind_target(PyObject* target);
        void clear_target();
        void capture_current_target_error();
        std::string take_target_error_message();
        bool has_target_error() const;
        void prepare_target_resume();
        void reset_target_state();
        bool flush_target_background();
        bool target_shutdown();
        bool call_target_bind(BindingHandle handle);
        bool call_target_delete(BindingHandle handle);
        bool call_target_intern(PyObject* obj, BindingHandle handle);
        bool call_target_write_handle_ref(BindingHandle handle);
        bool call_target_collection(PyObject* type, size_t len);
        bool call_target_write_object(PyObject* obj);

        bool try_pop_entry(QEntry& entry);
        QEntry pop_entry();
        void* consume_raw_ptr_payload();
        PyObject* consume_owned_payload();
        BindingHandle consume_binding_handle(EntryKind expected_kind);
        void release_consumed_obj(PyObject* obj);
        void finish_consumed_obj(PyObject* obj);
        void clear_thread_state();
        bool has_entry_slots(size_t needed) const;
        bool wait_for_slots(size_t needed);
        bool wait_for_space(size_t needed_free_slots = 2);
        bool try_pop_returned(PyObject*& obj);
        bool has_returned_entries() const;
        void maybe_notify_return_thread(bool force = false);
        void drain_returned_all_with_gil();
        bool drain_returned_with_gil(int64_t needed_size);
        bool wait_with_push_backoff();
        bool dispatch_command(QEntry entry);
        bool dispatch_entry(QEntry entry);
        void dispatch_release(QEntry entry);
        void worker_loop();
        void return_loop();
        void drain_returned();
        void release_entries();
        void poison_returned_queue();
        void push_shutdown_sentinel();
        void discard_pending_error();
        std::string take_pending_error_message();
        void notify_target_error(const std::string& message);
        void handle_target_failure(const std::string& message);
        bool reject_push() const;

        bool has_entries();
        bool try_consume();
        bool consume();

        void note_removed(int64_t size);
    public:
        int64_t inflight() const;
        int64_t inflight_limit() const;
        bool accepting_pushes() const;
        void set_inflight_limit(int64_t value);
        void disable();
        void close();
        void drain();
        void resume();
        PyObject* persister() const;
        PyObject* push_fail_handler() const;
        void set_push_fail_handler(PyObject* callback);
        PyObject* target_error_handler() const;
        void set_target_error_handler(PyObject* callback);

    private:
        friend int Queue_init(Queue* self, PyObject* args, PyObject* kwds);

        inline bool try_push_entry(QEntry entry) {
            return entries.try_push(entry);
        }

        inline void push_entry_unchecked(QEntry entry) {

            if (thread_id_callback && PyThreadState_Get() != last_thread_tstate) {
                last_thread_tstate = PyThreadState_Get();
                PyObject * thread_id = PyObject_CallNoArgs(thread_id_callback);
                if (!thread_id) {
                    throw nullptr;
                }
                const bool pushed_thread_cmd = try_push_entry(cmd_entry(CMD_THREAD_SWITCH, 0));
                const bool pushed_thread_id = pushed_thread_cmd &&
                    try_push_entry(object_entry(thread_id));
                assert(pushed_thread_id);
                (void)pushed_thread_id;
            }

            const bool pushed = try_push_entry(entry);
            assert(pushed);
            (void)pushed;
            maybe_notify_worker();
        }
    
        inline void maybe_notify_worker() {
            if (!worker_waiting.load(std::memory_order_acquire)) return;
            if (entries.size() < notify_threshold_entries) return;
            wake_cv.notify_one();
        }
    
        inline bool reserve_inflight(int64_t size) {
            total_added += size;
            return true;
        }
           
        bool wait_for_inflight();

        inline bool push(QEntry entry, size_t estimated_size = 0) {
            if (reject_push()) return false;

            if (estimated_size > 0) {
                reserve_inflight(estimated_size);
                push_entry_unchecked(entry);
                
                if (inflight_limit_bytes > 0 && inflight() >= inflight_limit_bytes) {
                    if (!wait_for_inflight()) return false;
                }
            } else {
                push_entry_unchecked(entry);
            }
            return wait_for_slots(4);
        }

        inline bool push_command_with_ptr(Cmd cmd, QEntry ptr, size_t estimated_size = 0) {
            if (reject_push()) return false;
            if (estimated_size > 0) {
                reserve_inflight(estimated_size);
            }
            push_entry_unchecked(cmd_entry(cmd, 0));
            push_entry_unchecked(ptr);
            if (estimated_size > 0 && inflight_limit_bytes > 0 && inflight() >= inflight_limit_bytes) {
                if (!wait_for_inflight()) return false;
            }
            return wait_for_slots(4);
        }

        inline bool push_immortal(PyObject * obj) {
            if (reject_push()) return false;
            push_entry_unchecked(object_entry(obj));
            return wait_for_slots(4);
        }

        inline PyObject * find_thread_id();

    public:
        Queue();
        Queue(size_t capacity, int64_t inflight_limit, int worker_wait_timeout_ms);
        ~Queue();

        bool push_flush() {
            return push_command(CMD_FLUSH);
        }
    
        bool push_shutdown() {
            return push_command(CMD_SHUTDOWN);
        }
    
        bool push_heartbeat() {
            return push_command(CMD_HEARTBEAT);
        }
            
        bool push_list_header(size_t len) {
            return push_command(CMD_LIST, static_cast<uint32_t>(len));
        }
    
        bool push_tuple_header(size_t len) {
            return push_command(CMD_TUPLE, static_cast<uint32_t>(len));
        }
    
        bool push_dict_header(size_t len) {
            return push_command(CMD_DICT, static_cast<uint32_t>(len));
        }
    
        bool push_delete(BindingHandle handle) {
            if (reject_push()) return false;
            push_entry_unchecked(delete_entry(handle));
            return wait_for_slots(4);
        }

        bool push_bind(BindingHandle handle) {
            if (reject_push()) return false;
            push_entry_unchecked(bind_entry(handle));
            return wait_for_slots(4);
        }

        bool push_intern(PyObject* obj) {
            return push_intern(obj, static_cast<BindingHandle>(reinterpret_cast<uintptr_t>(obj)));
        }

        bool push_intern(PyObject* obj, BindingHandle handle) {
            PyObject* owned = Py_NewRef(obj);
            if (reject_push()) {
                Py_DECREF(owned);
                return false;
            }
            const size_t estimated_size = queue_is_immortal(owned) ? 0 : approximate_size_bytes(owned);
            try {
                if (estimated_size > 0) reserve_inflight(estimated_size);
                push_entry_unchecked(cmd_entry(CMD_INTERN));
                push_entry_unchecked(object_entry(owned));
                push_entry_unchecked(bind_entry(handle));
                if (estimated_size > 0 && inflight_limit_bytes > 0 && inflight() >= inflight_limit_bytes) {
                    if (!wait_for_inflight()) return false;
                }
                return wait_for_slots(6);
            } catch (...) {
                Py_DECREF(owned);
                throw;
            }
        }

        inline bool push_ref(BindingHandle handle) {
            if (reject_push()) return false;
            push_entry_unchecked(ref_entry(handle));
            return wait_for_slots(4);
        }

        bool push_obj(PyObject* obj) {
            if (queue_is_immortal(obj)) {
                return push_immortal(obj);
            } else {
                const size_t estimated_size = approximate_size_bytes(obj);
                try {
                    return push(reinterpret_cast<QEntry>(Py_NewRef(obj)), estimated_size);
                } catch (...) {
                    Py_DECREF(obj);
                    throw;
                }
            }
        }
    };
}
