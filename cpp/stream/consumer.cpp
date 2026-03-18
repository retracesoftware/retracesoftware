#include "consumer.h"

#include "persister.h"
#include "stream.h"

namespace retracesoftware_stream {
    void Consumer::handle_error() const {
        handle_debug_error(quit_on_error());
    }

    void Consumer::call0(const char* name) {
        PyGILState_STATE gil = PyGILState_Ensure();
        PyObject* result = PyObject_CallMethod(target_obj, name, nullptr);
        if (!result) {
            handle_error();
            PyGILState_Release(gil);
            return;
        }
        Py_DECREF(result);
        PyGILState_Release(gil);
    }

    void Consumer::call_obj(const char* name, PyObject* obj) {
        PyGILState_STATE gil = PyGILState_Ensure();
        PyObject* result = PyObject_CallMethod(target_obj, name, "O", obj);
        if (!result) {
            handle_error();
            PyGILState_Release(gil);
            return;
        }
        Py_DECREF(result);
        PyGILState_Release(gil);
    }

    void Consumer::call_obj_obj(const char* name, PyObject* a, PyObject* b) {
        PyGILState_STATE gil = PyGILState_Ensure();
        PyObject* result = PyObject_CallMethod(target_obj, name, "OO", a, b);
        if (!result) {
            handle_error();
            PyGILState_Release(gil);
            return;
        }
        Py_DECREF(result);
        PyGILState_Release(gil);
    }

    void Consumer::call_u64(const char* name, uint64_t value) {
        PyGILState_STATE gil = PyGILState_Ensure();
        PyObject* result = PyObject_CallMethod(
            target_obj,
            name,
            "K",
            (unsigned long long)value);
        if (!result) {
            handle_error();
            PyGILState_Release(gil);
            return;
        }
        Py_DECREF(result);
        PyGILState_Release(gil);
    }

    void Consumer::call_optional0(const char* name) {
        PyGILState_STATE gil = PyGILState_Ensure();
        PyObject* method = PyObject_GetAttrString(target_obj, name);
        if (!method) {
            PyErr_Clear();
            PyGILState_Release(gil);
            return;
        }
        PyObject* result = PyObject_CallNoArgs(method);
        Py_DECREF(method);
        if (!result) {
            handle_error();
            PyGILState_Release(gil);
            return;
        }
        Py_DECREF(result);
        PyGILState_Release(gil);
    }

    Consumer::Consumer(PyObject* target)
        : target_obj(Py_NewRef(target)) {}

    Consumer::~Consumer() {
        PyGILState_STATE gil = PyGILState_Ensure();
        Py_DECREF(target_obj);
        PyGILState_Release(gil);
    }

    PyObject* Consumer::target() const {
        return target_obj;
    }

    void Consumer::prepare_resume() {}

    void Consumer::flush_background() {}

    void Consumer::reset_state() {
        call_optional0("reset_state");
    }

    void Consumer::consume_object(PyObject* obj) {
        call_obj("consume_object", obj);
    }

    void Consumer::consume_ref(Ref ref) {
        call_u64("consume_ref", (uint64_t)(uintptr_t)ref);
    }

    void Consumer::consume_intern(PyObject* obj) {
        consume_object(obj);
        consume_bind(reinterpret_cast<Ref>(obj));
    }

    void Consumer::consume_flush() {
        call0("consume_flush");
    }

    void Consumer::consume_shutdown() {
        call0("consume_shutdown");
    }

    void Consumer::consume_list(uint32_t len) {
        call_u64("consume_list", len);
    }

    void Consumer::consume_tuple(uint32_t len) {
        call_u64("consume_tuple", len);
    }

    void Consumer::consume_dict(uint32_t len) {
        call_u64("consume_dict", len);
    }

    void Consumer::consume_heartbeat() {
        call0("consume_heartbeat");
    }

    void Consumer::consume_bind(Ref ref) {
        call_u64("consume_bind", (uint64_t)(uintptr_t)ref);
    }

    void Consumer::consume_delete(Ref ref) {
        call_u64("consume_delete", (uint64_t)(uintptr_t)ref);
    }

    void Consumer::consume_thread_switch(PyObject* obj) {
        call_obj("consume_thread_switch", obj);
    }

    void Consumer::consume_new_patched(PyObject* obj, PyTypeObject* type) {
        call_obj_obj("consume_new_patched", obj, reinterpret_cast<PyObject*>(type));
    }

