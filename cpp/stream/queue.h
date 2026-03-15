#pragma once

#include <Python.h>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <thread>

namespace retracesoftware_stream {
    extern PyTypeObject StreamHandle_Type;
}

#include "queueentry.h"
#include "vendor/SPSCQueue.h"

namespace retracesoftware_stream {
    class Queue;

    class QueueConsumer {
    public:
        virtual ~QueueConsumer() = default;

        virtual bool return_consumed_objects() const { return true; }

        virtual void consume_object(PyObject* obj) = 0;
        virtual void consume_handle_ref(Ref ref) = 0;
        virtual void consume_handle_delete(Ref ref) = 0;

        virtual void consume_flush() = 0;
        virtual void consume_shutdown() = 0;
        virtual void consume_list(Queue& queue, uint32_t len) = 0;
        virtual void consume_tuple(Queue& queue, uint32_t len) = 0;
        virtual void consume_dict(Queue& queue, uint32_t len) = 0;
        virtual void consume_heartbeat(Queue& queue) = 0;
        virtual void consume_external_wrapped() = 0;
        virtual void consume_delete(PyObject* obj) = 0;
        virtual void consume_thread(PyThreadState* tstate) = 0;
        virtual void consume_pickled(PyObject* obj) = 0;
        virtual void consume_new_handle(Ref ref, PyObject* obj) = 0;
        virtual void consume_new_patched(PyObject* obj, PyObject* type) = 0;
        virtual void consume_bind(PyObject* obj) = 0;
        virtual void consume_serialize_error(Queue& queue) = 0;
    };

    class Queue {
        rigtorp::SPSCQueue<QEntry> entries;
        rigtorp::SPSCQueue<PyObject*> returned;
        int64_t total_added = 0;
        std::atomic<int64_t> total_removed{0};
        int64_t inflight_limit_bytes;
        int stall_timeout_seconds_value;

        inline bool try_push_entry(QEntry entry) {
            return entries.try_push(entry);
        }

