#include "consumer.h"

#include "persister.h"
#include "stream.h"

namespace retracesoftware_stream {
    namespace {
        std::string format_current_error_message_preserving_exception() {
            if (!PyErr_Occurred()) return "consumer error";

            PyObject* exc_type = nullptr;
            PyObject* exc_value = nullptr;
            PyObject* exc_tb = nullptr;
            PyErr_Fetch(&exc_type, &exc_value, &exc_tb);
            PyErr_NormalizeException(&exc_type, &exc_value, &exc_tb);

            std::string message = "consumer error";
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
            PyErr_Restore(exc_type, exc_value, exc_tb);
            return message;
        }
    }

    void Consumer::capture_current_error() {
        has_error_message = true;
        last_error_message = format_current_error_message_preserving_exception();
    }

    bool Consumer::call0(const char* name) {
        PyGILState_STATE gil = PyGILState_Ensure();
        PyObject* result = PyObject_CallMethod(target_obj, name, nullptr);
        if (!result) {
            capture_current_error();
            PyGILState_Release(gil);
            return false;
        }
        Py_DECREF(result);
        PyGILState_Release(gil);
        return true;
    }

    bool Consumer::call_obj(const char* name, PyObject* obj) {
        PyGILState_STATE gil = PyGILState_Ensure();
        PyObject* result = PyObject_CallMethod(target_obj, name, "O", obj);
        if (!result) {
            capture_current_error();
            PyGILState_Release(gil);
            return false;
        }
        Py_DECREF(result);
        PyGILState_Release(gil);
        return true;
    }

    bool Consumer::call_obj_obj(const char* name, PyObject* a, PyObject* b) {
        PyGILState_STATE gil = PyGILState_Ensure();
        PyObject* result = PyObject_CallMethod(target_obj, name, "OO", a, b);
        if (!result) {
            capture_current_error();
            PyGILState_Release(gil);
            return false;
        }
        Py_DECREF(result);
        PyGILState_Release(gil);
        return true;
    }

    bool Consumer::call_u64(const char* name, uint64_t value) {
        PyGILState_STATE gil = PyGILState_Ensure();
        PyObject* result = PyObject_CallMethod(
            target_obj,
            name,
            "K",
            (unsigned long long)value);
        if (!result) {
            capture_current_error();
            PyGILState_Release(gil);
            return false;
        }
        Py_DECREF(result);
        PyGILState_Release(gil);
        return true;
    }

