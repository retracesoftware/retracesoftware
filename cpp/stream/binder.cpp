#include "stream.h"
#include <structmember.h>
#include <cstdint>
#include <cstddef>
#include <new>
#include <vector>
#include "unordered_dense.h"

using namespace ankerl::unordered_dense;

namespace retracesoftware_stream {

    static destructor get_subtype_dealloc();

    static bool current_thread_id(uint64_t * out) {
        PyObject * module = PyImport_ImportModule("_thread");
        if (!module) {
            return false;
        }
        PyObject * get_ident = PyObject_GetAttrString(module, "get_ident");
        Py_DECREF(module);
        if (!get_ident) {
            return false;
        }
        PyObject * ident = PyObject_CallNoArgs(get_ident);
        Py_DECREF(get_ident);
        if (!ident) {
            return false;
        }
        unsigned long long value = PyLong_AsUnsignedLongLong(ident);
        Py_DECREF(ident);
        if (PyErr_Occurred()) {
            return false;
        }
        *out = static_cast<uint64_t>(value);
        return true;
    }

    static bool uint64_from_object(PyObject * value, uint64_t * out) {
        unsigned long long parsed = PyLong_AsUnsignedLongLong(value);
        if (PyErr_Occurred()) {
            return false;
        }
        *out = static_cast<uint64_t>(parsed);
        return true;
    }

    static bool handle_from_tuple(PyObject * value, uint64_t * thread_id, uint64_t * index) {
        if (PyTuple_GET_SIZE(value) != 2) {
            PyErr_SetString(PyExc_TypeError, "Binding handle tuple must have length 2");
            return false;
        }
        return uint64_from_object(PyTuple_GET_ITEM(value, 0), thread_id)
            && uint64_from_object(PyTuple_GET_ITEM(value, 1), index);
    }

    struct Binding : PyTupleObject {
        static PyObject * from_values(PyTypeObject * type, uint64_t thread_id, uint64_t index) {
            PyObject * thread_obj = PyLong_FromUnsignedLongLong(thread_id);
            if (!thread_obj) {
                return nullptr;
            }
            PyObject * index_obj = PyLong_FromUnsignedLongLong(index);
            if (!index_obj) {
                Py_DECREF(thread_obj);
                return nullptr;
            }
            PyObject * values = PyTuple_Pack(2, thread_obj, index_obj);
            Py_DECREF(thread_obj);
            Py_DECREF(index_obj);
            if (!values) {
                return nullptr;
            }
            PyObject * tuple_args = PyTuple_Pack(1, values);
            Py_DECREF(values);
            if (!tuple_args) {
                return nullptr;
            }
            PyObject * self = PyTuple_Type.tp_new(type, tuple_args, nullptr);
            Py_DECREF(tuple_args);
            return self;
        }

        static PyObject * py_new(PyTypeObject * type, PyObject * args, PyObject * kwargs) {
            static const char * kwlist[] = {"index", nullptr};
            PyObject * value = nullptr;

            if (!PyArg_ParseTupleAndKeywords(
                    args, kwargs, "O", (char **)kwlist, &value)) {
                return nullptr;
            }

            uint64_t thread_id = 0;
            uint64_t index = 0;
            if (PyTuple_Check(value)) {
                if (!handle_from_tuple(value, &thread_id, &index)) {
                    return nullptr;
                }
            } else {
                if (!uint64_from_object(value, &index)) {
                    return nullptr;
                }
                if (!current_thread_id(&thread_id)) {
                    return nullptr;
                }
            }
            return from_values(type, thread_id, index);
        }

        static uint64_t item(Binding * self, Py_ssize_t index) {
            PyObject * value = PyTuple_GET_ITEM(reinterpret_cast<PyObject *>(self), index);
            return static_cast<uint64_t>(PyLong_AsUnsignedLongLong(value));
        }

        static PyObject * handle_get(Binding * self, void *) {
            return Py_NewRef(reinterpret_cast<PyObject *>(self));
        }

        static PyObject * thread_id_get(Binding * self, void *) {
            return Py_NewRef(PyTuple_GET_ITEM(reinterpret_cast<PyObject *>(self), 0));
        }

