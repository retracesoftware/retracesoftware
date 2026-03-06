#include "functional.h"
#include "object.h"
#include "pyerrors.h"
#include <structmember.h>

struct FirstOf : public PyVarObject {
    vectorcallfunc vectorcall;
    // std::vector<std::pair<PyTypeObject *, PyObject *>> dispatch;
    retracesoftware::FastCall dispatch[];
};

static PyObject * vectorcall(FirstOf * self, PyObject** args, size_t nargsf, PyObject* kwnames) {

    retracesoftware::FastCall * pair = self->dispatch;

    for (size_t i = 0; i < (size_t)self->ob_size - 1; i++) {

        PyObject * res = pair->vectorcall(pair->callable, args, nargsf, kwnames);
        if (!res) return nullptr;
        else if (res == Py_None) {
            Py_DECREF(res);
        } else {
            return res;
        }
        pair++;
    }
    return pair->vectorcall(pair->callable, args, nargsf, kwnames);
}

static int traverse(FirstOf* self, visitproc visit, void* arg) {
    for (size_t i = 0; i < (size_t)self->ob_size; i++) {
        Py_VISIT(self->dispatch[i].callable);
    } 
    return 0;
}

static int clear(FirstOf* self) {
    for (size_t i = 0; i < (size_t)self->ob_size; i++) {
        Py_CLEAR(self->dispatch[i].callable);
    } 
    return 0;
}

static void dealloc(FirstOf *self) {
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static PyMemberDef members[] = {
    // {"elements", T_OBJECT, offsetof(CasePredicate, elements), READONLY, "TODO"},
    {NULL}  /* Sentinel */
};

PyTypeObject FirstOf_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "firstof",
    .tp_basicsize = sizeof(FirstOf),
    .tp_itemsize = sizeof(retracesoftware::FastCall),
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(FirstOf, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "firstof(*functions)\n--\n\n"
               "Like first(), but optimized with cached vectorcall pointers.\n\n"
               "Calls functions in order until one returns a non-None value.\n"
               "The last function is always called (no None check), useful for\n"
               "providing a guaranteed fallback.\n\n"
               "Args:\n"
               "    *functions: Callables to try in order.\n\n"
               "Returns:\n"
               "    First non-None result, or result of last function.",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    .tp_members = members,
};

PyObject * firstof(PyObject * const * args, size_t nargs) {

    FirstOf * self = (FirstOf *)FirstOf_Type.tp_alloc(&FirstOf_Type, nargs);
    
    if (!self) {
        return NULL;
    }

    for (size_t i = 0; i < nargs; i++) {
        self->dispatch[i] = retracesoftware::FastCall(Py_NewRef(args[i]));
    }

    self->vectorcall = (vectorcallfunc)vectorcall;

    return (PyObject *)self;
}
