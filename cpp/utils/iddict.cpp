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

    struct WeakRefCallback : public PyObject {
        PyObject * handle;
        PyObject * id_dict;
        vectorcallfunc vectorcall;
        
        static int traverse(WeakRefCallback* self, visitproc visit, void* arg) {
            Py_VISIT(self->id_dict);
            return 0;
        }

        static int clear(WeakRefCallback* self) {
            Py_CLEAR(self->id_dict);
            return 0;
        }

        static void dealloc(WeakRefCallback* self) {
            PyObject_GC_UnTrack(self);
            clear(self);
            Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
        }
    };

    struct IdDict : PyObject {
        map<PyObject *, PyObject *> contents;
        map<PyObject *, PyObject *> key_to_weakref;

        // ---- GC: traverse each contained PyObject so the GC sees edges
        static int traverse(IdDict* self, visitproc visit, void* arg) {
            for (const auto& pair : self->contents) {
                Py_VISIT(pair.second);
            }
            return 0;
        }

        // ---- GC: clear strong refs (drop edges)
        static int clear(IdDict* self) {
            for (const auto& pair : self->contents) {
                Py_DECREF(pair.second);
            }
            self->contents.clear();
            return 0;
        }

        // ---- dealloc: untrack, clear, then free
        static void dealloc(IdDict* self) {
            PyObject_GC_UnTrack(self);
            clear(self);

            self->contents.~map<PyObject*, PyObject*>();
            
            Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
        }

        static PyObject* create(PyTypeObject * type, PyObject*, PyObject*) {
            auto* self = reinterpret_cast<IdDict*>(type->tp_alloc(type, 0));
            if (!self) return nullptr;

            // Construct the std::set in-place
            new (&self->contents) map<PyObject*, PyObject *>();

            return reinterpret_cast<PyObject*>(self);
        }

        static PyObject * WeakRefCallback_vectorcall(WeakRefCallback * self, PyObject *const * args, size_t nargsf, PyObject* kwnames) {
            
            IdDict * id_dict = reinterpret_cast<IdDict *>(self->id_dict);

            assert(id_dict->contents.contains(self->handle));

            Py_DECREF(id_dict->contents[self->handle]);
            id_dict->contents.erase(self->handle);
            
            Py_DECREF(args[0]);

            Py_RETURN_NONE;
        }

        PyObject * weakref_callback(PyObject* handle) {
            WeakRefCallback * self = (WeakRefCallback *)WeakRefCallback_Type.tp_alloc(&WeakRefCallback_Type, 0);
            if (!self) return nullptr;

            self->id_dict = Py_NewRef(this);
            self->handle = handle;
            self->vectorcall = (vectorcallfunc)WeakRefCallback_vectorcall;
            
            return (PyObject *)self;
        }

        // bool contains(PyObject * obj) const {
        //     return contents.find(obj) != contents.end();
        // }

        bool add(PyObject* key, PyObject* value) {
            if (contents.contains(key)) {
                Py_DECREF(contents[key]);
                contents[key] = Py_NewRef(value);
            } else {
                PyObject * callback = weakref_callback(key);

                if (!PyWeakref_NewRef(key, callback)) {
                    Py_DECREF(callback);
                    return false;
                }
                Py_DECREF(callback);
                contents[key] = Py_NewRef(value);
            }
            return true;
        }

        static PyObject* py_get_else_key(IdDict* self, PyObject* obj) {
            return Py_NewRef(self->contents.contains(obj) ? self->contents[obj] : obj);
        }

        static PyObject *getitem(IdDict *self, PyObject *key) {
            if (self->contents.contains(key)) {
                return Py_NewRef(self->contents[key]);
            } else {
                PyErr_SetString(PyExc_KeyError, "Key not found");
                return nullptr;
            }
        }

        static Py_ssize_t len(IdDict *self) {
            return (Py_ssize_t)self->contents.size();
        }

        static int setitem(IdDict *self, PyObject *key, PyObject *value) {
            if (value == NULL) {
                PyErr_SetString(PyExc_RuntimeError, "id_dict does not currently support deletion");
                return -1;
                // Handle deletion (del obj[key])
            } else {
                return self->add(key, value) ? 0 : -1;
            }
            // Return 0 on success, -1 on failure
        }
    };

    // ---- methods table
    static PyMethodDef methods[] = {
        // {"contains", (PyCFunction)InstanceCheck::py_contains, METH_O, "Add an object by identity; returns True if newly added"},

        {"get_else_key", (PyCFunction)IdDict::py_get_else_key, METH_O, "TODO"},

        // {"add", (PyCFunction)IdSet::py_add, METH_O, "Add an object by identity; returns True if newly added"},
        // {"test", (PyCFunction)InstanceCheck::test, METH_O, "returns input if in set, else None"},
        // {"remove", (PyCFunction)IdSet::py_remove, METH_O, "Remove object by identity; raises KeyError if absent"},
        {nullptr, nullptr, 0, nullptr}
    };

    static PyMappingMethods mapping = {
        (lenfunc)IdDict::len,              // mp_length
        (binaryfunc)IdDict::getitem,       // mp_subscript
        (objobjargproc)IdDict::setitem,    // mp_ass_subscript
    };

    PyTypeObject WeakRefCallback_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "WeakRefCallback",
        .tp_basicsize = sizeof(WeakRefCallback),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)WeakRefCallback::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(WeakRefCallback, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)WeakRefCallback::traverse,
        .tp_clear = (inquiry)WeakRefCallback::clear,
    };

    // ---- type object
    PyTypeObject IdDict_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "id_dict",
        .tp_basicsize = sizeof(IdDict),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)IdDict::dealloc,
        // .tp_vectorcall_offset = OFFSET_OF_MEMBER(IdDict, vectorcall),
        .tp_as_mapping = &mapping,
        // .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_BASETYPE,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)IdDict::traverse,
        .tp_clear = (inquiry)IdDict::clear,
        .tp_methods = methods,
        // .tp_init = (initproc)IdSet::init,
        .tp_new = IdDict::create,
    };
}