    bool Consumer::call_optional0(const char* name) {
        PyGILState_STATE gil = PyGILState_Ensure();
        PyObject* method = PyObject_GetAttrString(target_obj, name);
        if (!method) {
            PyErr_Clear();
            PyGILState_Release(gil);
            return true;
        }
        PyObject* result = PyObject_CallNoArgs(method);
        Py_DECREF(method);
        if (!result) {
            PyGILState_Release(gil);
            return false;
        }
        Py_DECREF(result);
        PyGILState_Release(gil);
        return true;
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

    bool Consumer::has_error() const {
        return has_error_message;
    }

    std::string Consumer::take_error_message() {
        has_error_message = false;
        std::string message = last_error_message.empty() ? "consumer error" : last_error_message;
        last_error_message.clear();
        return message;
    }

    void Consumer::prepare_resume() {}

    bool Consumer::flush_background() { return true; }

    void Consumer::reset_state() {
        if (!call_optional0("reset_state")) {
            PyErr_Clear();
        }
    }

    bool Consumer::consume_object(PyObject* obj) {
        return call_obj("consume_object", obj);
    }

    bool Consumer::consume_ref(Ref ref) {
        return call_u64("consume_ref", (uint64_t)(uintptr_t)ref);
    }

    bool Consumer::consume_intern(PyObject* obj) {
        return consume_object(obj) && consume_bind(reinterpret_cast<Ref>(obj));
    }

    bool Consumer::consume_flush() {
        return call0("consume_flush");
    }

    bool Consumer::consume_shutdown() {
        return call0("consume_shutdown");
    }

    bool Consumer::consume_list(uint32_t len) {
        return call_u64("consume_list", len);
    }

    bool Consumer::consume_tuple(uint32_t len) {
        return call_u64("consume_tuple", len);
    }

    bool Consumer::consume_dict(uint32_t len) {
        return call_u64("consume_dict", len);
    }

    bool Consumer::consume_heartbeat() {
        return call0("consume_heartbeat");
    }

    bool Consumer::consume_bind(Ref ref) {
        return call_u64("consume_bind", (uint64_t)(uintptr_t)ref);
    }

    bool Consumer::consume_delete(Ref ref) {
        return call_u64("consume_delete", (uint64_t)(uintptr_t)ref);
    }

    bool Consumer::consume_thread_switch(PyObject* obj) {
        return call_obj("consume_thread_switch", obj);
    }

    bool Consumer::consume_new_patched(PyObject* obj, PyTypeObject* type) {
        return call_obj_obj("consume_new_patched", obj, reinterpret_cast<PyObject*>(type));
    }

    bool Consumer::consume_new_ext_wrapped(PyTypeObject* type) {
        return call_obj("consume_new_ext_wrapped", reinterpret_cast<PyObject*>(type));
    }

    class NativeConsumer : public Consumer {
        template <typename Fn>
        bool run(Fn&& fn) {
            try {
                fn();
                return true;
            } catch (...) {
                PyGILState_STATE gil = PyGILState_Ensure();
                set_python_error_from_current_exception();
                capture_current_error();
                PyGILState_Release(gil);
                return false;
            }
        }

        template <typename Fn>
        bool run_bool(Fn&& fn) {
            try {
                if (!fn()) {
                    PyGILState_STATE gil = PyGILState_Ensure();
                    if (!PyErr_Occurred()) {
                        PyErr_SetString(PyExc_RuntimeError, "Persister operation failed");
                    }
                    capture_current_error();
                    PyGILState_Release(gil);
                    return false;
                }
                return true;
            } catch (...) {
                PyGILState_STATE gil = PyGILState_Ensure();
                set_python_error_from_current_exception();
                capture_current_error();
                PyGILState_Release(gil);
                return false;
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

        bool flush_background() override {
            return run([&] { persister()->flush(); });
        }

        bool consume_object(PyObject* obj) override {
            return run_bool([&] { return persister()->write_object(obj); });
        }

        bool consume_ref(Ref ref) override {
            return run([&] { persister()->write_ref(ref); });
        }

        bool consume_intern(PyObject* obj) override {
            return run_bool([&] { return persister()->intern(obj); });
        }

        bool consume_flush() override {
            return run([&] { persister()->flush(); });
        }

        bool consume_shutdown() override {
            return run([&] { persister()->shutdown(); });
        }

        bool consume_list(uint32_t len) override {
            return run([&] { persister()->start_list(len); });
        }

        bool consume_tuple(uint32_t len) override {
            return run([&] { persister()->start_tuple(len); });
        }

        bool consume_dict(uint32_t len) override {
            return run([&] { persister()->start_dict(len); });
        }

        bool consume_heartbeat() override {
            return run([&] { persister()->write_heartbeat(); });
        }

        bool consume_bind(Ref ref) override {
            return run([&] { persister()->bind(ref); });
        }

        bool consume_delete(Ref ref) override {
            return run([&] { persister()->write_delete(ref); });
        }

        bool consume_thread_switch(PyObject* obj) override {
            return run_bool([&] { return persister()->write_thread_switch(obj); });
        }

        bool consume_new_patched(PyObject* obj, PyTypeObject* type) override {
            return run([&] { persister()->write_new_patched(obj, reinterpret_cast<PyObject*>(type)); });
        }

        bool consume_new_ext_wrapped(PyTypeObject* type) override {
            return run_bool([&] { return persister()->write_new_ext_wrapped(type); });
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

        bool consume_intern(PyObject* obj) override {
            PyObject* method = PyObject_GetAttrString(target_obj, "consume_intern");
            if (!method) {
                PyErr_Clear();
                return Consumer::consume_intern(obj);
            }

            PyObject* result = PyObject_CallOneArg(method, obj);
            Py_DECREF(method);
            if (!result) {
                capture_current_error();
                return false;
            }
            Py_DECREF(result);
            return true;
        }
    };

    Consumer* make_consumer(PyObject* target) {
        if (PyObject_TypeCheck(target, &Persister_Type)) {
            return new NativeConsumer(reinterpret_cast<Persister*>(target));
        }
        return new PythonConsumer(target);
    }
}
