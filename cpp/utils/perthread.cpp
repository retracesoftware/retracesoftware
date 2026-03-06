#include "utils.h"
#include <structmember.h>
#include "unordered_dense.h"

using namespace ankerl::unordered_dense;

// #define MAGIC 0x8E78ACE50F73A8E

namespace retracesoftware {

    struct PerThread;

    class PerThreadLookup {
        map<PerThread *, PyObject *> lookup; 
    
    public:
        PerThreadLookup() : lookup() {}

        PyObject * get(PerThread * per_thread, PyObject * create) {
            auto it = lookup.find(per_thread);
            if (it != lookup.end()) return it->second;

            PyObject * obj = PyObject_CallNoArgs(create);

            if (obj) {
                Py_INCREF(per_thread);
                lookup.emplace(per_thread, obj);
            }
            return obj;
        }

        ~PerThreadLookup() {
            if (Py_IsInitialized()) {
                for (const auto& pair : lookup) {
                    assert(Py_REFCNT(pair.first) > 0);
                    Py_DECREF(pair.first);
                    assert(Py_REFCNT(pair.second) > 0);
                    Py_DECREF(pair.second);
                }
            }
        }
    };

    static thread_local PerThreadLookup lookup;
    
    struct PerThread : public PyObject { 
        PyObject * create;
        vectorcallfunc vectorcall;

        static int traverse(PerThread * self, visitproc visit, void* arg) {
            Py_VISIT(self->create);
            return 0;
        }

        static int clear(PerThread * self) {
            Py_CLEAR(self->create);
            return 0;
        }

        static void dealloc(PerThread *self) {
            PyObject_GC_UnTrack(self);          // Untrack from the GC
            clear(self);
            Py_TYPE(self)->tp_free(self);  // Free the object
        }

        static int init(PerThread *self, PyObject *args, PyObject *kwds) {
            PyObject * create;

            static const char *kwlist[] = { "create", NULL};

            if (!PyArg_ParseTupleAndKeywords(args, kwds, "O", (char **)kwlist, &create)) {
                return -1; // Return NULL on failure
            }

            self->create = Py_NewRef(create);
            self->vectorcall = (vectorcallfunc)call;
            return 0;
        }

        static PyObject * call(PerThread * self, PyObject** args, size_t nargsf, PyObject* kwnames) {
            return Py_XNewRef(lookup.get(self, self->create));
            // PyObject * target = lookup.get(self, self->create);
            // if (!target) return nullptr;
            // return PyObject_Vectorcall(target, args, nargsf, kwnames);
        }

        // static PyObject * py_get(PerThread * self, PyObject * unused) {
        //     return Py_XNewRef(lookup.get(self, self->create));
        // }
    };

    // ---- methods table
    static PyMethodDef methods[] = {
        // {"get", (PyCFunction)PerThread::py_get, METH_NOARGS, "TODO"},
        // {"remove", (PyCFunction)FastSet::remove, METH_O, "Remove object by identity; raises KeyError if absent"},
        {nullptr, nullptr, 0, nullptr}
    };

    PyTypeObject PerThread_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "perthread",
        .tp_basicsize = sizeof(PerThread),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)PerThread::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(PerThread, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)PerThread::traverse,
        .tp_clear = (inquiry)PerThread::clear,
        .tp_methods = methods,
        // .tp_members = members,
        .tp_init = (initproc)PerThread::init,
        .tp_new = PyType_GenericNew,
    };
};