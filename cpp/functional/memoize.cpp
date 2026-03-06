#include "functional.h"
#include <structmember.h>
#include "unordered_dense.h"

using namespace ankerl::unordered_dense;

struct Memoize {
    PyObject_HEAD
    PyObject * target;
    PyObject * callback;
    map<PyObject *, PyObject *> weakref_to_key;
    map<PyObject *, PyObject *> m_cache;

    Memoize(PyObject * target) : target(Py_NewRef(target)), weakref_to_key(), m_cache() {}
    ~Memoize() {}

    vectorcallfunc vectorcall;
};

static void delete_key(Memoize * self, PyObject * key) {
    auto it = self->m_cache.find(key);
    if (it != self->m_cache.end()) {
        self->m_cache.erase(key);
        Py_DECREF(it->second);
    }
}

static PyObject * weakref_callback(Memoize * self, PyObject * weakref) {

    auto it = self->weakref_to_key.find(weakref);

    if (it != self->weakref_to_key.end()) {
        self->weakref_to_key.erase(it);
        delete_key(self, it->second);
        // Py_DECREF(it->first);
    }
    Py_RETURN_NONE;
}

static PyObject * memo_one_arg(Memoize * self, PyObject * arg) {
    auto it = self->m_cache.find(arg);

    if (it == self->m_cache.end()) {

        PyObject * res = PyObject_CallOneArg(self->target, arg);

        if (!res) return nullptr;
    
        PyObject * weakref = PyWeakref_NewRef(arg, self->callback);

        if (weakref) {
            self->weakref_to_key[weakref] = arg;
        }
        else {
            PyErr_Clear();
            Py_INCREF(arg);
        }
        self->m_cache[arg] = res;

        return Py_NewRef(res);
    } else {
        return Py_NewRef(it->second);
    }
}

static PyObject * vectorcall_one_arg(Memoize * self, PyObject** args, size_t nargsf, PyObject* kwnames) {

    size_t nargs = PyVectorcall_NARGS(nargsf);

    if (nargs != 1 || kwnames) {
        raise(SIGTRAP);

        PyErr_SetString(PyExc_TypeError, "Memo only takes one arg");
        return nullptr;
    }
    return memo_one_arg(self, args[0]);
}

static int traverse(Memoize* self, visitproc visit, void* arg) {
    Py_VISIT(self->target);
    Py_VISIT(self->callback);
    return 0;
}

static int clear(Memoize* self) {
    Py_CLEAR(self->target);
    Py_CLEAR(self->callback);

    for (auto it : self->m_cache) {
        Py_DECREF(it.first);
        Py_DECREF(it.second);
    }
    self->m_cache.clear();

    return 0;
}

static void dealloc(Memoize *self) {
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    self->~Memoize();
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static PyMemberDef members[] = {
    {"target", T_OBJECT, offsetof(Memoize, target), READONLY, "The wrapped function being memoized."},
    {NULL}  /* Sentinel */
};

static PyObject * create(PyTypeObject *type, PyObject *args, PyObject *kwds) {

    PyObject * target;
    
    static const char *kwlist[] = {"target", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O", (char **)kwlist, &target))
    {
        return NULL; // Return NULL on failure
    }
    
    Memoize * self = (Memoize *)type->tp_alloc(type, 0);

    if (!self) {
        return NULL;
    }

    new (self) Memoize(target);

    static PyMethodDef def = { "weakref_callback", (PyCFunction)weakref_callback, METH_O, "Internal callback to evict cache entries when keys are garbage collected." };

    self->callback = PyCFunction_New(&def, (PyObject *)self);

    self->vectorcall = (vectorcallfunc)vectorcall_one_arg;

    return (PyObject *)self;
}

PyTypeObject Memoize_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "memoize_one_arg",
    .tp_basicsize = sizeof(Memoize),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = offsetof(Memoize, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "memoize_one_arg(target)\n--\n\n"
               "Memoize a single-argument function using object identity.\n\n"
               "Uses a high-performance C++ hash map (unordered_dense) for O(1) lookups.\n"
               "Automatically evicts cached entries when keys are garbage collected\n"
               "via weak references.\n\n"
               "Args:\n"
               "    target: A callable that takes exactly one argument.\n\n"
               "Returns:\n"
               "    A memoized version of the function.\n\n"
               "Example:\n"
               "    >>> @memoize_one_arg\n"
               "    ... def expensive(obj):\n"
               "    ...     return compute(obj)\n"
               "    >>> expensive(x)  # computed\n"
               "    >>> expensive(x)  # cached",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    .tp_members = members,
    .tp_new = (newfunc)create,
};
