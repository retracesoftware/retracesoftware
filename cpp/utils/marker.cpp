#include "utils.h"
#include <exception>
#include <structmember.h>

namespace retracesoftware {
    struct FastTypePredicate : public PyObject {
        PyObject* predicate;
        set<PyTypeObject*>* inside;
        set<PyTypeObject*>* outside;
        map<PyTypeObject*, PyObject*>* weakrefs;
    };

    struct FastTypePredicateWeakrefCallback : public PyObject {
        FastTypePredicate* owner;
        PyTypeObject* type;
    };
    
    PyTypeObject Marker_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "marker",
        .tp_basicsize = sizeof(PyObject),
        .tp_itemsize = 0,
        // .tp_getattro = tp_getattro,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
        .tp_doc = "Marker type",
        .tp_base = &PyBaseObject_Type,
    };

    PyTypeObject Patched_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "Patched",
        .tp_basicsize = sizeof(PyObject),
        .tp_itemsize = 0,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
        .tp_doc = "Patched marker type",
        .tp_base = &PyBaseObject_Type,
        .tp_new = PyType_GenericNew,
    };

    static void fast_type_predicate_weakref_callback_dealloc(FastTypePredicateWeakrefCallback* self) {
        PyObject_Free(self);
    }

    static PyObject* fast_type_predicate_weakref_callback_call(PyObject* self_obj, PyObject* args, PyObject*) {
        auto* self = reinterpret_cast<FastTypePredicateWeakrefCallback*>(self_obj);

        PyObject *exc_type, *exc_value, *exc_tb;
        PyErr_Fetch(&exc_type, &exc_value, &exc_tb);

        if (self->owner && self->owner->inside) {
            self->owner->inside->erase(self->type);
        }
        if (self->owner && self->owner->outside) {
            self->owner->outside->erase(self->type);
        }
        if (self->owner && self->owner->weakrefs) {
            PyObject* weakref = self->owner->weakrefs->contains(self->type)
                ? self->owner->weakrefs->at(self->type)
                : nullptr;
            self->owner->weakrefs->erase(self->type);
            Py_XDECREF(weakref);
        }

        PyErr_Restore(exc_type, exc_value, exc_tb);

        Py_RETURN_NONE;
    }

    PyTypeObject FastTypePredicateWeakrefCallback_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "FastTypePredicateWeakrefCallback",
        .tp_basicsize = sizeof(FastTypePredicateWeakrefCallback),
        .tp_dealloc = (destructor)fast_type_predicate_weakref_callback_dealloc,
        .tp_call = fast_type_predicate_weakref_callback_call,
        .tp_flags = Py_TPFLAGS_DEFAULT,
    };

    static int fast_type_predicate_track_type(FastTypePredicate* self, PyTypeObject* type) {
        if (!PyType_HasFeature(type, Py_TPFLAGS_HEAPTYPE)) {
            return 0;
        }
        if (self->weakrefs->contains(type)) {
            return 0;
        }

        auto* callback = PyObject_New(FastTypePredicateWeakrefCallback, &FastTypePredicateWeakrefCallback_Type);
        if (!callback) {
            return -1;
        }

        callback->owner = self;
        callback->type = type;

        PyObject* weakref = PyWeakref_NewRef(reinterpret_cast<PyObject*>(type), reinterpret_cast<PyObject*>(callback));
        Py_DECREF(reinterpret_cast<PyObject*>(callback));
        if (!weakref) {
            return -1;
        }

        (*self->weakrefs)[type] = weakref;
        return 0;
    }

    static int fast_type_predicate_store(FastTypePredicate* self, PyTypeObject* type, bool matches) {
        if (matches) {
            self->outside->erase(type);
            self->inside->insert(type);
        } else {
            self->inside->erase(type);
            self->outside->insert(type);
        }
        return fast_type_predicate_track_type(self, type);
    }

    static int fast_type_predicate_check(FastTypePredicate* self, PyObject* arg) {
        auto* type = Py_TYPE(arg);
        if (self->inside->contains(type)) {
            return 1;
        }
        if (self->outside->contains(type)) {
            return 0;
        }

        PyObject* result = PyObject_CallOneArg(self->predicate, reinterpret_cast<PyObject*>(type));
        if (!result) {
            return -1;
        }

        int matches = PyObject_IsTrue(result);
        Py_DECREF(result);
        if (matches < 0) {
            return -1;
        }
        if (fast_type_predicate_store(self, type, matches != 0) < 0) {
            return -1;
        }
        return matches;
    }

    static PyObject* fast_type_predicate_istypeof(FastTypePredicate* self, PyObject* arg) {
        int matches = fast_type_predicate_check(self, arg);
        if (matches < 0) {
            return nullptr;
        }
        return PyBool_FromLong(matches);
    }

    static PyObject* fast_type_predicate_call(PyObject* self_obj, PyObject* args, PyObject*) {
        auto* self = reinterpret_cast<FastTypePredicate*>(self_obj);
        PyObject* arg = nullptr;
        if (!PyArg_ParseTuple(args, "O", &arg)) {
            return nullptr;
        }
        return fast_type_predicate_istypeof(self, arg);
    }

    static PyMethodDef FastTypePredicate_methods[] = {
        {"istypeof", (PyCFunction)fast_type_predicate_istypeof, METH_O, "Return whether obj's exact type matches the cached predicate."},
        {NULL}
    };

    static PyObject* fast_type_predicate_create(PyTypeObject* cls, PyObject* args, PyObject* kwds) {
        FastTypePredicate* self = reinterpret_cast<FastTypePredicate*>(cls->tp_alloc(cls, 0));
        if (!self) {
            return nullptr;
        }

        self->predicate = nullptr;
        self->inside = new set<PyTypeObject*>();
        self->outside = new set<PyTypeObject*>();
        self->weakrefs = new map<PyTypeObject*, PyObject*>();
        return reinterpret_cast<PyObject*>(self);
    }

    static int fast_type_predicate_init(FastTypePredicate* self, PyObject* args, PyObject* kwds) {
        PyObject* predicate = nullptr;
        static const char* kwlist[] = {"predicate", NULL};

        if (!PyArg_ParseTupleAndKeywords(args, kwds, "O", (char**)kwlist, &predicate)) {
            return -1;
        }
        if (!PyCallable_Check(predicate)) {
            PyErr_SetString(PyExc_TypeError, "predicate must be callable");
            return -1;
        }

        Py_XDECREF(self->predicate);
        Py_INCREF(predicate);
        self->predicate = predicate;
        return 0;
    }

    static void fast_type_predicate_dealloc(FastTypePredicate* self) {
        Py_CLEAR(self->predicate);
        if (self->weakrefs) {
            for (auto& [_, weakref] : *self->weakrefs) {
                Py_DECREF(weakref);
            }
            delete self->weakrefs;
            self->weakrefs = nullptr;
        }
        delete self->inside;
        self->inside = nullptr;
        delete self->outside;
        self->outside = nullptr;
        Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
    }

    PyTypeObject FastTypePredicate_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "FastTypePredicate",
        .tp_basicsize = sizeof(FastTypePredicate),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)fast_type_predicate_dealloc,
        .tp_call = fast_type_predicate_call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
        .tp_doc = "Memoized exact-type predicate with positive and negative caches.",
        .tp_methods = FastTypePredicate_methods,
        .tp_init = (initproc)fast_type_predicate_init,
        .tp_new = (newfunc)fast_type_predicate_create,
    };
}
