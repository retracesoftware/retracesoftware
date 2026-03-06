#include "utils.h"
#include <exception>
#include <structmember.h>
#include "unordered_dense.h"
using namespace ankerl::unordered_dense;

namespace retracesoftware {

    static map<PyTypeObject *, allocfunc> allocfuncs;
    static map<PyTypeObject *, destructor> deallocfuncs;
    static map<PyObject *, PyObject *> dealloc_callbacks;
    static map<PyTypeObject *, PyObject *> alloc_callbacks;

    // ── DeallocBridge ────────────────────────────────────────────
    // Minimal callable that wraps a no-arg dealloc callback for use
    // as a weakref callback.  PyObject_ClearWeakRefs calls it with
    // the dead weakref as the sole positional argument.

    struct DeallocBridge : PyObject {
        PyObject *callback;
    };

    static void DeallocBridge_dealloc(DeallocBridge *self) {
        Py_XDECREF(self->callback);
        Py_TYPE(self)->tp_free((PyObject *)self);
    }

    static PyObject *DeallocBridge_call(PyObject *self, PyObject *args, PyObject *) {
        DeallocBridge *bridge = (DeallocBridge *)self;

        PyObject *exc_type, *exc_value, *exc_tb;
        PyErr_Fetch(&exc_type, &exc_value, &exc_tb);

        PyObject *r = PyObject_CallNoArgs(bridge->callback);
        Py_XDECREF(r);
        if (!r) PyErr_Clear();

        PyErr_Restore(exc_type, exc_value, exc_tb);

        PyObject *wr = PyTuple_GET_ITEM(args, 0);
        Py_DECREF(wr);

        Py_RETURN_NONE;
    }

