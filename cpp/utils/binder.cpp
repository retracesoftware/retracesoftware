#include "utils.h"
#include <structmember.h>
#include <cstdint>
#include <vector>
#include "unordered_dense.h"

using namespace ankerl::unordered_dense;

namespace retracesoftware {

    struct Binding : PyObject {
        uint64_t handle;

        static PyObject * py_new(PyTypeObject * type, PyObject * args, PyObject * kwargs) {
            static const char * kwlist[] = {"handle", nullptr};
            unsigned long long handle = 0;

            if (!PyArg_ParseTupleAndKeywords(
                    args, kwargs, "K", (char **)kwlist, &handle)) {
                return nullptr;
            }

            auto * self = reinterpret_cast<Binding *>(type->tp_alloc(type, 0));
            if (!self) return nullptr;
            self->handle = static_cast<uint64_t>(handle);
            return reinterpret_cast<PyObject *>(self);
        }

        static void dealloc(Binding * self) {
            Py_TYPE(self)->tp_free(reinterpret_cast<PyObject *>(self));
        }

        static PyObject * repr(Binding * self) {
            return PyUnicode_FromFormat(
                "Binding(%llu)",
                static_cast<unsigned long long>(self->handle));
        }

        static PyObject * int_(Binding * self) {
            return PyLong_FromUnsignedLongLong(
                static_cast<unsigned long long>(self->handle));
        }

        static Py_hash_t hash(Binding * self) {
            Py_hash_t value = static_cast<Py_hash_t>(self->handle);
            return value == -1 ? -2 : value;
        }

        static PyObject * richcompare(PyObject * a, PyObject * b, int op) {
            if (!PyObject_TypeCheck(a, &Binding_Type)
                || !PyObject_TypeCheck(b, &Binding_Type)) {
                Py_RETURN_NOTIMPLEMENTED;
            }

            uint64_t left = reinterpret_cast<Binding *>(a)->handle;
            uint64_t right = reinterpret_cast<Binding *>(b)->handle;

            bool result = false;
            switch (op) {
                case Py_LT: result = left < right; break;
                case Py_LE: result = left <= right; break;
                case Py_EQ: result = left == right; break;
                case Py_NE: result = left != right; break;
                case Py_GT: result = left > right; break;
                case Py_GE: result = left >= right; break;
                default: Py_RETURN_NOTIMPLEMENTED;
            }

            return PyBool_FromLong(result);
        }
    };

    static PyMemberDef Binding_members[] = {
        {"handle", T_ULONGLONG, OFFSET_OF_MEMBER(Binding, handle), READONLY, "Opaque binding handle"},
        {nullptr}
    };

    static PyNumberMethods Binding_number_methods = {
        .nb_int = (unaryfunc)Binding::int_,
    };

