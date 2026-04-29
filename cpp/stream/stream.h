#pragma once
#include <Python.h>
// #include <nanobind/nanobind.h>

#include <algorithm>
#include <atomic>
#include <signal.h>
#include <string>
#include <vector>
#include <tuple>
#include "unordered_dense.h"

#include "fastcall.h"
#include "gilguard.h"
#include "queue.h"

#if defined(__GNUC__) || defined(__clang__)
#include <alloca.h>
#elif defined(_MSC_VER)
#include <malloc.h>
#define alloca _alloca
#endif

#define MODULE "retracesoftware_stream."

#define OFFSET_OF_MEMBER(type, member) \
    ((Py_ssize_t) &reinterpret_cast<const volatile char&>((((type*)0)->member)))

#define SMALL_ARGS 5

//            0xRETRACESOFTWARE
//            123456789ABCDEF

// namespace nb = nanobind;
using namespace ankerl::unordered_dense;

namespace retracesoftware_stream {

#if PY_VERSION_HEX >= 0x030C0000
    inline bool is_immortal(PyObject* obj) { return _Py_IsImmortal(obj); }
#else
    inline bool is_immortal(PyObject* obj) { return obj == Py_None || obj == Py_True || obj == Py_False; }
#endif

    inline bool is_interned_unicode(PyObject* obj) {
#if PY_VERSION_HEX >= 0x030B0000
        return PyUnicode_CHECK_INTERNED(obj) != SSTATE_NOT_INTERNED;
#else
        return PyUnicode_IsInterned(obj);
#endif
    }


    struct PyDeleter {
        void operator()(PyObject* p) const { Py_XDECREF(p); }
    };

    using PyUniquePtr = std::unique_ptr<PyObject, PyDeleter>;
    using PySafePtr = std::unique_ptr<PyObject, decltype(&Py_XDECREF)>;

    // Convert any range of PyObject* → new Python list
    // Works with: vector<PyObject*>, span, views, filter_view, etc.
    template<std::ranges::input_range R>
        requires std::same_as<std::ranges::range_value_t<R>, PyObject*>
    bool pylist_concat(PyObject * list, R&& range)
    {
        for (PyObject* item : std::forward<R>(range)) {
            if (PyList_Append(list, item) < 0) {
                return false;       
            }
        }
        return true;
    }

    template<std::ranges::input_range R>
        requires std::same_as<std::ranges::range_value_t<R>, PyObject*>
    PyObject* to_pylist(R&& range)
    {
        // Get size if possible (fast path)
        Py_ssize_t size = 0;
        if constexpr (std::ranges::sized_range<R>) {
            size = static_cast<Py_ssize_t>(std::ranges::size(range));
        } else {
            size = static_cast<Py_ssize_t>(std::distance(range.begin(), range.end()));
        }

        PyUniquePtr list(PyList_New(size));
        if (!list) return nullptr;

        Py_ssize_t i = 0;
        for (PyObject* item : std::forward<R>(range)) {
            // You must own a reference to item!
            // PyList_SetItem steals it — perfect
            if (PyList_SetItem(list.get(), i++, Py_NewRef(item)) < 0) {
                return nullptr;  // exception already set
            }
            // Do NOT decref item — SetItem stole it
        }

        return list.release();  // transfer ownership to caller
    }    

    extern PyTypeObject ObjectWriter_Type;
    extern PyTypeObject ObjectReader_Type;
    extern PyTypeObject ObjectStream_Type;
    extern PyTypeObject TapeReader_Type;
    extern PyTypeObject Persister_Type;
    extern PyTypeObject FramedWriter_Type;
    extern PyTypeObject Binding_Type;
    extern PyTypeObject Binder_Type;
    extern PyTypeObject WeakBindingCallback_Type;

    void handle_write_error();
    void handle_debug_error(bool quit_on_error);
    void set_python_error_from_current_exception();

    class FramedWriter;
    FramedWriter* FramedWriter_get(PyObject* obj);

    bool Binding_Check(PyObject * obj);
    uint64_t Binding_Handle(PyObject * obj);
    PyObject * Binding_New(uint64_t handle);
    bool AddBindSupport(PyTypeObject * type);
    bool RemoveBindSupport(PyTypeObject * type);
    bool GetExactBindSupportOriginalDealloc(PyTypeObject * type, destructor * out);
    PyObject * Binder_Bind(PyObject * binder, PyObject * obj);
    PyObject * Binder_Lookup(PyObject * binder, PyObject * obj);

    // extern PyTypeObject ObjectReader_Type;

    inline void generic_gc_dealloc(PyObject *self) {

        PyObject_GC_UnTrack(self);

        // if (Py_TYPE(self)->tp_dealloc == generic_gc_dealloc) {
        //     PyObject_GC_UnTrack(self);
        // }
        // else {
        //     raise(SIGTRAP);
        // }

        PyTypeObject *tp = Py_TYPE(self);

        if (tp->tp_weaklistoffset) {
            PyObject_ClearWeakRefs(self);
        }

        if (tp->tp_clear) {
            tp->tp_clear(self);
        }
        tp->tp_free(self);
    }

    class PyObjectPtr {
        PyObject * ptr;
    public:
        inline PyObjectPtr(PyObject * obj) : ptr(obj) {}

        inline PyObject * get() const { return ptr; }
        
        inline PyObject * get_or_none() const { return ptr ? ptr : Py_None; }

        inline PyTypeObject * type() const {
            return Py_TYPE(ptr);
        }

        inline void assert_exact_type(PyTypeObject * cls) {
            if (Py_TYPE(ptr) != cls) {
                PyErr_Format(PyExc_RuntimeError, "Object: %s, was not expected type: %S", ptr, cls);
                throw std::exception();    
            }
        }

        // inline PyObject * resolve() {
        //     auto modname = PyObjectPtr(get_attr("__module__"));
        //     auto name = PyObjectPtr(get_attr("__name__"));

        //     return ::resolve(modname.get(), name.get());
        // }

        inline PyObject * get_attr(const char * name) const {
            PyObject * result = PyObject_GetAttrString(ptr, name);
            if (!result) {
                assert(PyErr_Occurred());
                throw std::exception();
            }
            return result;
        }

        inline PyObject * get_attr(PyObjectPtr &name) const {
            PyObject * result = PyObject_GetAttr(ptr, name.ptr);
            if (!result) {
                assert(PyErr_Occurred());
                throw std::exception();
            }
            return result;
        }

        inline ~PyObjectPtr() {
            Py_XDECREF(ptr);
        }
    };
}
