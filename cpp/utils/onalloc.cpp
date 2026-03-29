#include "utils.h"
#include <exception>
#include <structmember.h>
#include <vector>
#include "unordered_dense.h"
using namespace ankerl::unordered_dense;

namespace retracesoftware {

    struct TypePatchState : PyObject {
        PyTypeObject *type;
        PyObject *alloc_callback;
        PyObject *type_weakref;
        allocfunc original_alloc;
        destructor original_dealloc;
        bool alloc_patched;
        bool dealloc_patched;
        TypePatchState *alloc_owner;
    };

    static map<PyTypeObject *, TypePatchState *> type_patches;
    static map<PyObject *, PyObject *> instance_dealloc_callbacks;

    static PyObject * generic_alloc(PyTypeObject *type, Py_ssize_t nitems);
    static void generic_dealloc(PyObject * obj);
    static void replacement_subtype_dealloc(PyObject * obj);

    static void clear_type_patch(TypePatchState *state) {
        bool drop_map_ref = false;
        bool type_alive = state->type
            && (!state->type_weakref || PyWeakref_GetObject(state->type_weakref) != Py_None);

        std::vector<TypePatchState *> owned_alloc_patches;
        for (auto const &entry : type_patches) {
            TypePatchState *candidate = entry.second;
            if (candidate != state && candidate->alloc_owner == state) {
                owned_alloc_patches.push_back(candidate);
            }
        }

        for (TypePatchState *owned : owned_alloc_patches) {
            clear_type_patch(owned);
        }

        if (type_alive) {
            if (state->alloc_patched && state->type->tp_alloc == generic_alloc) {
                state->type->tp_alloc = state->original_alloc
                    ? state->original_alloc
                    : PyType_GenericAlloc;
            }
            if (state->dealloc_patched
                && (state->type->tp_dealloc == generic_dealloc
                    || state->type->tp_dealloc == replacement_subtype_dealloc)) {
                state->type->tp_dealloc = state->original_dealloc;
            }
            PyType_Modified(state->type);
        }

        if (state->type) {
            auto it = type_patches.find(state->type);
            if (it != type_patches.end() && it->second == state) {
                type_patches.erase(it);
                drop_map_ref = true;
            }
        }

        state->type = nullptr;
        state->original_alloc = nullptr;
        state->original_dealloc = nullptr;
        state->alloc_patched = false;
        state->dealloc_patched = false;
        state->alloc_owner = nullptr;
        Py_CLEAR(state->alloc_callback);
        Py_CLEAR(state->type_weakref);

        if (drop_map_ref)
            Py_DECREF((PyObject *)state);
    }

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

    static void TypePatchState_dealloc(TypePatchState *self) {
        Py_XDECREF(self->alloc_callback);
        Py_XDECREF(self->type_weakref);
        Py_TYPE(self)->tp_free((PyObject *)self);
    }

    static PyObject *TypePatchState_call(PyObject *self, PyObject *, PyObject *) {
        TypePatchState *state = (TypePatchState *)self;
        Py_INCREF(self);

        PyObject *exc_type, *exc_value, *exc_tb;
        PyErr_Fetch(&exc_type, &exc_value, &exc_tb);

        clear_type_patch(state);

        PyErr_Restore(exc_type, exc_value, exc_tb);
        Py_DECREF(self);
        Py_RETURN_NONE;
    }

