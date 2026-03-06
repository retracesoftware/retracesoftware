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

    struct InstanceCheck : PyObject {
        set<PyTypeObject*> types;
        vectorcallfunc vectorcall;

        // ---- GC: traverse each contained PyObject so the GC sees edges
        static int traverse(InstanceCheck* self, visitproc visit, void* arg) {
            for (PyTypeObject* o : self->types) {
                Py_VISIT(o);
            }
            return 0;
        }

        // ---- GC: clear strong refs (drop edges)
        static int clear(InstanceCheck* self) {
            for (PyTypeObject* o : self->types) {
                Py_DECREF(o);
            }
            self->types.clear();
            return 0;
        }

        // ---- dealloc: untrack, clear, then free
        static void dealloc(InstanceCheck* self) {
            PyObject_GC_UnTrack(self);
            clear(self);

            self->types.~set<PyTypeObject*>();

            Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
        }

        static PyObject* create(PyTypeObject* type, PyObject*, PyObject*) {
            auto* self = reinterpret_cast<InstanceCheck*>(type->tp_alloc(type, 0));
            if (!self) return nullptr;

            // Construct the std::set in-place
            new (&self->types) set<PyTypeObject*>();
            self->vectorcall = nullptr;
            return reinterpret_cast<PyObject*>(self);
        }

        static int init(InstanceCheck* self, PyObject* args, PyObject* kwds) {
            // static const char* kwlist[] = {"initial", nullptr};
            // PyObject* initial = nullptr;
            // if (!PyArg_ParseTupleAndKeywords(args, kwds, "|O",
            //                                 const_cast<char**>(kwlist), &initial)) {
            //     return -1;
            // }

            self->vectorcall = reinterpret_cast<vectorcallfunc>(&InstanceCheck::call);

            // if (initial && initial != Py_None) {
            //     PyObject* it = PyObject_GetIter(initial);
            //     if (!it) return -1;
            //     PyObject* item;
            //     while ((item = PyIter_Next(it))) {
            //         auto [_, inserted] = self->types.insert(item);
            //         if (!inserted) Py_DECREF(item);
            //     }
            //     Py_DECREF(it);
            //     if (PyErr_Occurred()) return -1;
            // }

            // Now fully initialized: track with GC
            // PyObject_GC_Track(self);
            return 0;
        }

        bool contains(PyTypeObject* obj) const {
            return types.find(obj) != types.end();
        }

        static PyObject* call(PyObject* callable, PyObject* const* args, size_t nargsf, PyObject* kwnames) {
            auto* self = reinterpret_cast<InstanceCheck*>(callable);

            const Py_ssize_t total_args = PyVectorcall_NARGS(nargsf) + (kwnames ? PyTuple_GET_SIZE(kwnames) : 0);

            for (size_t i = 0; i < total_args; i++) {
                if (args[i] != Py_None && !self->contains(Py_TYPE(args[i]))) {
                    Py_RETURN_FALSE;
                }
            }
            Py_RETURN_TRUE;
        }

        static PyObject* py_contains(InstanceCheck* self, PyObject* obj) {
            if (!PyObject_TypeCheck(obj, &PyType_Type)) {
                PyErr_SetString(PyExc_TypeError, "Can only run contains on types");
                return nullptr;
            }

            return PyBool_FromLong(self->contains(reinterpret_cast<PyTypeObject *>(obj)));
        }

        static PyObject* add(InstanceCheck* self, PyObject* obj) {

            if (!PyObject_TypeCheck(obj, &PyType_Type)) {
                PyErr_Format(PyExc_TypeError, "Error in %S trying to add: %S, Can only add types", self, obj);
                return nullptr;
            }

            auto [_, inserted] = self->types.insert(reinterpret_cast<PyTypeObject *>(obj));
            if (inserted) Py_INCREF(obj);
            return Py_NewRef(inserted ? Py_True : Py_False);
        }

        static PyObject* test(InstanceCheck* self, PyObject* obj) {

            if (!PyObject_TypeCheck(obj, &PyType_Type)) {
                PyErr_Format(PyExc_TypeError, "Error in %S trying to test: %S, Can only test types", self, obj);
                return nullptr;
            }
            return Py_NewRef(self->contains(reinterpret_cast<PyTypeObject *>(Py_TYPE(obj))) ? obj : Py_None);
        }

        static PyObject* remove(InstanceCheck* self, PyObject* obj) {
            if (!PyObject_TypeCheck(obj, &PyType_Type)) {
                PyErr_SetString(PyExc_TypeError, "Can only remove types");
                return nullptr;
            }

            auto it = self->types.find(reinterpret_cast<PyTypeObject *>(obj));
            if (it == self->types.end()) {
                PyErr_SetString(PyExc_KeyError, "object not in FastSet");
                return nullptr;
            }
            Py_DECREF(*it);
            self->types.erase(it);
            Py_RETURN_NONE;
        }
    };

    // ---- methods table
    static PyMethodDef instancecheck_methods[] = {
        {"contains", (PyCFunction)InstanceCheck::py_contains, METH_O, "Add an object by identity; returns True if newly added"},
        {"add", (PyCFunction)InstanceCheck::add, METH_O, "Add an object by identity; returns True if newly added"},
        {"test", (PyCFunction)InstanceCheck::test, METH_O, "returns input if in set, else None"},
        {"remove", (PyCFunction)InstanceCheck::remove, METH_O, "Remove object by identity; raises KeyError if absent"},
        {nullptr, nullptr, 0, nullptr}
    };

    // ---- type object
    PyTypeObject InstanceCheck_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "instancecheck",
        .tp_basicsize = sizeof(InstanceCheck),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)InstanceCheck::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(InstanceCheck, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)InstanceCheck::traverse,
        .tp_clear = (inquiry)InstanceCheck::clear,
        .tp_methods = instancecheck_methods,
        .tp_init = (initproc)InstanceCheck::init,
        .tp_new = InstanceCheck::create,
    };
}