        inline bool push_entry(QEntry entry) {
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

    public:
        Queue(size_t capacity, size_t return_capacity,
              int64_t inflight_limit, int stall_timeout_seconds)
            : entries(capacity),
              returned(return_capacity),
              inflight_limit_bytes(inflight_limit),
              stall_timeout_seconds_value(stall_timeout_seconds) {}

        inline bool push_command(Cmd cmd, uint32_t len = 0) {
            return push_entry(cmd_entry(cmd, len));
        }

        inline bool push_flush() {
            return push_command(CMD_FLUSH);
        }

        inline bool push_shutdown() {
            return push_command(CMD_SHUTDOWN);
        }

        inline bool push_bind(PyObject* obj, int64_t estimated_size = 0) {
            return push_command(CMD_BIND) &&
                   push_obj(obj, estimated_size);
        }

        inline bool push_delete(PyObject* obj, int64_t estimated_size = 0) {
            return push_command(CMD_DELETE) &&
                   push_obj(obj, estimated_size);
        }

        inline bool push_thread(PyThreadState* tstate) {
            return push_command(CMD_THREAD) &&
                   push_entry(raw_ptr_entry(tstate));
        }

        inline bool push_new_patched(PyObject* obj, PyObject* type,
                                     int64_t obj_size = 0, int64_t type_size = 0) {
            return push_command(CMD_NEW_PATCHED) &&
                   push_obj(obj, obj_size) &&
                   push_obj(type, type_size);
        }

        inline bool push_new_handle(Ref handle, PyObject* obj, int64_t estimated_size = 0) {
            return push_command(CMD_NEW_HANDLE) &&
                   push_entry(raw_ptr_entry(handle)) &&
                   push_obj(obj, estimated_size);
        }

        inline bool push_heartbeat() {
            return push_command(CMD_HEARTBEAT);
        }

        inline bool push_serialize_error() {
            return push_command(CMD_SERIALIZE_ERROR);
        }

        inline bool push_pickled(PyObject* bytes_obj, int64_t estimated_size = 0) {
            return push_command(CMD_PICKLED) &&
                   push_obj(bytes_obj, estimated_size);
        }

        inline bool push_list_header(size_t len) {
            return push_command(CMD_LIST, (uint32_t)len);
        }

        inline bool push_tuple_header(size_t len) {
            return push_command(CMD_TUPLE, (uint32_t)len);
        }

        inline bool push_dict_header(size_t len) {
            return push_command(CMD_DICT, (uint32_t)len);
        }

        inline bool push_ref(Ref ref) {
            return push_entry(handle_ref_entry(ref));
        }

        inline bool push_ref_delete(Ref ref) {
            return push_entry(handle_delete_entry(ref));
        }

        inline bool has_entries() {
            return entries.front() != nullptr;
        }

        inline bool try_consume(QueueConsumer& consumer) {
            QEntry entry;
            if (!try_pop_entry(entry)) return false;
            dispatch_entry(consumer, entry);
            return true;
        }

        inline void consume(QueueConsumer& consumer) {
            dispatch_entry(consumer, pop_entry());
        }

        inline bool try_push_return(PyObject* obj) {
            return returned.try_push(obj);
        }

        inline PyObject** return_front() {
            return returned.front();
        }

        inline void return_pop() {
            returned.pop();
        }

        inline bool push_obj(PyObject* obj, int64_t estimated_size = 0) {
            if (!reserve_inflight(estimated_size)) return false;
            Py_INCREF(obj);
            if (push_entry(obj_entry(obj))) return true;
            Py_DECREF(obj);
            release_reserved_inflight(estimated_size);
            return false;
        }

        inline void note_removed(int64_t size) {
            total_removed.fetch_add(size, std::memory_order_relaxed);
        }

        inline int64_t inflight() const {
            return total_added - total_removed.load(std::memory_order_relaxed);
        }

    private:
        inline bool try_pop_entry(QEntry& entry) {
            QEntry* ep = entries.front();
            if (!ep) return false;
            entry = *ep;
            entries.pop();
            return true;
        }

        inline QEntry pop_entry() {
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

        inline PyObject* consume_ptr() {
            return as_ptr(pop_entry());
        }

        inline PyThreadState* consume_tstate() {
            return as_tstate(pop_entry());
        }

        inline Ref consume_ref() {
            return (Ref)as_raw_ptr(pop_entry());
        }

        inline void release_consumed_obj(PyObject* obj) {
            if (is_immortal(obj)) return;
            note_removed(estimate_size(obj));
            Py_DECREF(obj);
        }

        inline void finish_consumed_obj(QueueConsumer& consumer, PyObject* obj) {
            if (!consumer.return_consumed_objects()) {
                release_consumed_obj(obj);
                return;
            }
            if (is_immortal(obj)) return;
            if (!returned.try_push(obj)) {
                release_consumed_obj(obj);
            }
        }

        inline void dispatch_entry(QueueConsumer& consumer, QEntry entry) {
            switch (tag_of(entry)) {
                case TAG_OBJECT: {
                    PyObject* obj = as_ptr(entry);
                    consumer.consume_object(obj);
                    finish_consumed_obj(consumer, obj);
                    break;
                }
                case TAG_HANDLE_REF:
                    consumer.consume_handle_ref(handle_ref_of(entry));
                    break;
                case TAG_HANDLE_DELETE:
                    consumer.consume_handle_delete(handle_delete_of(entry));
                    break;
                case TAG_COMMAND:
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
                        case CMD_EXTERNAL_WRAPPED:
                            consumer.consume_external_wrapped();
                            break;
                        case CMD_DELETE: {
                            PyObject* obj = consume_ptr();
                            consumer.consume_delete(obj);
                            finish_consumed_obj(consumer, obj);
                            break;
                        }
                        case CMD_THREAD:
                            consumer.consume_thread(consume_tstate());
                            break;
                        case CMD_PICKLED: {
                            PyObject* obj = consume_ptr();
                            consumer.consume_pickled(obj);
                            finish_consumed_obj(consumer, obj);
                            break;
                        }
                        case CMD_NEW_HANDLE: {
                            Ref ref = consume_ref();
                            PyObject* obj = consume_ptr();
                            consumer.consume_new_handle(ref, obj);
                            finish_consumed_obj(consumer, obj);
                            break;
                        }
                        case CMD_NEW_PATCHED: {
                            PyObject* obj = consume_ptr();
                            PyObject* type = consume_ptr();
                            consumer.consume_new_patched(obj, type);
                            finish_consumed_obj(consumer, obj);
                            finish_consumed_obj(consumer, type);
                            break;
                        }
                        case CMD_BIND: {
                            PyObject* obj = consume_ptr();
                            consumer.consume_bind(obj);
                            finish_consumed_obj(consumer, obj);
                            break;
                        }
                        case CMD_SERIALIZE_ERROR:
                            consumer.consume_serialize_error(*this);
                            break;
                    }
                    break;
            }
        }

        inline bool reserve_inflight(int64_t size) {
            if (!wait_for_inflight()) return false;
            total_added += size;
            return true;
        }

        inline void release_reserved_inflight(int64_t size) {
            total_added -= size;
        }

        inline bool wait_for_inflight() {
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

    public:
        inline int64_t inflight_limit() const {
            return inflight_limit_bytes;
        }

        inline void set_inflight_limit(int64_t value) {
            inflight_limit_bytes = value;
        }
    };
}