    PyTypeObject TypePatchState_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = "_retracesoftware_utils.TypePatchState",
        .tp_basicsize = sizeof(TypePatchState),
        .tp_dealloc = (destructor)TypePatchState_dealloc,
        .tp_call = TypePatchState_call,
        .tp_flags = Py_TPFLAGS_DEFAULT,
    };

    static PyObject *create_dealloc_bridge(PyObject *callback) {
        DeallocBridge *bridge = PyObject_New(DeallocBridge, &DeallocBridge_Type);
        if (!bridge) return nullptr;
        Py_INCREF(callback);
        bridge->callback = callback;
        return (PyObject *)bridge;
    }

    static TypePatchState *get_type_patch(PyTypeObject * type) {
        auto it = type_patches.find(type);
        if (it == type_patches.end()) return nullptr;
        return it->second;
    }

    static TypePatchState *find_type_patch(PyTypeObject * type) {
        while (type) {
            TypePatchState *state = get_type_patch(type);
            if (state) return state;
            type = type->tp_base;
        }
        return nullptr;
    }

    static TypePatchState *find_callback_patch(PyTypeObject * type) {
        while (type) {
            TypePatchState *state = get_type_patch(type);
            if (state && state->alloc_callback) return state;
            type = type->tp_base;
        }
        return nullptr;
    }

    static TypePatchState *find_dealloc_patch(PyTypeObject * type) {
        while (type) {
            TypePatchState *state = get_type_patch(type);
            if (state && state->original_dealloc) return state;
            type = type->tp_base;
        }
        return nullptr;
    }

    static TypePatchState *ensure_type_patch(PyTypeObject * type) {
        TypePatchState *state = get_type_patch(type);
        if (state) return state;

        state = PyObject_New(TypePatchState, &TypePatchState_Type);
        if (!state) return nullptr;

        state->type = type;
        state->alloc_callback = nullptr;
        state->type_weakref = nullptr;
        state->original_alloc = nullptr;
        state->original_dealloc = nullptr;
        state->alloc_patched = false;
        state->dealloc_patched = false;
        state->alloc_owner = nullptr;

        if (type->tp_flags & Py_TPFLAGS_HEAPTYPE) {
            PyObject *wr = PyWeakref_NewRef((PyObject *)type, (PyObject *)state);
            if (!wr) {
                Py_DECREF((PyObject *)state);
                return nullptr;
            }
            state->type_weakref = wr;
        }

        type_patches[type] = state;
        return state;
    }

    // ── helpers ──────────────────────────────────────────────────

    static thread_local bool in_callback = false;

    static bool call_callback(PyObject * allocated) {
        if (in_callback || _Py_IsFinalizing()) return true;
        TypePatchState *state = find_callback_patch(Py_TYPE(allocated));
        if (!state) return true;

        in_callback = true;
        PyObject * result = PyObject_CallOneArg(state->alloc_callback, allocated);
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
                instance_dealloc_callbacks[allocated] = result;
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
        auto it = instance_dealloc_callbacks.find(obj);
        if (it == instance_dealloc_callbacks.end()) return nullptr;
        PyObject * cb = it->second;
        instance_dealloc_callbacks.erase(it);
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

        TypePatchState *state = find_dealloc_patch(Py_TYPE(obj));
        if (state)
            state->original_dealloc(obj);

        if (cb) fire_dealloc_callback(cb);
    }

    // ── alloc patching ───────────────────────────────────────────

    static PyObject * generic_alloc(PyTypeObject *type, Py_ssize_t nitems) {
        TypePatchState *state = get_type_patch(type);
        if (!state) {
            PyErr_Format(PyExc_RuntimeError, "Original tp_alloc mapping for type: %S not found",
                            type);
            return nullptr;
        }
        allocfunc original = state->original_alloc ? state->original_alloc : PyType_GenericAlloc;
        PyObject * obj = original(type, nitems);

        if (obj) {
            if (!call_callback(obj)) {
                Py_DECREF(obj);
                return nullptr;
            }
        }
        return obj;
    }

    bool is_alloc_patched(allocfunc func) {
        return func == generic_alloc;
    }

    static bool patch_alloc(PyTypeObject * cls, TypePatchState *owner) {
        TypePatchState *state = ensure_type_patch(cls);
        if (!state) return false;
        if (state->alloc_patched) return true;

        state->original_alloc = cls->tp_alloc == PyType_GenericAlloc ? nullptr : cls->tp_alloc;
        state->alloc_patched = true;
        state->alloc_owner = owner;
        cls->tp_alloc = generic_alloc;
        PyType_Modified(cls);
        return true;
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

    static bool patch_dealloc(PyTypeObject * cls) {
        if (!cls->tp_dealloc
            || cls->tp_dealloc == generic_dealloc
            || cls->tp_dealloc == replacement_subtype_dealloc) return true;
        TypePatchState *state = ensure_type_patch(cls);
        if (!state) return false;
        if (state->dealloc_patched) return true;
        state->original_dealloc = cls->tp_dealloc;
        state->dealloc_patched = true;
        if (cls->tp_dealloc == get_subtype_dealloc()) {
            cls->tp_dealloc = replacement_subtype_dealloc;
        } else {
            cls->tp_dealloc = generic_dealloc;
        }
        PyType_Modified(cls);
        return true;
    }

    static bool patch_alloc_subclasses(PyTypeObject * type, TypePatchState *owner) {
        PyObject * subs = PyObject_CallMethod((PyObject *)type, "__subclasses__", NULL);
        if (!subs) { PyErr_Clear(); return true; }

        Py_ssize_t n = PyList_Size(subs);
        for (Py_ssize_t i = 0; i < n; i++) {
            PyTypeObject * sub = (PyTypeObject *)PyList_GetItem(subs, i);
            if (!is_alloc_patched(sub->tp_alloc))
                if (!patch_alloc(sub, owner)) {
                    Py_DECREF(subs);
                    return false;
                }
            if (!patch_alloc_subclasses(sub, owner)) {
                Py_DECREF(subs);
                return false;
            }
        }
        Py_DECREF(subs);
        return true;
    }

    PyObject * set_on_alloc(PyTypeObject *type, PyObject * callback) {
        assert(type);
        assert(callback);
        
        if (type->tp_base == NULL) {
            PyErr_Format(PyExc_TypeError,
                "set_on_alloc cannot patch the root object type");
            return nullptr;
        }
        TypePatchState *state = ensure_type_patch(type);
        if (!state) return nullptr;

        if (state->alloc_callback) {
            bool stale_heap_type = state->type_weakref
                && PyWeakref_GetObject(state->type_weakref) == Py_None;
            if (stale_heap_type) {
                clear_type_patch(state);
                state = ensure_type_patch(type);
                if (!state) return nullptr;
            } else {
                PyErr_Format(PyExc_RuntimeError,
                    "set_on_alloc: type '%.200s' is already patched; "
                    "each type may only be patched once per process",
                    type->tp_name);
                return nullptr;
            }
        }
        if (!is_alloc_patched(type->tp_alloc))
            if (!patch_alloc(type, state))
                return nullptr;
        if (!patch_alloc_subclasses(type, state)) {
            clear_type_patch(state);
            return nullptr;
        }

        if (!((type->tp_flags & Py_TPFLAGS_HEAPTYPE) && type->tp_weaklistoffset))
            if (!patch_dealloc(type)) {
                clear_type_patch(state);
                return nullptr;
            }

        Py_INCREF(callback);
        state->alloc_callback = callback;
        state->alloc_owner = state;
        return Py_NewRef((PyObject *)state);
    }
}
