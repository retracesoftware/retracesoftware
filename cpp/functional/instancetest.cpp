#include "functional.h"

struct InstanceTest : public PyObject {
    
    vectorcallfunc vectorcall;
    PyTypeObject * type;
    PyTypeObject * andnot;

    static int clear(InstanceTest* self) {
        Py_CLEAR(self->type);
        Py_CLEAR(self->andnot);
        return 0;
    }
    
    static int traverse(InstanceTest* self, visitproc visit, void* arg) {
        Py_VISIT(self->type);
        Py_VISIT(self->andnot);
        return 0;
    }
    
    static void dealloc(InstanceTest *self) {
        PyObject_GC_UnTrack(self);          // Untrack from the GC
        clear(self);
        Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
    }

    static PyObject * instanceof_andnot(InstanceTest * self, PyObject** args, size_t nargsf, PyObject* kwnames) {
        if (PyVectorcall_NARGS(nargsf) != 1 || kwnames) {
            PyErr_SetString(PyExc_TypeError, "instanceof takes one positional argument");
            return nullptr;
        }
        return PyBool_FromLong(PyObject_TypeCheck(args[0], self->type) && (!self->andnot || PyObject_TypeCheck(args[0], self->andnot)));
    }

    static PyObject * instanceof(InstanceTest * self, PyObject** args, size_t nargsf, PyObject* kwnames) {
        if (PyVectorcall_NARGS(nargsf) != 1 || kwnames) {
            PyErr_SetString(PyExc_TypeError, "instanceof takes one positional argument");
            return nullptr;
        }
        return PyBool_FromLong(PyObject_TypeCheck(args[0], self->type));
    }

    static PyObject * instancetest(InstanceTest * self, PyObject** args, size_t nargsf, PyObject* kwnames) {

        if (PyVectorcall_NARGS(nargsf) != 1 || kwnames) {
            PyErr_SetString(PyExc_TypeError, "instance_test takes one positional argument");
            return nullptr;
        }
        return Py_NewRef(PyObject_TypeCheck(args[0], self->type) ? args[0] : Py_None);
    }

    static PyObject * notinstancetest(InstanceTest * self, PyObject** args, size_t nargsf, PyObject* kwnames) {

        if (PyVectorcall_NARGS(nargsf) != 1 || kwnames) {
            PyErr_SetString(PyExc_TypeError, "notinstance_test takes one positional argument");
            return nullptr;
        }
        return Py_NewRef(PyObject_TypeCheck(args[0], self->type) ? Py_None : args[0]);
    }
};

PyTypeObject InstanceTest_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "InstanceTest",
    .tp_basicsize = sizeof(InstanceTest),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)InstanceTest::dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(InstanceTest, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "InstanceTest\n--\n\n"
               "Fast isinstance-style predicates with cached type checks.\n\n"
               "Internal type backing isinstanceof(), instance_test(), and\n"
               "notinstance_test() functions. Provides optimized type checking\n"
               "using direct PyObject_TypeCheck calls.",
    .tp_traverse = (traverseproc)InstanceTest::traverse,
    .tp_clear = (inquiry)InstanceTest::clear,

    // .tp_methods = methods,
    // .tp_members = members,
    // .tp_new = (newfunc)Partial::create,
    // .tp_init = (initproc)Partial::init,
    // .tp_new = PyType_GenericNew,
};

static PyObject * create(PyTypeObject * cls, PyTypeObject * andnot, vectorcallfunc func) {
    InstanceTest * self = (InstanceTest *)InstanceTest_Type.tp_alloc(&InstanceTest_Type, 0);
    
    if (!self) {
        return NULL;
    }
    Py_INCREF(cls);
    self->type = cls;

    if (andnot) {
        Py_INCREF(andnot);
        self->andnot = andnot;
    }
    self->vectorcall = func;
    return (PyObject *)self;
}

PyObject * instanceof_andnot(PyTypeObject * cls, PyTypeObject * andnot) {
    return create(cls, andnot, (vectorcallfunc)InstanceTest::instanceof_andnot);
}

PyObject * instanceof(PyTypeObject * cls) {
    return create(cls, nullptr, (vectorcallfunc)InstanceTest::instanceof);
}

PyObject * instance_test(PyTypeObject * cls) {
    return create(cls, nullptr, (vectorcallfunc)InstanceTest::instancetest);
}

PyObject * notinstance_test(PyTypeObject * cls) {
    return create(cls, nullptr, (vectorcallfunc)InstanceTest::notinstancetest);
}

