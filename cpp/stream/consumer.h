#pragma once

#include <Python.h>
#include <cstdint>

#include "queueentry.h"

namespace retracesoftware_stream {
    class Consumer {
    protected:
        PyObject* target_obj;

        void handle_error() const;
        void call0(const char* name);
        void call_obj(const char* name, PyObject* obj);
        void call_obj_obj(const char* name, PyObject* a, PyObject* b);
        void call_u64(const char* name, uint64_t value);
        void call_optional0(const char* name);

    public:
        explicit Consumer(PyObject* target);
        virtual ~Consumer();

        PyObject* target() const;

        virtual bool quit_on_error() const = 0;
        virtual void prepare_resume();
        virtual void flush_background();
        virtual void reset_state();

        virtual void consume_ref(Ref ref);
        virtual void consume_intern(PyObject* obj);
        virtual void consume_object(PyObject* obj);
        virtual void consume_flush();
        virtual void consume_shutdown();
        virtual void consume_list(uint32_t len);
        virtual void consume_tuple(uint32_t len);
        virtual void consume_dict(uint32_t len);
        virtual void consume_heartbeat();
        virtual void consume_bind(Ref ref);
        virtual void consume_delete(Ref ref);
        virtual void consume_thread_switch(PyObject* obj);
        virtual void consume_new_patched(PyObject* obj, PyTypeObject* type);
        virtual void consume_new_ext_wrapped(PyTypeObject* type);
    };

    Consumer* make_consumer(PyObject* target);
}
