#include "utils.h"
#include "unordered_dense.h"

using namespace ankerl::unordered_dense;

namespace retracesoftware {

    struct MemoryAddresses;

    struct Branch : PyObject {
        MemoryAddresses* owner;
        PyObject* on_member;
        PyObject* on_non_member;
        vectorcallfunc vectorcall;

        static PyObject* call(PyObject* self, PyObject* const* args, size_t nargsf, PyObject* kwnames);
        static void dealloc(Branch* self);
        static int traverse(Branch* self, visitproc visit, void* arg);
        static int clear(Branch* self);
    };

    extern PyTypeObject Branch_Type;

    struct Remover : PyObject {
        MemoryAddresses* owner;
        PyObject* addr;
        vectorcallfunc vectorcall;

        static PyObject* call(PyObject* self, PyObject* const* args, size_t nargsf, PyObject* kwnames);
        static void dealloc(Remover* self);
        static int traverse(Remover* self, visitproc visit, void* arg);
        static int clear(Remover* self);
    };

    extern PyTypeObject Remover_Type;

    struct MemoryAddresses : PyObject {
        set<PyObject*> addrs;

        static PyObject* create(PyTypeObject* type, PyObject*, PyObject*) {
            auto* self = reinterpret_cast<MemoryAddresses*>(type->tp_alloc(type, 0));
            if (!self) return nullptr;
            new (&self->addrs) set<PyObject*>();
            return reinterpret_cast<PyObject*>(self);
        }

        static void dealloc(MemoryAddresses* self) {
            self->addrs.~set<PyObject*>();
            Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
        }

        static int sq_contains(MemoryAddresses* self, PyObject* obj) {
            return self->addrs.find(obj) != self->addrs.end() ? 1 : 0;
        }

        static PyObject* py_add(MemoryAddresses* self, PyObject* obj) {
            self->addrs.insert(obj);

            auto* remover = reinterpret_cast<Remover*>(Remover_Type.tp_alloc(&Remover_Type, 0));
            if (!remover) return nullptr;

            remover->owner = self;
            Py_INCREF(self);
            remover->addr = obj;
            remover->vectorcall = Remover::call;

            return reinterpret_cast<PyObject*>(remover);
        }

        static Py_ssize_t sq_length(MemoryAddresses* self) {
            return (Py_ssize_t)self->addrs.size();
        }

        static PyObject* py_contains(MemoryAddresses* self, PyObject* obj) {
            if (self->addrs.find(obj) != self->addrs.end())
                Py_RETURN_TRUE;
            Py_RETURN_FALSE;
        }

        static PyObject* py_branch(MemoryAddresses* self, PyObject* const* args, Py_ssize_t nargs) {
            if (nargs != 2) {
                PyErr_Format(PyExc_TypeError,
                    "branch() takes exactly 2 arguments (%zd given)", nargs);
                return nullptr;
            }

            PyObject* on_member = args[0];
            PyObject* on_non_member = args[1];

            if (on_member != Py_None && !PyCallable_Check(on_member)) {
                PyErr_SetString(PyExc_TypeError, "on_member must be callable or None");
                return nullptr;
            }
            if (on_non_member != Py_None && !PyCallable_Check(on_non_member)) {
                PyErr_SetString(PyExc_TypeError, "on_non_member must be callable or None");
                return nullptr;
            }

            auto* branch = reinterpret_cast<Branch*>(Branch_Type.tp_alloc(&Branch_Type, 0));
            if (!branch) return nullptr;

            branch->owner = self;
            Py_INCREF(self);
            branch->on_member = Py_NewRef(on_member);
            branch->on_non_member = Py_NewRef(on_non_member);
            branch->vectorcall = Branch::call;

            return reinterpret_cast<PyObject*>(branch);
        }
    };