        static PyObject * index_get(Binding * self, void *) {
            return Py_NewRef(PyTuple_GET_ITEM(reinterpret_cast<PyObject *>(self), 1));
        }

        static PyObject * repr(Binding * self) {
            return PyUnicode_FromFormat(
                "Binding(%llu, %llu)",
                static_cast<unsigned long long>(item(self, 0)),
                static_cast<unsigned long long>(item(self, 1)));
        }

        static PyObject * int_(Binding * self) {
            return PyLong_FromUnsignedLongLong(
                static_cast<unsigned long long>(item(self, 1)));
        }
    };

    static PyGetSetDef Binding_getset[] = {
        {"handle", (getter)Binding::handle_get, nullptr, "Opaque binding handle", nullptr},
        {"thread_id", (getter)Binding::thread_id_get, nullptr, "Binding thread id", nullptr},
        {"index", (getter)Binding::index_get, nullptr, "Per-thread binding index", nullptr},
        {nullptr}
    };

    static PyNumberMethods Binding_number_methods = {
        .nb_int = (unaryfunc)Binding::int_,
    };

    PyTypeObject Binding_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "Binding",
        .tp_basicsize = offsetof(PyTupleObject, ob_item),
        .tp_itemsize = sizeof(PyObject *),
        .tp_repr = (reprfunc)Binding::repr,
        .tp_as_number = &Binding_number_methods,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
        .tp_getset = Binding_getset,
        .tp_base = &PyTuple_Type,
        .tp_new = Binding::py_new,
    };

    struct Binder;

    struct WeakBindingEntry {
        PyObject * weakref;
        PyObject * binding;
    };

    struct WeakBindingCallback : PyObject {
        Binder * owner;
        PyObject * key;

        static PyObject * call(PyObject * self_obj, PyObject * args, PyObject * kwargs);
        static void dealloc(WeakBindingCallback * self);
    };

    extern PyTypeObject WeakBindingCallback_Type;

    struct BoundEntry {
        Binder * binder;
        PyObject * binding;
        BoundEntry(Binder * binder, PyObject * binding)
            : binder(binder), binding(binding) {}
    };

    struct PendingDelete {
        Binder * binder;
        PyObject * binding;
        PyObject * handle;
    };

    struct PyObjectIdentityHash {
        using is_avalanching = void;

        auto operator()(PyObject * obj) const noexcept -> uint64_t {
            return static_cast<uint64_t>(reinterpret_cast<uintptr_t>(obj));
        }
    };

    struct PyObjectIdentityEqual {
        bool operator()(PyObject * left, PyObject * right) const noexcept {
            return left == right;
        }
    };

    template <typename T>
    using identity_map = map<PyObject *, T, PyObjectIdentityHash, PyObjectIdentityEqual>;

    struct Binder : PyObject {
        identity_map<PyObject *> bindings;
        identity_map<PyObject *> fallback_bindings;
        identity_map<WeakBindingEntry> weak_bindings;
        map<uint64_t, uint64_t> next_binding_indices;
        PyObject * on_delete = nullptr;
        vectorcallfunc vectorcall = nullptr;

        static int clear(Binder * self) {
            Py_CLEAR(self->on_delete);
            for (auto const & [_, binding] : self->bindings) {
                Py_DECREF(binding);
            }
            self->bindings.clear();
            for (auto const & [_, entry] : self->weak_bindings) {
                Py_DECREF(entry.weakref);
                Py_DECREF(entry.binding);
            }
            self->weak_bindings.clear();
            for (auto const & [obj, binding] : self->fallback_bindings) {
                Py_DECREF(obj);
                Py_DECREF(binding);
            }
            self->fallback_bindings.clear();
            self->next_binding_indices.clear();
            return 0;
        }

        static void dealloc(Binder * self) {
            clear(self);
            self->bindings.~identity_map<PyObject *>();
            self->weak_bindings.~identity_map<WeakBindingEntry>();
            self->fallback_bindings.~identity_map<PyObject *>();
            self->next_binding_indices.~map<uint64_t, uint64_t>();
            Py_TYPE(self)->tp_free(reinterpret_cast<PyObject *>(self));
        }

        static PyObject * py_new(PyTypeObject * type, PyObject *, PyObject *) {
            auto * self = reinterpret_cast<Binder *>(type->tp_alloc(type, 0));
            if (!self) return nullptr;
            new (&self->bindings) identity_map<PyObject *>();
            new (&self->weak_bindings) identity_map<WeakBindingEntry>();
            new (&self->fallback_bindings) identity_map<PyObject *>();
            new (&self->next_binding_indices) map<uint64_t, uint64_t>();
            self->on_delete = nullptr;
            return reinterpret_cast<PyObject *>(self);
        }

        static int init(Binder * self, PyObject * args, PyObject * kwargs) {
            static const char * kwlist[] = {"on_delete", nullptr};
            PyObject * on_delete = Py_None;

            if (!PyArg_ParseTupleAndKeywords(
                    args, kwargs, "|O", (char **)kwlist, &on_delete)) {
                return -1;
            }

            if (on_delete == Py_None) {
                on_delete = nullptr;
            } else if (!PyCallable_Check(on_delete)) {
                PyErr_SetString(PyExc_TypeError, "on_delete must be callable or None");
                return -1;
            }

            Py_XINCREF(on_delete);
            Py_XSETREF(self->on_delete, on_delete);
            self->vectorcall = (vectorcallfunc)call;
            return 0;
        }

        static PyObject * call(Binder * self, PyObject * const * args, size_t nargsf, PyObject * kwnames) {
            Py_ssize_t nargs = PyVectorcall_NARGS(nargsf);
            if (nargs != 1 || (kwnames && PyTuple_GET_SIZE(kwnames) != 0)) {
                PyErr_SetString(PyExc_TypeError, "Binder() takes exactly one positional argument");
                return nullptr;
            }

            PyObject * binding = self->lookup(args[0]);
            if (!binding) {
                return nullptr;
            }

            if (binding == Py_None) {
                Py_DECREF(binding);
                return Py_NewRef(args[0]);
            }
            return binding;
        }

        static PyObject * on_delete_get(Binder * self, void *) {
            return Py_NewRef(self->on_delete ? self->on_delete : Py_None);
        }

        static int on_delete_set(Binder * self, PyObject * value, void *) {
            if (!value || value == Py_None) {
                Py_CLEAR(self->on_delete);
                return 0;
            }
            if (!PyCallable_Check(value)) {
                PyErr_SetString(PyExc_TypeError, "on_delete must be callable or None");
                return -1;
            }
            Py_INCREF(value);
            Py_XSETREF(self->on_delete, value);
            return 0;
        }

        PyObject * lookup(PyObject * obj) {
            auto it = bindings.find(obj);
            if (it != bindings.end()) {
                return Py_NewRef(it->second);
            }

            auto fallback = fallback_bindings.find(obj);
            if (fallback != fallback_bindings.end()) {
                return Py_NewRef(fallback->second);
            }

            auto weak = weak_bindings.find(obj);
            if (weak != weak_bindings.end()) {
                if (PyWeakref_GetObject(weak->second.weakref) == obj) {
                    return Py_NewRef(weak->second.binding);
                }
            }

            Py_RETURN_NONE;
        }

        void forget_bound_entries(PyObject * obj);

        PyObject * forget(PyObject * obj) {
            auto it = bindings.find(obj);
            if (it == bindings.end()) {
                auto fallback = fallback_bindings.find(obj);
                if (fallback != fallback_bindings.end()) {
                    PyObject * binding = Py_NewRef(fallback->second);
                    Py_DECREF(fallback->first);
                    Py_DECREF(fallback->second);
                    fallback_bindings.erase(fallback);
                    return binding;
                }

                auto weak = weak_bindings.find(obj);
                if (weak != weak_bindings.end()) {
                    PyObject * binding = Py_NewRef(weak->second.binding);
                    Py_DECREF(weak->second.weakref);
                    Py_DECREF(weak->second.binding);
                    weak_bindings.erase(weak);
                    return binding;
                }
                Py_RETURN_NONE;
            }
            PyObject * binding = Py_NewRef(it->second);
            Py_DECREF(it->second);
            bindings.erase(it);
            forget_bound_entries(obj);
            return binding;
        }

        void emit_delete(PyObject * handle) {
            if (!on_delete || _Py_IsFinalizing()) {
                return;
            }

            PyObject * exc_type, * exc_value, * exc_tb;
            PyErr_Fetch(&exc_type, &exc_value, &exc_tb);

            PyObject * result = PyObject_CallOneArg(on_delete, handle);
            Py_XDECREF(result);
            if (!result) {
                PyErr_Clear();
            }

            PyErr_Restore(exc_type, exc_value, exc_tb);
        }

        void on_weak_collect(PyObject * obj) {
            auto it = weak_bindings.find(obj);
            if (it == weak_bindings.end()) {
                return;
            }

            PyObject * weakref = it->second.weakref;
            PyObject * binding = it->second.binding;
            PyObject * handle = Binding_Handle(binding);
            weak_bindings.erase(it);

            Py_DECREF(weakref);
            Py_DECREF(binding);
            if (handle) {
                emit_delete(handle);
                Py_DECREF(handle);
            } else {
                PyErr_Clear();
            }
        }

        PyObject * bind_with_weakref(PyObject * obj, PyObject * binding) {
            auto * callback = PyObject_New(WeakBindingCallback, &WeakBindingCallback_Type);
            if (!callback) {
                return nullptr;
            }
            callback->owner = this;
            callback->key = obj;

            PyObject * weakref = PyWeakref_NewRef(
                obj,
                reinterpret_cast<PyObject *>(callback));
            Py_DECREF(reinterpret_cast<PyObject *>(callback));
            if (!weakref) {
                return nullptr;
            }

            WeakBindingEntry entry = {
                .weakref = weakref,
                .binding = Py_NewRef(binding),
            };
            auto [it, inserted] = weak_bindings.emplace(obj, entry);
            if (!inserted) {
                Py_DECREF(it->second.weakref);
                Py_DECREF(it->second.binding);
                it->second = entry;
            }
            return Py_NewRef(binding);
        }

        PyObject * new_binding() {
            uint64_t thread_id = 0;
            if (!current_thread_id(&thread_id)) {
                return nullptr;
            }
            uint64_t index = next_binding_indices[thread_id]++;
            return Binding_New(thread_id, index);
        }

        PyObject * bind_plain(PyObject * obj) {
            PyObject * existing = lookup(obj);
            if (!existing) {
                return nullptr;
            }
            if (existing != Py_None) {
                Py_DECREF(existing);
                PyErr_SetString(PyExc_RuntimeError, "object is already bound");
                return nullptr;
            }
            Py_DECREF(existing);

            PyObject * binding = new_binding();
            if (!binding) {
                return nullptr;
            }

            bindings[obj] = Py_NewRef(binding);
            Py_DECREF(binding);
            Py_RETURN_NONE;
        }

        PyObject * bind_auto(PyObject * obj);

        PyObject * bind_fallback(PyObject * obj, PyObject * binding) {
            auto it = fallback_bindings.find(obj);
            if (it != fallback_bindings.end()) {
                Py_DECREF(binding);
                return Py_NewRef(it->second);
            }

            fallback_bindings.emplace(Py_NewRef(obj), Py_NewRef(binding));
            return binding;
        }

        static PyObject * py_bind(Binder * self, PyObject * obj) {
            return self->bind_plain(obj);
        }

        static PyObject * py_autobind(Binder * self, PyObject * obj) {
            return self->bind_auto(obj);
        }

        static PyObject * py_unbind(Binder * self, PyObject * obj) {
            PyObject * binding = self->forget(obj);
            if (!binding) {
                return nullptr;
            }
            if (binding != Py_None) {
                PyObject * handle = Binding_Handle(binding);
                Py_DECREF(binding);
                if (!handle) {
                    return nullptr;
                }
                self->emit_delete(handle);
                Py_DECREF(handle);
                Py_RETURN_NONE;
            }
            Py_DECREF(binding);
            Py_RETURN_NONE;
        }

        static PyObject * py_lookup(Binder * self, PyObject * obj) {
            return self->lookup(obj);
        }
    };

    PyObject * WeakBindingCallback::call(PyObject * self_obj, PyObject *, PyObject *) {
        auto * self = reinterpret_cast<WeakBindingCallback *>(self_obj);
        PyObject * owner = reinterpret_cast<PyObject *>(self->owner);
        Py_INCREF(owner);
        self->owner->on_weak_collect(self->key);
        Py_DECREF(owner);
        Py_RETURN_NONE;
    }

    void WeakBindingCallback::dealloc(WeakBindingCallback * self) {
        Py_TYPE(self)->tp_free(reinterpret_cast<PyObject *>(self));
    }

    PyTypeObject WeakBindingCallback_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "WeakBindingCallback",
        .tp_basicsize = sizeof(WeakBindingCallback),
        .tp_dealloc = (destructor)WeakBindingCallback::dealloc,
        .tp_call = WeakBindingCallback::call,
        .tp_flags = Py_TPFLAGS_DEFAULT,
        .tp_doc = "Internal weak binding cleanup callback",
    };

    static map<PyTypeObject *, destructor> dealloc_patches;
    static set<PyTypeObject *> bind_supported_types;
    static identity_map<std::vector<BoundEntry>> bound_entries;
    static void binder_dealloc(PyObject * obj);

    void Binder::forget_bound_entries(PyObject * obj) {
        auto entries_it = bound_entries.find(obj);
        if (entries_it == bound_entries.end()) {
            return;
        }

        auto & entries = entries_it->second;
        for (auto it = entries.begin(); it != entries.end();) {
            if (it->binder != this) {
                ++it;
                continue;
            }

            Py_DECREF(it->binding);
            Py_DECREF(reinterpret_cast<PyObject *>(it->binder));
            it = entries.erase(it);
        }

        if (entries.empty()) {
            bound_entries.erase(entries_it);
        }
    }

    static destructor get_subtype_dealloc() {
        static destructor cached = nullptr;
        if (!cached) {
            PyObject *probe = PyObject_CallFunction(
                (PyObject *)&PyType_Type,
                "s(O){}",
                "_BinderSubtypeProbe",
                (PyObject *)&PyBaseObject_Type
            );
            cached = ((PyTypeObject *)probe)->tp_dealloc;
            Py_DECREF(probe);
        }
        return cached;
    }

    static void call_subtype_dealloc_without_reentering_wrapper(PyObject * obj) {
        PyTypeObject * type = Py_TYPE(obj);
        bool gc = type->tp_flags & Py_TPFLAGS_HAVE_GC;
        if (gc) PyObject_GC_UnTrack(obj);

        if (type->tp_finalize) {
            if (gc) PyObject_GC_Track(obj);
            PyObject_CallFinalizer(obj);
            if (Py_REFCNT(obj) > 0) {
                return;
            }
            if (gc) PyObject_GC_UnTrack(obj);
        }

        if (type->tp_weaklistoffset) {
            PyObject **list = (PyObject **)((char *)obj + type->tp_weaklistoffset);
            if (*list != NULL) {
                PyObject_ClearWeakRefs(obj);
            }
        }

        PyObject **dictptr = _PyObject_GetDictPtr(obj);
        if (dictptr && *dictptr) {
            Py_CLEAR(*dictptr);
        }

        PyBaseObject_Type.tp_dealloc(obj);

        if (type->tp_flags & Py_TPFLAGS_HEAPTYPE) {
            Py_DECREF(type);
        }
    }

    static bool has_patched_base(PyTypeObject * type) {
        type = type->tp_base;
        while (type) {
            if (dealloc_patches.contains(type)) {
                return true;
            }
            type = type->tp_base;
        }
        return false;
    }

    static bool is_subtype_of(PyTypeObject * type, PyTypeObject * base) {
        while (type) {
            if (type == base) {
                return true;
            }
            type = type->tp_base;
        }
        return false;
    }

    static void unpatch_descendants(PyTypeObject * base) {
        for (auto it = dealloc_patches.begin(); it != dealloc_patches.end();) {
            PyTypeObject * type = it->first;
            if (type != base && is_subtype_of(type, base)) {
                type->tp_dealloc = it->second;
                PyType_Modified(type);
                Py_DECREF(type);
                it = dealloc_patches.erase(it);
            } else {
                ++it;
            }
        }
    }

    static PyTypeObject * find_bind_supported_type(PyTypeObject * type) {
        while (type) {
            if (bind_supported_types.contains(type)) {
                return type;
            }
            type = type->tp_base;
        }
        return nullptr;
    }

    static destructor * find_patch(PyTypeObject * type) {
        while (type) {
            auto it = dealloc_patches.find(type);
            if (it != dealloc_patches.end()) {
                return &it->second;
            }
            type = type->tp_base;
        }
        return nullptr;
    }

    bool GetExactBindSupportOriginalDealloc(PyTypeObject * type, destructor * out) {
        auto it = dealloc_patches.find(type);
        if (it == dealloc_patches.end()) {
            return false;
        }
        *out = it->second;
        return true;
    }

    static void binder_dealloc(PyObject * obj) {
        PyTypeObject * patched_type = Py_TYPE(obj);
        while (patched_type && !dealloc_patches.contains(patched_type)) {
            patched_type = patched_type->tp_base;
        }
        destructor * original_dealloc = patched_type ? find_patch(patched_type) : nullptr;
        if (!patched_type || !original_dealloc) {
            Py_TYPE(obj)->tp_dealloc(obj);
            return;
        }
        destructor original = *original_dealloc;

        // Binder composes with other native dealloc wrappers. If another layer
        // saved binder_dealloc as its "original" handler and calls us as an
        // inner deallocator, patched_type->tp_dealloc already points at that
        // outer wrapper rather than binder_dealloc. In that case we must not
        // temporarily re-enter the full dealloc slot chain or we recurse back
        // into the outer wrapper. Only perform the tp_dealloc swap when binder
        // is the active slot owner for this type.
        bool entry = patched_type->tp_dealloc == binder_dealloc;

        std::vector<PendingDelete> pending_deletes;
        auto entries_it = bound_entries.find(obj);
        if (entries_it != bound_entries.end()) {
            auto entries = std::move(entries_it->second);
            bound_entries.erase(entries_it);
            pending_deletes.reserve(entries.size());

            for (auto &bound : entries) {
                PyObject * handle = Binding_Handle(bound.binding);
                if (!handle) {
                    PyErr_Clear();
                    handle = Py_NewRef(Py_None);
                }
                pending_deletes.push_back({
                    .binder = bound.binder,
                    .binding = bound.binding,
                    .handle = handle,
                });
                PyObject * forgotten = bound.binder->forget(obj);
                Py_XDECREF(forgotten);
            }
        }

        if (entry) {
            patched_type->tp_dealloc = original;
            original(obj);
            patched_type->tp_dealloc = binder_dealloc;
        } else if (original == get_subtype_dealloc()) {
            call_subtype_dealloc_without_reentering_wrapper(obj);
        } else {
            original(obj);
        }

        for (auto &pending : pending_deletes) {
            pending.binder->emit_delete(pending.handle);
            Py_DECREF(pending.handle);
            Py_DECREF(pending.binding);
            Py_DECREF(reinterpret_cast<PyObject *>(pending.binder));
        }
    }

    static bool patch_dealloc(PyTypeObject * type) {
        if (dealloc_patches.contains(type)) {
            return true;
        }
        if (has_patched_base(type)) {
            return true;
        }
        if (!type->tp_dealloc) {
            PyErr_Format(PyExc_TypeError, "type '%.200s' has no tp_dealloc", type->tp_name);
            return false;
        }
        if (type->tp_base == nullptr) {
            PyErr_SetString(PyExc_TypeError, "Binder cannot patch the root object type");
            return false;
        }

        unpatch_descendants(type);
        Py_INCREF(type);
        dealloc_patches.emplace(type, type->tp_dealloc);
        type->tp_dealloc = binder_dealloc;
        PyType_Modified(type);
        return true;
    }

    bool AddBindSupport(PyTypeObject * type) {
        auto [_, inserted] = bind_supported_types.emplace(type);
        if (inserted) {
            Py_INCREF(type);
        }
        return true;
    }

    bool RemoveBindSupport(PyTypeObject * type) {
        auto it = bind_supported_types.find(type);
        if (it == bind_supported_types.end()) {
            return true;
        }
        bind_supported_types.erase(it);
        Py_DECREF(type);
        return true;
    }

    PyObject * Binder::bind_auto(PyObject * obj) {
        PyObject * existing = lookup(obj);
        if (!existing) {
            return nullptr;
        }
        if (existing != Py_None) {
            Py_DECREF(existing);
            PyErr_SetString(PyExc_RuntimeError, "object is already bound");
            return nullptr;
        }
        Py_DECREF(existing);

        PyObject * binding = new_binding();
        if (!binding) {
            return nullptr;
        }

        PyTypeObject * supported_type = find_bind_supported_type(Py_TYPE(obj));
        if (!supported_type) {
            PyObject * result = bind_with_weakref(obj, binding);
            if (result) {
                Py_DECREF(binding);
                Py_DECREF(result);
                Py_RETURN_NONE;
            }
            if (PyErr_ExceptionMatches(PyExc_TypeError)) {
                PyErr_Clear();
                PyObject * fallback = bind_fallback(obj, binding);
                if (!fallback) {
                    return nullptr;
                }
                Py_DECREF(fallback);
                Py_RETURN_NONE;
            }
            Py_DECREF(binding);
            return nullptr;
        }

        if (!patch_dealloc(supported_type)) {
            Py_DECREF(binding);
            return nullptr;
        }

        bindings[obj] = Py_NewRef(binding);
        auto & entries = bound_entries[obj];
        Py_INCREF(reinterpret_cast<PyObject *>(this));
        entries.emplace_back(this, Py_NewRef(binding));
        Py_DECREF(binding);
        Py_RETURN_NONE;
    }

    static PyMethodDef Binder_methods[] = {
        {"bind", (PyCFunction)Binder::py_bind, METH_O, "Bind an object without automatic cleanup tracking"},
        {"autobind", (PyCFunction)Binder::py_autobind, METH_O, "Bind an object and automatically unbind it when collected"},
        {"unbind", (PyCFunction)Binder::py_unbind, METH_O, "Remove an object's binding and emit the delete callback"},
        {"lookup", (PyCFunction)Binder::py_lookup, METH_O, "Return the bound handle for an object or None"},
        {nullptr}
    };

    static PyGetSetDef Binder_getset[] = {
        {"on_delete", (getter)Binder::on_delete_get, (setter)Binder::on_delete_set, "Delete callback", nullptr},
        {nullptr}
    };

    PyTypeObject Binder_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "Binder",
        .tp_basicsize = sizeof(Binder),
        .tp_dealloc = (destructor)Binder::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(Binder, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_methods = Binder_methods,
        .tp_getset = Binder_getset,
        .tp_init = (initproc)Binder::init,
        .tp_new = Binder::py_new,
    };

    bool Binding_Check(PyObject * obj) {
        return PyObject_TypeCheck(obj, &Binding_Type);
    }

    PyObject * Binding_Handle(PyObject * obj) {
        assert(Binding_Check(obj));
        return Py_NewRef(obj);
    }

    uint64_t Binding_Index(PyObject * obj) {
        assert(Binding_Check(obj));
        return Binding::item(reinterpret_cast<Binding *>(obj), 1);
    }

    uint64_t Binding_ThreadId(PyObject * obj) {
        assert(Binding_Check(obj));
        return Binding::item(reinterpret_cast<Binding *>(obj), 0);
    }

    PyObject * Binding_New(uint64_t thread_id, uint64_t index) {
        return Binding::from_values(&Binding_Type, thread_id, index);
    }

    PyObject * Binder_Bind(PyObject * binder, PyObject * obj) {
        if (!PyObject_TypeCheck(binder, &Binder_Type)) {
            PyErr_SetString(PyExc_TypeError, "binder must be a Binder");
            return nullptr;
        }
        return reinterpret_cast<Binder *>(binder)->bind_plain(obj);
    }

    PyObject * Binder_Lookup(PyObject * binder, PyObject * obj) {
        if (!PyObject_TypeCheck(binder, &Binder_Type)) {
            PyErr_SetString(PyExc_TypeError, "binder must be a Binder");
            return nullptr;
        }
        return reinterpret_cast<Binder *>(binder)->lookup(obj);
    }
}
