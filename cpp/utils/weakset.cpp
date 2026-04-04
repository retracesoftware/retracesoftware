#include "utils.h"
#include <algorithm>
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

    struct WeakEntry {
        PyObject* weakref;
        unsigned int index;

        WeakEntry(PyObject* weakref, unsigned int index)
            : weakref(weakref), index(index) {}
    };

    struct WeakSet : PyObject {
        map<PyObject*, WeakEntry> weak;
        map<PyObject*, unsigned int> strong;
        vectorcallfunc vectorcall;
        unsigned int current_index = 0;

        static PyObject* create(PyTypeObject* type, PyObject*, PyObject*) {
            auto* self = reinterpret_cast<WeakSet*>(type->tp_alloc(type, 0));
            if (!self) return nullptr;
            new (&self->weak) map<PyObject*, WeakEntry>();
            new (&self->strong) map<PyObject*, unsigned int>();
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
            for (auto const& [_, entry] : self->weak) {
                Py_VISIT(entry.weakref);
            }
            for (auto const& [obj, _] : self->strong) {
                Py_VISIT(obj);
            }
            return 0;
        }

        static int clear(WeakSet* self) {
            for (auto const& [_, entry] : self->weak) {
                Py_DECREF(entry.weakref);
            }
            self->weak.clear();

            for (auto const& [obj, _] : self->strong) {
                Py_DECREF(obj);
            }
            self->strong.clear();
            return 0;
        }

        static void dealloc(WeakSet* self) {
            PyObject_GC_UnTrack(self);
            clear(self);
            self->weak.~map<PyObject*, WeakEntry>();
            self->strong.~map<PyObject*, unsigned int>();
            Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
        }

        bool contains(PyObject* obj) const {
            return weak.contains(obj) || strong.contains(obj);
        }

        bool try_get_index(PyObject* obj, unsigned int* index) const {
            auto weak_it = weak.find(obj);
            if (weak_it != weak.end()) {
                *index = weak_it->second.index;
                return true;
            }

            auto strong_it = strong.find(obj);
            if (strong_it != strong.end()) {
                *index = strong_it->second;
                return true;
            }

            return false;
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

                auto [_, inserted] = self->strong.emplace(obj, self->current_index++);
                if (inserted) {
                    Py_INCREF(obj);
                } else {
                    self->current_index--;
                }
                return Py_NewRef(inserted ? Py_True : Py_False);
            }

            self->weak.emplace(obj, WeakEntry(weakref, self->current_index++));
            Py_RETURN_TRUE;
        }

        static PyObject* py_contains(WeakSet* self, PyObject* obj) {
            return Py_NewRef(self->contains(obj) ? Py_True : Py_False);
        }

        static PyObject* index(WeakSet* self, PyObject* obj) {
            unsigned int index;
            if (!self->try_get_index(obj, &index)) {
                Py_RETURN_NONE;
            }
            return PyLong_FromUnsignedLong(index);
        }

        static PyObject* ordered(WeakSet* self, PyObject*) {
            std::vector<std::pair<unsigned int, PyObject*>> ordered_entries;
            ordered_entries.reserve(self->weak.size() + self->strong.size());

            for (auto const& [obj, entry] : self->weak) {
                ordered_entries.emplace_back(entry.index, obj);
            }

            for (auto const& [obj, index] : self->strong) {
                ordered_entries.emplace_back(index, obj);
            }

            std::sort(
                ordered_entries.begin(),
                ordered_entries.end(),
                [](auto const& left, auto const& right) {
                    return left.first < right.first;
                });

            PyObject* result = PyTuple_New(ordered_entries.size());
            if (!result) return nullptr;

            for (Py_ssize_t i = 0; i < static_cast<Py_ssize_t>(ordered_entries.size()); ++i) {
                PyTuple_SET_ITEM(result, i, Py_NewRef(ordered_entries[i].second));
            }

            return result;
        }

        static PyObject* discard(WeakSet* self, PyObject* obj) {
            auto weak_it = self->weak.find(obj);
            if (weak_it != self->weak.end()) {
                PyObject* weakref = weak_it->second.weakref;
                self->weak.erase(weak_it);
                Py_DECREF(weakref);
                Py_RETURN_TRUE;
            }

            auto strong_it = self->strong.find(obj);
            if (strong_it != self->strong.end()) {
                PyObject* strong_obj = strong_it->first;
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
        {"index", (PyCFunction)WeakSet::index, METH_O, "Return the insertion index for an object, or None."},
        {"ordered", (PyCFunction)WeakSet::ordered, METH_NOARGS, "Return live objects in insertion order."},
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
                PyObject* weakref = it->second.weakref;
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
        .tp_as_sequence = &weakset_as_sequence,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(WeakSet, vectorcall),
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
