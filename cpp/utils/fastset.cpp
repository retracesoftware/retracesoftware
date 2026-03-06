#include "utils.h"
#include <cstdint>
#include <vector>
#include <algorithm>
// #include "methodobject.h"
// #include "object.h"
#include "unordered_dense.h"
#include <utility>  // for std::pair
#include <functional>

using namespace ankerl::unordered_dense;

namespace retracesoftware {

    struct FastSet : PyObject {
        set<PyObject*> impl;
        vectorcallfunc vectorcall;

        // ---- GC: traverse each contained PyObject so the GC sees edges
        static int traverse(FastSet* self, visitproc visit, void* arg) {
            for (PyObject* o : self->impl) {
                Py_VISIT(o);
            }
            return 0;
        }

        // ---- GC: clear strong refs (drop edges)
        static int clear(FastSet* self) {
            for (PyObject* o : self->impl) {
                Py_DECREF(o);
            }
            self->impl.clear();
            return 0;
        }

        // ---- dealloc: untrack, clear, then free
        static void dealloc(FastSet* self) {
            PyObject_GC_UnTrack(self);
            clear(self);

            self->impl.~set<PyObject*>();

            Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
        }

        static PyObject* create(PyTypeObject* type, PyObject*, PyObject*) {
            auto* self = reinterpret_cast<FastSet*>(type->tp_alloc(type, 0));
            if (!self) return nullptr;

            // Construct the std::set in-place
            new (&self->impl) set<PyObject*>();
            self->vectorcall = nullptr;
            return reinterpret_cast<PyObject*>(self);
        }

        static int init(FastSet* self, PyObject* args, PyObject* kwds) {
            static const char* kwlist[] = {"initial", nullptr};
            PyObject* initial = nullptr;
            if (!PyArg_ParseTupleAndKeywords(args, kwds, "|O",
                                            const_cast<char**>(kwlist), &initial)) {
                return -1;
            }

            self->vectorcall = reinterpret_cast<vectorcallfunc>(&FastSet::call);

            if (initial && initial != Py_None) {
                PyObject* it = PyObject_GetIter(initial);
                if (!it) return -1;
                PyObject* item;
                while ((item = PyIter_Next(it))) {
                    auto [_, inserted] = self->impl.insert(item);
                    if (!inserted) Py_DECREF(item);
                }
                Py_DECREF(it);
                if (PyErr_Occurred()) return -1;
            }

            // Now fully initialized: track with GC
            // PyObject_GC_Track(self);
            return 0;
        }

        bool contains(PyObject* obj) const {
            return impl.find(obj) != impl.end();
        }

        static PyObject* call(PyObject* callable, PyObject* const* args,
                            size_t nargsf, PyObject* kwnames) {
            auto* self = reinterpret_cast<FastSet*>(callable);
            const Py_ssize_t nargs = PyVectorcall_NARGS(nargsf);
            if (kwnames || nargs != 1) {
                PyErr_SetString(PyExc_TypeError,
                                "FastSet(...) takes exactly one positional argument");
                return nullptr;
            }
            return Py_NewRef(self->contains(args[0]) ? Py_True : Py_False);
        }

        static PyObject* add(FastSet* self, PyObject* obj) {
            auto [_, inserted] = self->impl.insert(obj);
            if (inserted) Py_INCREF(obj);
            return Py_NewRef(inserted ? Py_True : Py_False);
        }

        static PyObject* remove(FastSet* self, PyObject* obj) {
            auto it = self->impl.find(obj);
            if (it == self->impl.end()) {
                PyErr_SetString(PyExc_KeyError, "object not in FastSet");
                return nullptr;
            }
            Py_DECREF(*it);
            self->impl.erase(it);
            Py_RETURN_NONE;
        }
    };

    // ---- methods table
    static PyMethodDef fastset_methods[] = {
        {"add", (PyCFunction)FastSet::add, METH_O, "Add an object by identity; returns True if newly added"},
        {"remove", (PyCFunction)FastSet::remove, METH_O, "Remove object by identity; raises KeyError if absent"},
        {nullptr, nullptr, 0, nullptr}
    };

    // ---- type object
    PyTypeObject FastSet_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "fastset",
        .tp_basicsize = sizeof(FastSet),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)FastSet::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(FastSet, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)FastSet::traverse,
        .tp_clear = (inquiry)FastSet::clear,
        .tp_methods = fastset_methods,
        .tp_init = (initproc)FastSet::init,
        .tp_new = FastSet::create,
    };
}