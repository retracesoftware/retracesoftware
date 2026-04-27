#include "utils.h"
#include <new>

namespace retracesoftware {

    struct WeakSet;

    struct WeakSetCallback : PyObject {
        WeakSet* owner;
        PyObject* key;

        static int traverse(WeakSetCallback* self, visitproc visit, void* arg);
        static int clear(WeakSetCallback* self);
        static void dealloc(WeakSetCallback* self);
        static PyObject* call(PyObject* self_obj, PyObject* args, PyObject*);
    };

    struct WeakSet : PyObject {
        map<PyObject*, PyObject*> weak;
        set<PyObject*> strong;
        vectorcallfunc vectorcall;

        static PyObject* create(PyTypeObject* type, PyObject*, PyObject*) {
            auto* self = reinterpret_cast<WeakSet*>(type->tp_alloc(type, 0));
            if (!self) return nullptr;
            new (&self->weak) map<PyObject*, PyObject*>();
            new (&self->strong) set<PyObject*>();
            self->vectorcall = nullptr;
            return reinterpret_cast<PyObject*>(self);
        }

        static int init(WeakSet* self, PyObject* args, PyObject* kwds) {
            static const char* kwlist[] = {"initial", nullptr};
            PyObject* initial = nullptr;
            if (!PyArg_ParseTupleAndKeywords(args, kwds, "|O",
                                             const_cast<char**>(kwlist), &initial)) {
                return -1;
            }

            self->vectorcall = reinterpret_cast<vectorcallfunc>(&WeakSet::call);

            if (initial && initial != Py_None) {
                PyObject* it = PyObject_GetIter(initial);
                if (!it) return -1;
                PyObject* item;
                while ((item = PyIter_Next(it))) {
                    PyObject* result = add(self, item);
                    Py_DECREF(item);
                    if (!result) {
                        Py_DECREF(it);
                        return -1;
                    }
                    Py_DECREF(result);
                }
                Py_DECREF(it);
                if (PyErr_Occurred()) return -1;
            }

            return 0;
        }

        static int traverse(WeakSet* self, visitproc visit, void* arg) {
            for (auto const& [_, weakref] : self->weak) {
                Py_VISIT(weakref);
            }
            for (auto const& obj : self->strong) {
                Py_VISIT(obj);
            }
            return 0;
        }

        static int clear(WeakSet* self) {
            for (auto const& [_, weakref] : self->weak) {
                Py_DECREF(weakref);
            }
            self->weak.clear();

            for (auto const& obj : self->strong) {
                Py_DECREF(obj);
            }
            self->strong.clear();
            return 0;
        }

        static void dealloc(WeakSet* self) {
            PyObject_GC_UnTrack(self);
            clear(self);
            self->weak.~map<PyObject*, PyObject*>();
            self->strong.~set<PyObject*>();
            Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
        }

        bool contains(PyObject* obj) const {
            return weak.contains(obj) || strong.contains(obj);
        }

        static Py_ssize_t sq_length(WeakSet* self) {
            return static_cast<Py_ssize_t>(self->weak.size() + self->strong.size());
        }

        static int sq_contains(WeakSet* self, PyObject* obj) {
            return self->contains(obj) ? 1 : 0;
        }

        static PyObject* call(PyObject* callable, PyObject* const* args, size_t nargsf, PyObject* kwnames) {
            auto* self = reinterpret_cast<WeakSet*>(callable);
            Py_ssize_t nargs = PyVectorcall_NARGS(nargsf);
            if (kwnames || nargs != 1) {
                PyErr_SetString(PyExc_TypeError,
                                "WeakSet(...) takes exactly one positional argument");
                return nullptr;
            }
            return Py_NewRef(self->contains(args[0]) ? Py_True : Py_False);
        }

        static PyObject* add(WeakSet* self, PyObject* obj) {
            if (self->contains(obj)) {
                Py_RETURN_FALSE;
            }

            auto* callback = PyObject_GC_New(WeakSetCallback, &WeakSetCallback_Type);
            if (!callback) return nullptr;
            callback->owner = self;
            Py_INCREF(self);
            callback->key = obj;
            PyObject_GC_Track(callback);

            PyObject* weakref = PyWeakref_NewRef(obj, reinterpret_cast<PyObject*>(callback));
            Py_DECREF(reinterpret_cast<PyObject*>(callback));
            if (!weakref) {
                if (!PyErr_ExceptionMatches(PyExc_TypeError)) {
                    return nullptr;
                }
                PyErr_Clear();

                auto [_, inserted] = self->strong.emplace(obj);
                if (inserted) {
                    Py_INCREF(obj);
                }
                return Py_NewRef(inserted ? Py_True : Py_False);
            }

            self->weak.emplace(obj, weakref);
            Py_RETURN_TRUE;
        }