    PyTypeObject DeallocBridge_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = "_retracesoftware_utils.DeallocBridge",
        .tp_basicsize = sizeof(DeallocBridge),
        .tp_dealloc = (destructor)DeallocBridge_dealloc,
        .tp_call = DeallocBridge_call,
        .tp_flags = Py_TPFLAGS_DEFAULT,
    };

    static PyObject *create_dealloc_bridge(PyObject *callback) {
        DeallocBridge *bridge = PyObject_New(DeallocBridge, &DeallocBridge_Type);
        if (!bridge) return nullptr;
        Py_INCREF(callback);
        bridge->callback = callback;
        return (PyObject *)bridge;
    }

    // ── helpers ──────────────────────────────────────────────────

    static PyObject * find_callback(PyTypeObject * cls) {
        while (cls) {
            auto it = alloc_callbacks.find(cls);
            if (it != alloc_callbacks.end()) return it->second;
            cls = cls->tp_base;
        }
        return nullptr;
    }

    static thread_local bool in_callback = false;

    static bool call_callback(PyObject * allocated) {
        if (in_callback || _Py_IsFinalizing()) return true;
        PyObject * callback = find_callback(Py_TYPE(allocated));
        if (!callback) return true;

        in_callback = true;
        PyObject * result = PyObject_CallOneArg(callback, allocated);
        in_callback = false;
        if (!result) return false;

        if (result == Py_None) {
            Py_DECREF(result);
            return true;
        }

        if (PyCallable_Check(result)) {
            PyTypeObject *tp = Py_TYPE(allocated);
            if ((tp->tp_flags & Py_TPFLAGS_HEAPTYPE) && tp->tp_weaklistoffset) {
                PyObject *bridge = create_dealloc_bridge(result);
                Py_DECREF(result);
                if (!bridge) return false;
                PyObject *wr = PyWeakref_NewRef(allocated, bridge);
                Py_DECREF(bridge);
                if (!wr) return false;
            } else {
                dealloc_callbacks[allocated] = result;
            }
            return true;
        }

        Py_DECREF(result);
        PyErr_Format(PyExc_TypeError,
            "__retrace_on_alloc__ callback must return None or a callable, "
            "got %.200s", Py_TYPE(result)->tp_name);
        return false;
    }

    // ── dealloc callback helpers ────────────────────────────────

    static PyObject * take_dealloc_callback(PyObject * obj) {
        auto it = dealloc_callbacks.find(obj);
        if (it == dealloc_callbacks.end()) return nullptr;
        PyObject * cb = it->second;
        dealloc_callbacks.erase(it);
        return cb;
    }

    static void fire_dealloc_callback(PyObject * cb) {
        PyObject *exc_type, *exc_value, *exc_tb;
        PyErr_Fetch(&exc_type, &exc_value, &exc_tb);

        PyObject * r = PyObject_CallNoArgs(cb);
        Py_XDECREF(r);
        Py_DECREF(cb);
        if (!r) PyErr_Clear();

        PyErr_Restore(exc_type, exc_value, exc_tb);
    }

    // ── generic_dealloc (fallback for non-weakref C types) ───────

    static void generic_dealloc(PyObject * obj) {
        PyObject * cb = _Py_IsFinalizing() ? nullptr : take_dealloc_callback(obj);

        PyTypeObject * type = Py_TYPE(obj);
        while (type && type->tp_dealloc != generic_dealloc)
            type = type->tp_base;
        if (type) {
            auto dit = deallocfuncs.find(type);
            if (dit != deallocfuncs.end())
                dit->second(obj);
        }

        if (cb) fire_dealloc_callback(cb);
    }

    // ── alloc patching ───────────────────────────────────────────

    static PyObject * generic_alloc(PyTypeObject *type, Py_ssize_t nitems) {

        auto it = allocfuncs.find(type);
        if (it == allocfuncs.end()) {
            PyErr_Format(PyExc_RuntimeError, "Original tp_alloc mapping for type: %S not found",
                            type);
            return nullptr;
        }
        PyObject * obj = it->second(type, nitems);

        if (obj) {
            if (!call_callback(obj)) {
                Py_DECREF(obj);
                return nullptr;
            }
        }
        return obj;
    }

    static PyObject * PyType_GenericAlloc_Wrapper(PyTypeObject *type, Py_ssize_t nitems) {

        PyObject * obj = PyType_GenericAlloc(type, nitems);

        if (obj) {
            if (!call_callback(obj)) {
                Py_DECREF(obj);
                return nullptr;
            }
        }
        return obj;
    }

    static bool is_alloc_patched(allocfunc func) {
        return func == PyType_GenericAlloc_Wrapper || func == generic_alloc;
    }

    static void patch_alloc(PyTypeObject * cls) {
        if (cls->tp_alloc == PyType_GenericAlloc) {
            cls->tp_alloc = PyType_GenericAlloc_Wrapper;
        } else {
            allocfuncs[cls] = cls->tp_alloc;
            cls->tp_alloc = generic_alloc;
        }
    }

    // ── dealloc patching (fallback for non-weakref types) ────────

    static destructor get_subtype_dealloc() {
        static destructor cached = nullptr;
        if (!cached) {
            PyObject *probe = PyObject_CallFunction((PyObject *)&PyType_Type,
                "s(O){}", "_P", (PyObject *)&PyBaseObject_Type);
            cached = ((PyTypeObject *)probe)->tp_dealloc;
            Py_DECREF(probe);
        }
        return cached;
    }

    static void replacement_subtype_dealloc(PyObject * obj) {
        PyTypeObject * type = Py_TYPE(obj);
        bool entry = (type->tp_dealloc == replacement_subtype_dealloc);

        if (entry) {
            bool gc = type->tp_flags & Py_TPFLAGS_HAVE_GC;
            if (gc) PyObject_GC_UnTrack(obj);

            if (type->tp_finalize) {
                if (gc) PyObject_GC_Track(obj);
                PyObject_CallFinalizer(obj);
                if (Py_REFCNT(obj) > 0)
                    return;
                if (gc) PyObject_GC_UnTrack(obj);
            }

            if (type->tp_weaklistoffset) {
                PyObject **list = (PyObject **)((char *)obj + type->tp_weaklistoffset);
                if (*list != NULL)
                    PyObject_ClearWeakRefs(obj);
            }

            PyObject **dictptr = _PyObject_GetDictPtr(obj);
            if (dictptr && *dictptr)
                Py_CLEAR(*dictptr);
        }

        PyObject * cb = take_dealloc_callback(obj);

        PyBaseObject_Type.tp_dealloc(obj);

        if (entry && (type->tp_flags & Py_TPFLAGS_HEAPTYPE))
            Py_DECREF(type);

        if (cb) fire_dealloc_callback(cb);
    }

    static void patch_dealloc(PyTypeObject * cls) {
        if (!cls->tp_dealloc
            || cls->tp_dealloc == generic_dealloc
            || cls->tp_dealloc == replacement_subtype_dealloc) return;
        if (cls->tp_dealloc == get_subtype_dealloc()) {
            cls->tp_dealloc = replacement_subtype_dealloc;
        } else {
            deallocfuncs[cls] = cls->tp_dealloc;
            cls->tp_dealloc = generic_dealloc;
        }
    }

    static void patch_alloc_subclasses(PyTypeObject * type) {
        PyObject * subs = PyObject_CallMethod((PyObject *)type, "__subclasses__", NULL);
        if (!subs) { PyErr_Clear(); return; }

        Py_ssize_t n = PyList_Size(subs);
        for (Py_ssize_t i = 0; i < n; i++) {
            PyTypeObject * sub = (PyTypeObject *)PyList_GetItem(subs, i);
            if (!is_alloc_patched(sub->tp_alloc))
                patch_alloc(sub);
            patch_alloc_subclasses(sub);
        }
        Py_DECREF(subs);
    }

    bool set_on_alloc(PyTypeObject *type, PyObject * callback) {
        assert(type);
        assert(callback);
        
        if (type->tp_base == NULL) {
            PyErr_Format(PyExc_TypeError,
                "set_on_alloc cannot patch the root object type");
            return false;
        }
        if (alloc_callbacks.find(type) != alloc_callbacks.end()) {
            PyErr_Format(PyExc_RuntimeError,
                "set_on_alloc: type '%.200s' is already patched; "
                "each type may only be patched once per process",
                type->tp_name);
            return false;
        }
        if (!is_alloc_patched(type->tp_alloc))
            patch_alloc(type);
        patch_alloc_subclasses(type);

        if (!((type->tp_flags & Py_TPFLAGS_HEAPTYPE) && type->tp_weaklistoffset))
            patch_dealloc(type);

        Py_INCREF(callback);
        alloc_callbacks[type] = callback;
        return true;
    }
}