    PyTypeObject Binding_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "Binding",
        .tp_basicsize = sizeof(Binding),
        .tp_dealloc = (destructor)Binding::dealloc,
        .tp_repr = (reprfunc)Binding::repr,
        .tp_as_number = &Binding_number_methods,
        .tp_hash = (hashfunc)Binding::hash,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
        .tp_richcompare = Binding::richcompare,
        .tp_members = Binding_members,
        .tp_new = Binding::py_new,
    };

    struct Binder;

    struct BoundEntry {
        Binder * binder;
        PyObject * binding;
        BoundEntry(Binder * binder, PyObject * binding)
            : binder(binder), binding(binding) {}
    };

    struct Binder : PyObject {
        map<PyObject *, PyObject *> bindings;
        PyObject * on_delete = nullptr;

        static int traverse(Binder * self, visitproc visit, void * arg) {
            Py_VISIT(self->on_delete);
            for (auto const & [_, binding] : self->bindings) {
                Py_VISIT(binding);
            }
            return 0;
        }

        static int clear(Binder * self) {
            Py_CLEAR(self->on_delete);
            for (auto const & [_, binding] : self->bindings) {
                Py_DECREF(binding);
            }
            self->bindings.clear();
            return 0;
        }

        static void dealloc(Binder * self) {
            PyObject_GC_UnTrack(self);
            clear(self);
            self->bindings.~map<PyObject *, PyObject *>();
            Py_TYPE(self)->tp_free(reinterpret_cast<PyObject *>(self));
        }

        static PyObject * py_new(PyTypeObject * type, PyObject *, PyObject *) {
            auto * self = reinterpret_cast<Binder *>(type->tp_alloc(type, 0));
            if (!self) return nullptr;
            new (&self->bindings) map<PyObject *, PyObject *>();
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
            return 0;
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
            if (it == bindings.end()) {
                Py_RETURN_NONE;
            }
            return Py_NewRef(it->second);
        }

        void forget(PyObject * obj) {
            auto it = bindings.find(obj);
            if (it == bindings.end()) {
                return;
            }
            Py_DECREF(it->second);
            bindings.erase(it);
        }

        void emit_delete(PyObject * binding) {
            if (!on_delete || _Py_IsFinalizing()) {
                return;
            }

            PyObject * exc_type, * exc_value, * exc_tb;
            PyErr_Fetch(&exc_type, &exc_value, &exc_tb);

            PyObject * result = PyObject_CallOneArg(on_delete, binding);
            Py_XDECREF(result);
            if (!result) {
                PyErr_Clear();
            }

            PyErr_Restore(exc_type, exc_value, exc_tb);
        }

        PyObject * bind(PyObject * obj);

        static PyObject * py_bind(Binder * self, PyObject * obj) {
            return self->bind(obj);
        }

        static PyObject * py_lookup(Binder * self, PyObject * obj) {
            return self->lookup(obj);
        }
    };

    static map<PyTypeObject *, destructor> dealloc_patches;
    static map<PyObject *, std::vector<BoundEntry>> bound_entries;
    static uint64_t next_binding_handle = 0;

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

        auto entries_it = bound_entries.find(obj);
        if (entries_it != bound_entries.end()) {
            auto entries = std::move(entries_it->second);
            bound_entries.erase(entries_it);

            for (auto & entry : entries) {
                entry.binder->forget(obj);
                entry.binder->emit_delete(entry.binding);
                Py_DECREF(entry.binding);
                Py_DECREF(reinterpret_cast<PyObject *>(entry.binder));
            }
        }

        patched_type->tp_dealloc = *original_dealloc;
        (*original_dealloc)(obj);
        patched_type->tp_dealloc = binder_dealloc;
    }

    static bool patch_dealloc(PyTypeObject * type) {
        if (dealloc_patches.contains(type)) {
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

        Py_INCREF(type);
        dealloc_patches.emplace(type, type->tp_dealloc);
        type->tp_dealloc = binder_dealloc;
        PyType_Modified(type);
        return true;
    }

    PyObject * Binder::bind(PyObject * obj) {
        auto it = bindings.find(obj);
        if (it != bindings.end()) {
            return Py_NewRef(it->second);
        }

        if (!patch_dealloc(Py_TYPE(obj))) {
            return nullptr;
        }

        PyObject * binding = Binding_New(next_binding_handle++);
        if (!binding) {
            return nullptr;
        }

        bindings[obj] = Py_NewRef(binding);
        auto & entries = bound_entries[obj];
        Py_INCREF(reinterpret_cast<PyObject *>(this));
        entries.emplace_back(this, Py_NewRef(binding));
        return binding;
    }

    static PyMethodDef Binder_methods[] = {
        {"bind", (PyCFunction)Binder::py_bind, METH_O, "Bind an object and return its handle"},
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
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_BASETYPE,
        .tp_traverse = (traverseproc)Binder::traverse,
        .tp_clear = (inquiry)Binder::clear,
        .tp_methods = Binder_methods,
        .tp_getset = Binder_getset,
        .tp_init = (initproc)Binder::init,
        .tp_new = Binder::py_new,
    };

    bool Binding_Check(PyObject * obj) {
        return PyObject_TypeCheck(obj, &Binding_Type);
    }

    uint64_t Binding_Handle(PyObject * obj) {
        assert(Binding_Check(obj));
        return reinterpret_cast<Binding *>(obj)->handle;
    }

    PyObject * Binding_New(uint64_t handle) {
        auto * self = reinterpret_cast<Binding *>(Binding_Type.tp_alloc(&Binding_Type, 0));
        if (!self) return nullptr;
        self->handle = handle;
        return reinterpret_cast<PyObject *>(self);
    }

    PyObject * Binder_Bind(PyObject * binder, PyObject * obj) {
        if (!PyObject_TypeCheck(binder, &Binder_Type)) {
            PyErr_SetString(PyExc_TypeError, "binder must be a Binder");
            return nullptr;
        }
        return reinterpret_cast<Binder *>(binder)->bind(obj);
    }

    PyObject * Binder_Lookup(PyObject * binder, PyObject * obj) {
        if (!PyObject_TypeCheck(binder, &Binder_Type)) {
            PyErr_SetString(PyExc_TypeError, "binder must be a Binder");
            return nullptr;
        }
        return reinterpret_cast<Binder *>(binder)->lookup(obj);
    }
}