    PyObject* Remover::call(PyObject* self_obj, PyObject* const*, size_t nargsf, PyObject*) {
        if (PyVectorcall_NARGS(nargsf) != 0) {
            PyErr_SetString(PyExc_TypeError, "Remover takes no arguments");
            return nullptr;
        }
        auto* self = reinterpret_cast<Remover*>(self_obj);
        if (self->owner) {
            self->owner->addrs.erase(self->addr);
        }
        Py_RETURN_NONE;
    }

    void Remover::dealloc(Remover* self) {
        PyObject_GC_UnTrack(self);
        Py_XDECREF(reinterpret_cast<PyObject*>(self->owner));
        self->owner = nullptr;
        Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
    }

    int Remover::traverse(Remover* self, visitproc visit, void* arg) {
        Py_VISIT(reinterpret_cast<PyObject*>(self->owner));
        return 0;
    }

    int Remover::clear(Remover* self) {
        Py_CLEAR(self->owner);
        return 0;
    }

    PyObject* Branch::call(PyObject* self_obj, PyObject* const* args, size_t nargsf, PyObject*) {
        if (PyVectorcall_NARGS(nargsf) != 1) {
            PyErr_SetString(PyExc_TypeError, "Branch takes exactly 1 argument");
            return nullptr;
        }
        auto* self = reinterpret_cast<Branch*>(self_obj);
        PyObject* obj = args[0];
        bool in_set = self->owner->addrs.find(obj) != self->owner->addrs.end();
        PyObject* fn = in_set ? self->on_member : self->on_non_member;
        if (fn == Py_None) Py_RETURN_NONE;
        return PyObject_CallOneArg(fn, obj);
    }

    void Branch::dealloc(Branch* self) {
        PyObject_GC_UnTrack(self);
        Py_XDECREF(reinterpret_cast<PyObject*>(self->owner));
        Py_XDECREF(self->on_member);
        Py_XDECREF(self->on_non_member);
        Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
    }

    int Branch::traverse(Branch* self, visitproc visit, void* arg) {
        Py_VISIT(reinterpret_cast<PyObject*>(self->owner));
        Py_VISIT(self->on_member);
        Py_VISIT(self->on_non_member);
        return 0;
    }

    int Branch::clear(Branch* self) {
        Py_CLEAR(self->owner);
        Py_CLEAR(self->on_member);
        Py_CLEAR(self->on_non_member);
        return 0;
    }

    static PySequenceMethods MemoryAddresses_as_sequence = {
        .sq_length = (lenfunc)MemoryAddresses::sq_length,
        .sq_contains = (objobjproc)MemoryAddresses::sq_contains,
    };

    static PyMethodDef MemoryAddresses_methods[] = {
        {"add", (PyCFunction)MemoryAddresses::py_add, METH_O,
         "Add an address; returns a callable that removes it."},
        {"contains", (PyCFunction)MemoryAddresses::py_contains, METH_O,
         "Return True if the address is in the set."},
        {"branch", (PyCFunction)MemoryAddresses::py_branch, METH_FASTCALL,
         "branch(on_member, on_non_member) -> callable that dispatches based on membership."},
        {nullptr, nullptr, 0, nullptr}
    };

    PyTypeObject MemoryAddresses_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "MemoryAddresses",
        .tp_basicsize = sizeof(MemoryAddresses),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)MemoryAddresses::dealloc,
        .tp_as_sequence = &MemoryAddresses_as_sequence,
        .tp_flags = Py_TPFLAGS_DEFAULT,
        .tp_methods = MemoryAddresses_methods,
        .tp_new = MemoryAddresses::create,
    };

    PyTypeObject Remover_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "Remover",
        .tp_basicsize = sizeof(Remover),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)Remover::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(Remover, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_traverse = (traverseproc)Remover::traverse,
        .tp_clear = (inquiry)Remover::clear,
    };

    PyTypeObject Branch_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "Branch",
        .tp_basicsize = sizeof(Branch),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)Branch::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(Branch, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_traverse = (traverseproc)Branch::traverse,
        .tp_clear = (inquiry)Branch::clear,
    };

}
