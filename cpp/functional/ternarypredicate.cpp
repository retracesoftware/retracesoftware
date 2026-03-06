#include "functional.h"
#include <structmember.h>

struct TernaryPredicate : PyObject {
    PyObject * condition;
    PyObject * on_true;
    PyObject * on_false;
    vectorcallfunc vectorcall;

    static PyObject * call(TernaryPredicate * self, PyObject* const * args, size_t nargsf, PyObject* kwnames) {
        PyObject * branch = PyObject_Vectorcall(self->condition, args, nargsf, kwnames);

        if (!branch) return nullptr;

        int res = PyObject_IsTrue(branch);

        Py_DECREF(branch);

        switch (res) {
            case 0:
                return PyObject_Vectorcall(self->on_false, args, nargsf, kwnames);
            case 1:
                return PyObject_Vectorcall(self->on_true, args, nargsf, kwnames);
            default:
                return nullptr;
        }
    }

    static int traverse(TernaryPredicate* self, visitproc visit, void* arg) {
        Py_VISIT(self->condition);
        Py_VISIT(self->on_true);
        Py_VISIT(self->on_false);
        return 0;
    }

    static int clear(TernaryPredicate* self) {
        Py_CLEAR(self->condition);
        Py_CLEAR(self->on_true);
        Py_CLEAR(self->on_false);
        return 0;
    }

    static int init(TernaryPredicate *self, PyObject *args, PyObject *kwds) {

        PyObject * condition;
        PyObject * on_true;
        PyObject * on_false;

        static const char *kwlist[] = {"condition", "on_true", "on_false", NULL};

        if (!PyArg_ParseTupleAndKeywords(args, kwds, "OOO", (char **)kwlist, &condition, &on_true, &on_false))
        {
            return -1; // Return NULL on failure
        }

        self->condition = Py_NewRef(condition);
        self->on_true = Py_NewRef(on_true);
        self->on_false = Py_NewRef(on_false);
        self->vectorcall = (vectorcallfunc)call;
        return 0;
    }

    static void dealloc(TernaryPredicate *self) {
        PyObject_GC_UnTrack(self);          // Untrack from the GC
        clear(self);
        Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
    }
};

static PyMemberDef members[] = {
    {"condition", T_OBJECT, OFFSET_OF_MEMBER(TernaryPredicate, condition), READONLY, "The predicate that determines which branch to take."},
    {"on_true", T_OBJECT, OFFSET_OF_MEMBER(TernaryPredicate, on_true), READONLY, "Function called when condition is truthy."},
    {"on_false", T_OBJECT, OFFSET_OF_MEMBER(TernaryPredicate, on_false), READONLY, "Function called when condition is falsy."},
    {NULL}  /* Sentinel */
};

PyTypeObject TernaryPredicate_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "ternary_predicate",
    .tp_basicsize = sizeof(TernaryPredicate),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)TernaryPredicate::dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(TernaryPredicate, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "ternary_predicate(condition, on_true, on_false)\n--\n\n"
               "Ternary conditional: condition ? on_true : on_false.\n\n"
               "Evaluates condition(*args); if truthy calls on_true(*args),\n"
               "otherwise calls on_false(*args).\n\n"
               "Args:\n"
               "    condition: Predicate to evaluate.\n"
               "    on_true: Called when condition is truthy.\n"
               "    on_false: Called when condition is falsy.\n\n"
               "Returns:\n"
               "    Result of on_true or on_false depending on condition.",
    .tp_traverse = (traverseproc)TernaryPredicate::traverse,
    .tp_clear = (inquiry)TernaryPredicate::clear,
    // .tp_methods = methods,
    .tp_members = members,
    .tp_init = (initproc)TernaryPredicate::init,
    .tp_new = PyType_GenericNew,
};
