#pragma once

#include <Python.h>
#include <cstdint>
#include <string>

#include "queueentry.h"

namespace retracesoftware_stream {
    class Consumer {
    protected:
        PyObject* target_obj;
        std::string last_error_message;
        bool has_error_message = false;

        bool call0(const char* name);
        bool call_obj(const char* name, PyObject* obj);
        bool call_obj_obj(const char* name, PyObject* a, PyObject* b);
        bool call_u64(const char* name, uint64_t value);
        bool call_optional0(const char* name);
        void capture_current_error();

    public:
        explicit Consumer(PyObject* target);
        virtual ~Consumer();

        PyObject* target() const;
        bool has_error() const;
        std::string take_error_message();

        virtual bool quit_on_error() const = 0;
        virtual void prepare_resume();
        virtual bool flush_background();
        virtual void reset_state();

        virtual bool consume_ref(Ref ref);
        virtual bool consume_intern(PyObject* obj);
        virtual bool consume_object(PyObject* obj);
        virtual bool consume_flush();
        virtual bool consume_shutdown();
        virtual bool consume_list(uint32_t len);
        virtual bool consume_tuple(uint32_t len);
        virtual bool consume_dict(uint32_t len);
        virtual bool consume_heartbeat();
        virtual bool consume_bind(Ref ref);
        virtual bool consume_delete(Ref ref);
        virtual bool consume_thread_switch(PyObject* obj);
        virtual bool consume_new_patched(PyObject* obj, PyTypeObject* type);
        virtual bool consume_new_ext_wrapped(PyTypeObject* type);
    };

    Consumer* make_consumer(PyObject* target);
}
