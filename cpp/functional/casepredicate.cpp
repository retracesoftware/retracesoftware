#include "functional.h"
#include "object.h"
#include "pyerrors.h"
#include <structmember.h>

struct Pair {
    vectorcallfunc vectorcall;
    PyObject * callable;
};

struct IfThen {
    Pair test;
    Pair then;
};

struct CasePredicate : public PyVarObject {
    vectorcallfunc vectorcall;
    // std::vector<std::pair<PyTypeObject *, PyObject *>> dispatch;
    Pair otherwise;
    IfThen dispatch[];
};

static PyObject * vectorcall(CasePredicate * self, PyObject** args, size_t nargsf, PyObject* kwnames) {

    for (size_t i = 0; i < (size_t)self->ob_size; i++) {

        IfThen * if_then = self->dispatch + i;

        PyObject * res = if_then->test.vectorcall(if_then->test.callable, args, nargsf, kwnames);

        if (!res) return nullptr;
        int is_true = PyObject_IsTrue(res);
        Py_DECREF(res);

        switch (is_true) {
        case 0: break;
        case 1: return if_then->then.vectorcall(if_then->then.callable, args, nargsf, kwnames);
        default: return nullptr;
        }
    }
    if (self->otherwise.callable) {
        return self->otherwise.vectorcall(self->otherwise.callable, args, nargsf, kwnames);
    }
    Py_RETURN_NONE;
}

static int traverse(CasePredicate* self, visitproc visit, void* arg) {
    for (size_t i = 0; i < (size_t)self->ob_size; i++) {
        Py_VISIT(self->dispatch[i].test.callable);
        Py_VISIT(self->dispatch[i].then.callable);
    } 
    Py_VISIT(self->otherwise.callable);

    return 0;
}

static int clear(CasePredicate* self) {
    for (size_t i = 0; i < (size_t)self->ob_size; i++) {
        Py_CLEAR(self->dispatch[i].test.callable);
        Py_CLEAR(self->dispatch[i].then.callable);
    } 
    Py_CLEAR(self->otherwise.callable);
    return 0;
}

static void dealloc(CasePredicate *self) {
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static PyMemberDef members[] = {
    // {"elements", T_OBJECT, offsetof(CasePredicate, elements), READONLY, "TODO"},
    {NULL}  /* Sentinel */
};

PyTypeObject CasePredicate_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "dispatch",
    .tp_basicsize = sizeof(CasePredicate),
    .tp_itemsize = sizeof(IfThen),
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(CasePredicate, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "dispatch(test1, then1, test2, then2, ..., [otherwise])\n--\n\n"
               "Pattern matching / case dispatch with short-circuit evaluation.\n\n"
               "Tests predicates in order; on first truthy result, calls the\n"
               "corresponding 'then' function. If no predicate matches and an\n"
               "odd number of args is given, the last arg is called as fallback.\n\n"
               "Args:\n"
               "    Alternating (test, then) pairs, optionally ending with otherwise.\n\n"
               "Returns:\n"
               "    Result of the matched branch, or None if no match and no fallback.",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    .tp_members = members,
};

PyObject * dispatch(PyObject * const * args, size_t nargs) {

    CasePredicate * self = (CasePredicate *)CasePredicate_Type.tp_alloc(&CasePredicate_Type, (nargs >> 1));
    
    if (!self) {
        return NULL;
    }

    for (size_t pair = 0; pair < (nargs >> 1); pair++) {
        self->dispatch[pair].test.vectorcall = extract_vectorcall(args[pair * 2]);
        self->dispatch[pair].test.callable = Py_NewRef(args[pair * 2]);
        self->dispatch[pair].then.vectorcall = extract_vectorcall(args[(pair * 2) + 1]);
        self->dispatch[pair].then.callable = Py_NewRef(args[(pair * 2) + 1]);
    }
    if (nargs & 0x1) {
        self->otherwise.vectorcall = extract_vectorcall(args[nargs - 1]);
        self->otherwise.callable = Py_NewRef(args[nargs - 1]);
    } else {
        self->otherwise.callable = nullptr;
    }
    self->vectorcall = (vectorcallfunc)vectorcall;

    return (PyObject *)self;
}
