#pragma once

#include <Python.h>
#include <atomic>
#include <cstdint>
#include <thread>
#include <unordered_map>

namespace retracesoftware_stream {
    extern PyTypeObject StreamHandle_Type;
    extern PyTypeObject Queue_Type;
}

#include "queueentry.h"
#include "vendor/SPSCQueue.h"

namespace retracesoftware_stream {
    class Queue;
    Queue* Queue_get(PyObject* obj);

    class Consumer {
        Queue& queue;
        PyObject* persister;
        std::unordered_map<Ref, uint64_t> handle_indices;
        std::unordered_map<Ref, int> bound_ref_indices;
        uint64_t next_handle_index = 0;
        int next_bound_ref_index = 0;
        std::thread writer_thread;
        std::thread return_thread;
        std::atomic<bool> shutdown_flag{false};
        std::atomic<bool> return_shutdown{false};
        bool python_quit_on_error_value = false;
        bool closed = false;
        bool thread_started = false;
        bool saw_shutdown = false;

        uint64_t handle_index(Ref handle);
        bool handle_delete_delta(Ref handle, uint64_t* delta);
        int bind_index(Ref ref) const;
        int bound_ref_index(Ref ref);
        bool bound_ref_delete_index(Ref ref, int* index);
        void remember_bound_ref(Ref ref, int index);
        bool has_bound_ref(Ref ref) const;

        bool is_native_async() const;
        bool python_quit_on_error() const;
        bool call0(const char* name);
        bool call_obj(const char* name, PyObject* obj);
        bool call_obj_obj(const char* name, PyObject* a, PyObject* b);
        bool call_u64(const char* name, uint64_t value);
        bool call_u32(const char* name, uint32_t value);
        bool call_int(const char* name, int value);
        bool call_u64_obj(const char* name, uint64_t value, PyObject* obj);
        void python_error();
        void clear_ref_state();
        void reset_native_sink_state();
        void drain_queue_entries();
        void drain_return_queue();
        void return_loop();

        template <typename T>
        void run_loop(T& consumer);

    public:
        Consumer(Queue& q, PyObject* sink);
        ~Consumer();

        bool return_consumed_objects() const;
        void consume_object(PyObject* obj);
        void consume_handle_ref(Ref ref);
        void consume_handle_delete(Ref ref);
        void consume_bound_ref(Ref ref);
        void consume_bound_ref_delete(Ref ref);
        void consume_flush();
        void consume_shutdown();
        void consume_list(Queue& queue, uint32_t len);
        void consume_tuple(Queue& queue, uint32_t len);
        void consume_dict(Queue& queue, uint32_t len);
        void consume_heartbeat(Queue& queue);
        void consume_new_ext_wrapped(PyTypeObject* type);
        void consume_delete(Ref ref);
        void consume_thread(PyThreadState* tstate);
        void consume_pickled(PyObject* obj);
        void consume_new_handle(Ref ref, PyObject* obj);
        void consume_new_patched(PyObject* obj, PyObject* type);
        void consume_bind(Ref ref);
        void consume_serialize_error(Queue& queue);
        void operator()();
        void close();
        void drain();
        void resume();

        friend class Queue;
    };

    class Queue : public PyObject {
    public:
        rigtorp::SPSCQueue<QEntry> entries;
        rigtorp::SPSCQueue<PyObject*> returned;
        PyObject* persister_obj = nullptr;
        Consumer* consumer = nullptr;
        int64_t total_added = 0;
        std::atomic<int64_t> total_removed{0};
        int64_t inflight_limit_bytes;
        int stall_timeout_seconds_value;
        Queue();
        Queue(size_t capacity, size_t return_capacity,
              int64_t inflight_limit, int stall_timeout_seconds);
        ~Queue();

        bool push_flush();
        bool push_shutdown();

        bool push_immortal(PyObject* obj);
        bool push_bind(Ref ref);
        bool push_delete(Ref ref);

        bool push_thread(PyThreadState* tstate);
        bool push_new_patched(PyObject* obj, PyTypeObject* type);
        bool push_ext_wrapped(PyTypeObject* type);
        bool push_new_handle(Ref handle, PyObject* obj);
        bool push_heartbeat();
        bool push_serialize_error();
        bool push_pickled(PyObject* bytes_obj);
        bool push_list_header(size_t len);
        bool push_tuple_header(size_t len);
        bool push_dict_header(size_t len);
        bool push_handle_ref(Ref ref);
        bool push_handle_delete(Ref ref);
        bool push_bound_ref(Ref ref);
        bool push_bound_ref_delete(Ref ref);
        bool has_entries();
        bool try_consume(Consumer& consumer);
        void consume(Consumer& consumer);
        bool try_push_return(PyObject* obj);
        PyObject** return_front();
        void return_pop();
        bool push_obj(PyObject* obj);
        void note_removed(int64_t size);
        int64_t inflight() const;
        int64_t inflight_limit() const;
        void set_inflight_limit(int64_t value);
        void close();
        void drain();
        void resume();
        PyObject* persister() const;

    private:
        friend class Consumer;
        bool push_command(Cmd cmd, uint32_t len = 0);

        bool push_pointer_entry(void* ptr, PointerKind kind);
        bool push_owned_payload(PyObject* obj, int64_t estimated_size);
        bool push_raw_ptr_payload(void* ptr);
        bool try_push_entry(QEntry entry);
        bool push_entry(QEntry entry);
        bool try_pop_entry(QEntry& entry);
        QEntry pop_entry();
        void* consume_raw_ptr_payload();
        PyObject* consume_owned_payload();
        PyThreadState* consume_tstate();
        Ref consume_ref();
        void release_consumed_obj(PyObject* obj);
        void finish_consumed_obj(Consumer& consumer, PyObject* obj);
        void dispatch_entry(Consumer& consumer, QEntry entry);
        void dispatch_release(QEntry entry);
        void release_entries();
        bool reserve_inflight(int64_t size);
        void release_reserved_inflight(int64_t size);
        bool wait_for_inflight();
    };
}