        static PyObject* py_contains(WeakSet* self, PyObject* obj) {
            return Py_NewRef(self->contains(obj) ? Py_True : Py_False);
        }

        static PyObject* ordered(WeakSet* self, PyObject*) {
            PyObject* result = PyTuple_New(self->weak.size() + self->strong.size());
            if (!result) return nullptr;

            Py_ssize_t i = 0;
            for (auto const& [obj, _] : self->weak) {
                PyTuple_SET_ITEM(result, i++, Py_NewRef(obj));
            }
            for (auto const& obj : self->strong) {
                PyTuple_SET_ITEM(result, i++, Py_NewRef(obj));
            }

            return result;
        }

        static PyObject* discard(WeakSet* self, PyObject* obj) {
            auto weak_it = self->weak.find(obj);
            if (weak_it != self->weak.end()) {
                PyObject* weakref = weak_it->second;
                self->weak.erase(weak_it);
                Py_DECREF(weakref);
                Py_RETURN_TRUE;
            }

            auto strong_it = self->strong.find(obj);
            if (strong_it != self->strong.end()) {
                PyObject* strong_obj = *strong_it;
                self->strong.erase(strong_it);
                Py_DECREF(strong_obj);
                Py_RETURN_TRUE;
            }

            Py_RETURN_FALSE;
        }
    };

    static PyMethodDef weakset_methods[] = {
        {"add", (PyCFunction)WeakSet::add, METH_O, "Add an object by identity."},
        {"contains", (PyCFunction)WeakSet::py_contains, METH_O, "Return True if the object is in the set."},
        {"ordered", (PyCFunction)WeakSet::ordered, METH_NOARGS, "Return a snapshot of live objects."},
        {"discard", (PyCFunction)WeakSet::discard, METH_O, "Remove an object by identity if present."},
        {nullptr, nullptr, 0, nullptr}
    };

    static PySequenceMethods weakset_as_sequence = {
        .sq_length = (lenfunc)WeakSet::sq_length,
        .sq_contains = (objobjproc)WeakSet::sq_contains,
    };

    int WeakSetCallback::traverse(WeakSetCallback* self, visitproc visit, void* arg) {
        Py_VISIT(reinterpret_cast<PyObject*>(self->owner));
        return 0;
    }

    int WeakSetCallback::clear(WeakSetCallback* self) {
        Py_CLEAR(self->owner);
        self->key = nullptr;
        return 0;
    }

    void WeakSetCallback::dealloc(WeakSetCallback* self) {
        PyObject_GC_UnTrack(self);
        clear(self);
        Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
    }

    PyObject* WeakSetCallback::call(PyObject* self_obj, PyObject* args, PyObject*) {
        auto* self = reinterpret_cast<WeakSetCallback*>(self_obj);

        PyObject* exc_type;
        PyObject* exc_value;
        PyObject* exc_tb;
        PyErr_Fetch(&exc_type, &exc_value, &exc_tb);

        if (self->owner && self->key) {
            auto it = self->owner->weak.find(self->key);
            if (it != self->owner->weak.end()) {
                PyObject* weakref = it->second;
                self->owner->weak.erase(it);
                Py_DECREF(weakref);
            }
        }

        PyErr_Restore(exc_type, exc_value, exc_tb);
        Py_RETURN_NONE;
    }

    PyTypeObject WeakSet_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "WeakSet",
        .tp_basicsize = sizeof(WeakSet),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)WeakSet::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(WeakSet, vectorcall),
        .tp_as_sequence = &weakset_as_sequence,
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_traverse = (traverseproc)WeakSet::traverse,
        .tp_clear = (inquiry)WeakSet::clear,
        .tp_methods = weakset_methods,
        .tp_init = (initproc)WeakSet::init,
        .tp_new = WeakSet::create,
    };

    PyTypeObject WeakSetCallback_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "WeakSetCallback",
        .tp_basicsize = sizeof(WeakSetCallback),
        .tp_dealloc = (destructor)WeakSetCallback::dealloc,
        .tp_call = WeakSetCallback::call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
        .tp_traverse = (traverseproc)WeakSetCallback::traverse,
        .tp_clear = (inquiry)WeakSetCallback::clear,
    };
}
