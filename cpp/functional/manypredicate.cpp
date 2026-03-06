#include "functional.h"
#include <structmember.h>

static int traverse(ManyPredicate* self, visitproc visit, void* arg) {
    Py_VISIT(self->elements);

    return 0;
}

static int clear(ManyPredicate* self) {
    Py_CLEAR(self->elements);
    return 0;
}

static void dealloc(ManyPredicate *self) {
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    if (self->weakreflist) {
        PyObject_ClearWeakRefs(self);
    }
    Py_TYPE(self)->tp_free(self);  // Free the object
}

static PyMemberDef members[] = {
    {"elements", T_OBJECT, OFFSET_OF_MEMBER(ManyPredicate, elements), READONLY, "The tuple of predicates combined in this compound predicate."},
    {NULL}  /* Sentinel */
};

// static PyObject * create(PyTypeObject *type, PyObject *args, PyObject *kwds) {
//     ManyPredicate * self = (ManyPredicate *)type->tp_alloc(type, 0);
//     if (!self) {
//         return NULL;
//     }
//     self->elements = Py_NewRef(args);
//     self->vectorcall = (vectorcallfunc)vectorcall;
//     return (PyObject *)self;
// }

static PyObject * tp_str(ManyPredicate * self) {

    PyObject * joined = join(", ", self->elements);
    if (!joined) return nullptr;

    PyObject * result = PyUnicode_FromFormat("%s(%U)", Py_TYPE(self)->tp_name, joined);
    Py_DECREF(joined);
    return result;
}

static Py_ssize_t length(ManyPredicate *self) {
    return PyTuple_Size(self->elements);
}

static PyObject *subscript(ManyPredicate *self, PyObject *key) {
    if (!PyLong_Check(key)) {  // Ensure key is an int
        PyErr_SetString(PyExc_TypeError, "Index must be an integer");
        return NULL;
    }

    long index = PyLong_AS_LONG(key);

    Py_ssize_t size = PyTuple_Size(self->elements);

    if (index < 0 || index >= size) {
        PyErr_SetString(PyExc_IndexError, "index out of range");
        return NULL;
    }

    return Py_NewRef(PyTuple_GetItem(self->elements, index));
}

static PyObject * richcompare(PyObject *a, PyObject *b, int op) {

    if (Py_TYPE(a) != Py_TYPE(b)) {
        Py_RETURN_FALSE;
    }

    return PyObject_RichCompare(
        reinterpret_cast<ManyPredicate *>(a)->elements,
        reinterpret_cast<ManyPredicate *>(b)->elements, op);
}

static PyMappingMethods mapping = {
    .mp_length = (lenfunc) length,  // Optional: Provide length support if needed
    .mp_subscript = (binaryfunc) subscript,  // Implements obj[key]
    .mp_ass_subscript = NULL  // Implements obj[key] = value (see below)
};

/* __hash__ implementation */
static Py_hash_t hash(ManyPredicate *self) {

    PyObject * with_type = PyTuple_Pack(2, Py_TYPE(self), self->elements);
    Py_hash_t h = PyObject_Hash(with_type);
    Py_DECREF(with_type);
    return h;
}

PyTypeObject ManyPredicate_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "many_predicate",
    .tp_basicsize = sizeof(ManyPredicate),
    .tp_itemsize = 0,
    // .tp_alloc = PyType_GenericAlloc,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(ManyPredicate, vectorcall),
    .tp_as_mapping = &mapping,
    .tp_hash = (hashfunc) hash,
    .tp_call = PyVectorcall_Call,
    .tp_str = (reprfunc)tp_str,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL | Py_TPFLAGS_BASETYPE,
    .tp_doc = "many_predicate\n--\n\n"
               "Base class for compound predicates (and_predicate, or_predicate).\n\n"
               "Stores a tuple of predicates and provides common functionality\n"
               "like hashing, equality, subscript access, and iteration.",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    .tp_richcompare = richcompare,
    .tp_weaklistoffset = OFFSET_OF_MEMBER(ManyPredicate, weakreflist),
    .tp_members = members
};