    void Consumer::consume_new_ext_wrapped(PyTypeObject* type) {
        call_obj("consume_new_ext_wrapped", reinterpret_cast<PyObject*>(type));
    }

    class NativeConsumer : public Consumer {
        template <typename Fn>
        void run(Fn&& fn) {
            try {
                fn();
            } catch (...) {
                PyGILState_STATE gil = PyGILState_Ensure();
                handle_write_error();
                PyGILState_Release(gil);
            }
        }

        template <typename Fn>
        void run_bool(Fn&& fn) {
            try {
                if (!fn()) {
                    throw nullptr;
                }
            } catch (...) {
                PyGILState_STATE gil = PyGILState_Ensure();
                handle_write_error();
                PyGILState_Release(gil);
            }
        }

        Persister* persister() const {
            return reinterpret_cast<Persister*>(target_obj);
        }

    public:
        explicit NativeConsumer(Persister* persister)
            : Consumer(reinterpret_cast<PyObject*>(persister)) {}

        bool quit_on_error() const override { return false; }

        void prepare_resume() override {
            if (persister()->native_writer()) persister()->native_writer()->stamp_pid();
        }

        void reset_state() override {
            persister()->reset_state();
        }

        void flush_background() override {
            run([&] { persister()->flush(); });
        }

        void consume_object(PyObject* obj) override {
            run_bool([&] { return persister()->write_object(obj); });
        }

        void consume_ref(Ref ref) override {
            run([&] { persister()->write_ref(ref); });
        }

        void consume_intern(PyObject* obj) override {
            run_bool([&] { return persister()->intern(obj); });
        }

        void consume_flush() override {
            run([&] { persister()->flush(); });
        }

        void consume_shutdown() override {
            run([&] { persister()->shutdown(); });
        }

        void consume_list(uint32_t len) override {
            run([&] { persister()->start_list(len); });
        }

        void consume_tuple(uint32_t len) override {
            run([&] { persister()->start_tuple(len); });
        }

        void consume_dict(uint32_t len) override {
            run([&] { persister()->start_dict(len); });
        }

        void consume_heartbeat() override {
            run([&] { persister()->write_heartbeat(); });
        }

        void consume_bind(Ref ref) override {
            run([&] { persister()->bind(ref); });
        }

        void consume_delete(Ref ref) override {
            run([&] { persister()->write_delete(ref); });
        }

        void consume_thread_switch(PyObject* obj) override {
            run_bool([&] { return persister()->write_thread_switch(obj); });
        }

        void consume_new_patched(PyObject* obj, PyTypeObject* type) override {
            run([&] { persister()->write_new_patched(obj, reinterpret_cast<PyObject*>(type)); });
        }

        void consume_new_ext_wrapped(PyTypeObject* type) override {
            run_bool([&] { return persister()->write_new_ext_wrapped(type); });
        }
    };

    class PythonConsumer : public Consumer {
        bool quit_on_error_value = false;

    public:
        explicit PythonConsumer(PyObject* consumer)
            : Consumer(consumer) {
            PyGILState_STATE gil = PyGILState_Ensure();
            PyObject* attr = PyObject_GetAttrString(target_obj, "quit_on_error");
            if (attr) {
                int truthy = PyObject_IsTrue(attr);
                if (truthy >= 0) quit_on_error_value = truthy != 0;
                else PyErr_Clear();
                Py_DECREF(attr);
            } else {
                PyErr_Clear();
            }
            PyGILState_Release(gil);
        }

        bool quit_on_error() const override {
            return quit_on_error_value;
        }

        void consume_intern(PyObject* obj) override {
            PyObject* method = PyObject_GetAttrString(target_obj, "consume_intern");
            if (!method) {
                PyErr_Clear();
                Consumer::consume_intern(obj);
                return;
            }

            PyObject* result = PyObject_CallOneArg(method, obj);
            Py_DECREF(method);
            if (!result) {
                handle_error();
                return;
            }
            Py_DECREF(result);
        }
    };

    Consumer* make_consumer(PyObject* target) {
        if (PyObject_TypeCheck(target, &Persister_Type)) {
            return new NativeConsumer(reinterpret_cast<Persister*>(target));
        }
        return new PythonConsumer(target);
    }
}
