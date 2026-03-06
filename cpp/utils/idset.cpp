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

    static bool test(PyObject * idset, PyObject* obj);

    struct Test : PyObject {
        FastCall transform;
        PyObject * idset;
        vectorcallfunc vectorcall;

        bool test(PyObject * obj) {
            if (transform.callable) {
                PyObject * transformed = transform(obj);
                if (!transformed) throw nullptr;
                bool res = retracesoftware::test(idset, transformed);
                Py_DECREF(transformed);
                return res;
            } else {
                return retracesoftware::test(idset, obj);
            }
        }

        static PyObject* call(Test* self, PyObject* const* args, size_t nargsf, PyObject* kwnames) {
            if (PyVectorcall_NARGS(nargsf) != 1) {
                PyErr_Format(PyExc_TypeError, "%S only takes one positional argument", Py_TYPE(self));
                return nullptr;
            }

            try {
                return Py_NewRef(self->test(args[0]) ? args[0] : Py_None);
            } catch (...) {
                return nullptr;
            }
        }

        static int clear(Test* self) {
            Py_CLEAR(self->idset);
            Py_CLEAR(self->transform.callable);
            return 0;
        }

        static int traverse(Test* self, visitproc visit, void* arg) {
            Py_VISIT(self->idset);
            Py_VISIT(self->transform.callable);
            return 0;
        }

        // ---- dealloc: untrack, clear, then free
        static void dealloc(Test* self) {
            PyObject_GC_UnTrack(self);
            clear(self);
            Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
        }
    };

    struct Logical : PyObject {
        FastCall transform;
        PyObject * idset;
        size_t from;
        vectorcallfunc vectorcall;

        bool test(PyObject * obj) {
            if (transform.callable) {
                PyObject * transformed = transform(obj);
                if (!transformed) throw nullptr;
                bool res = retracesoftware::test(idset, transformed);
                Py_DECREF(transformed);
                return res;
            } else {
                return retracesoftware::test(idset, obj);
            }
        }

        static PyObject* all(Logical* self, PyObject* const* args, size_t nargsf, PyObject* kwnames) {
            try {
                for (Py_ssize_t i = self->from; i < PyVectorcall_NARGS(nargsf) + (kwnames ? PyTuple_GET_SIZE(kwnames) : 0); i++) {
                    if (!self->test(args[i])) {
                        Py_RETURN_FALSE;
                    }
                }
                Py_RETURN_TRUE;

            } catch (...) {
                return nullptr;
            }
        }

        static PyObject* any(Logical* self, PyObject* const* args, size_t nargsf, PyObject* kwnames) {
            try {
                for (Py_ssize_t i = self->from; i < PyVectorcall_NARGS(nargsf) + (kwnames ? PyTuple_GET_SIZE(kwnames) : 0); i++) {
                    if (self->test(args[i])) {
                        Py_RETURN_TRUE;
                    }
                }
                Py_RETURN_FALSE;
            } catch (...) {
                return nullptr;
            }
        }

        static int clear(Logical* self) {
            Py_CLEAR(self->idset);
            Py_CLEAR(self->transform.callable);
            return 0;
        }

        static int traverse(Logical* self, visitproc visit, void* arg) {
            Py_VISIT(self->idset);
            Py_VISIT(self->transform.callable);
            return 0;
        }

        // ---- dealloc: untrack, clear, then free
        static void dealloc(Logical* self) {
            PyObject_GC_UnTrack(self);
            clear(self);
            Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
        }
    };

    struct IdSet : PyObject {
        set<PyObject *> contents;
        vectorcallfunc vectorcall;

        // ---- GC: traverse each contained PyObject so the GC sees edges
        static int traverse(IdSet* self, visitproc visit, void* arg) {
            for (PyObject* o : self->contents) {
                Py_VISIT(o);
            }
            return 0;
        }

        // ---- GC: clear strong refs (drop edges)
        static int clear(IdSet* self) {
            for (PyObject* o : self->contents) {
                Py_DECREF(o);
            }
            self->contents.clear();
            return 0;
        }

        // ---- dealloc: untrack, clear, then free
        static void dealloc(IdSet* self) {
            PyObject_GC_UnTrack(self);
            clear(self);

            self->contents.~set<PyObject*>();

            Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
        }

        static PyObject* create(PyTypeObject * type, PyObject*, PyObject*) {
            auto* self = reinterpret_cast<IdSet*>(type->tp_alloc(type, 0));
            if (!self) return nullptr;

            // Construct the std::set in-place
            new (&self->contents) set<PyObject*>();
            self->vectorcall = nullptr;
            return reinterpret_cast<PyObject*>(self);
        }

        Logical * create_logical(PyObject *args, PyObject *kwds) {
            static const char* kwlist[] = {"transform", "from_arg", nullptr};
            
            PyObject* transform = nullptr;
            int from_arg = 0;

            if (!PyArg_ParseTupleAndKeywords(args, kwds, "|OI", const_cast<char**>(kwlist), &transform, &from_arg)) {
                return nullptr;
            }

            auto* logical = reinterpret_cast<Logical *>(IdSetLogical_Type.tp_alloc(&IdSetLogical_Type, 0));

            if (!logical) return nullptr;

            logical->transform = transform ? FastCall(Py_NewRef(transform)) : FastCall();
            logical->idset = Py_NewRef(this);
            logical->from = from_arg;

            return logical;
        }

        static PyObject * py_test(IdSet *self, PyObject *args, PyObject *kwds) {
            static const char* kwlist[] = {"transform", nullptr};

            PyObject* transform = nullptr;

            if (!PyArg_ParseTupleAndKeywords(args, kwds, "|O", const_cast<char**>(kwlist), &transform)) {
                return nullptr;
            }
            
            auto* test = reinterpret_cast<Test *>(IdSetTest_Type.tp_alloc(&IdSetTest_Type, 0));

            if (!test) return nullptr;

            test->transform = transform ? FastCall(Py_NewRef(transform)) : FastCall();
            test->idset = Py_NewRef(self);
            test->vectorcall = (vectorcallfunc)Test::call;

            return test;
        }

        static PyObject * py_all(IdSet *self, PyObject *args, PyObject *kwds) {

            Logical * logical = self->create_logical(args, kwds);
            if (!logical) return nullptr;
            logical->vectorcall = (vectorcallfunc)Logical::all;
            return logical;
        }

        static PyObject * py_any(IdSet *self, PyObject *args, PyObject *kwds) {

            Logical * logical = self->create_logical(args, kwds);
            if (!logical) return nullptr;
            logical->vectorcall = (vectorcallfunc)Logical::any;
            return logical;
        }

        static int init(IdSet* self, PyObject* args, PyObject* kwds) {
            
            self->vectorcall = reinterpret_cast<vectorcallfunc>(&IdSet::call);

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

        bool contains(PyObject * obj) const {
            return contents.find(obj) != contents.end();
        }

        static PyObject* call(PyObject* callable, PyObject* const* args, size_t nargsf, PyObject* kwnames) {
            auto* self = reinterpret_cast<IdSet*>(callable);

            if (PyVectorcall_NARGS(nargsf) != 1) {
                PyErr_Format(PyExc_TypeError, "%S takes one positional argument", Py_TYPE(callable));
            }

            return PyBool_FromLong(self->contains(args[0]));
        }

        bool add(PyObject* obj) {
            auto [_, inserted] = contents.insert(obj);
            if (inserted) Py_INCREF(obj);
            return inserted;
        }

        static PyObject* py_add(IdSet* self, PyObject* obj) {
            return PyBool_FromLong(self->add(obj));
        }

        // static PyObject* test(IdSet* self, PyObject* obj) {

        //     if (!PyObject_TypeCheck(obj, &PyType_Type)) {
        //         PyErr_Format(PyExc_TypeError, "Error in %S trying to test: %S, Can only test types", self, obj);
        //         return nullptr;
        //     }
        //     return Py_NewRef(self->contains(reinterpret_cast<PyTypeObject *>(Py_TYPE(obj))) ? obj : Py_None);
        // }

        bool remove(PyObject* obj) {
            auto it = contents.find(obj);
            if (it == contents.end()) {
                return false;
            }
            Py_DECREF(*it);
            return true;
        }

        static PyObject* py_remove(IdSet* self, PyObject* obj) {
            return PyBool_FromLong(self->remove(obj));
        }
    };

    static bool test(PyObject * idset, PyObject* obj) {
        return reinterpret_cast<IdSet *>(idset)->contains(obj);
    }

    // ---- methods table
    static PyMethodDef instancecheck_methods[] = {
        // {"contains", (PyCFunction)InstanceCheck::py_contains, METH_O, "Add an object by identity; returns True if newly added"},

        {"all", (PyCFunction)IdSet::py_all, METH_VARARGS | METH_KEYWORDS, "TODO"},
        {"any", (PyCFunction)IdSet::py_any, METH_VARARGS | METH_KEYWORDS, "TODO"},
        {"test", (PyCFunction)IdSet::py_test, METH_VARARGS | METH_KEYWORDS, "TODO"},

        {"add", (PyCFunction)IdSet::py_add, METH_O, "Add an object by identity; returns True if newly added"},
        // {"test", (PyCFunction)InstanceCheck::test, METH_O, "returns input if in set, else None"},
        {"remove", (PyCFunction)IdSet::py_remove, METH_O, "Remove object by identity; raises KeyError if absent"},
        {nullptr, nullptr, 0, nullptr}
    };

    // ---- type object
    PyTypeObject IdSet_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "idset",
        .tp_basicsize = sizeof(IdSet),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)IdSet::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(IdSet, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)IdSet::traverse,
        .tp_clear = (inquiry)IdSet::clear,
        .tp_methods = instancecheck_methods,
        .tp_init = (initproc)IdSet::init,
        .tp_new = IdSet::create,
    };

    PyTypeObject IdSetLogical_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "idset_logical",
        .tp_basicsize = sizeof(Logical),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)Logical::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(Logical, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)Logical::traverse,
        .tp_clear = (inquiry)Logical::clear,
    };

    PyTypeObject IdSetTest_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "idset_test",
        .tp_basicsize = sizeof(Test),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)Test::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(Test, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)Test::traverse,
        .tp_clear = (inquiry)Test::clear,
    };

}