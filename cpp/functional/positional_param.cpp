#include "functional.h"
#include <structmember.h>

struct PositionalParam : public PyObject {
    int index;
    vectorcallfunc vectorcall;

    static PyObject * call(PositionalParam * self, PyObject * const * args, size_t nargsf, PyObject * kwnames) {
        Py_ssize_t nargs = PyVectorcall_NARGS(nargsf);
        if (nargs > self->index) {
            return Py_NewRef(args[self->index]);
        }
        PyErr_Format(PyExc_IndexError,
            "positional_param(%d): expected at least %d positional args, got %zd",
            self->index, self->index + 1, nargs);
        return nullptr;
    }

    static PyObject * repr(PositionalParam * self) {
        return PyUnicode_FromFormat(MODULE "positional_param(%d)", self->index);
    }

    static int init(PositionalParam * self, PyObject * args, PyObject * kwds) {
        int index;
        static const char * kwlist[] = {"index", NULL};
        if (!PyArg_ParseTupleAndKeywords(args, kwds, "i", (char **)kwlist, &index)) {
            return -1;
        }
        if (index < 0) {
            PyErr_SetString(PyExc_ValueError, "positional_param index must be >= 0");
            return -1;
        }
        self->index = index;
        self->vectorcall = (vectorcallfunc)call;
        return 0;
    }
};

static PyMemberDef positional_param_members[] = {
    {"index", T_INT, OFFSET_OF_MEMBER(PositionalParam, index), READONLY, "Positional index."},
    {NULL}
};

PyTypeObject PositionalParam_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "positional_param",
    .tp_basicsize = sizeof(PositionalParam),
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(PositionalParam, vectorcall),
    .tp_repr = (reprfunc)PositionalParam::repr,
    .tp_call = PyVectorcall_Call,
    .tp_str = (reprfunc)PositionalParam::repr,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "positional_param(index) -> callable\n\n"
              "Extract a positional argument by index. Ignores kwargs.\n"
              "Raises IndexError if fewer positional args than needed.",
    .tp_members = positional_param_members,
    .tp_init = (initproc)PositionalParam::init,
    .tp_new = PyType_GenericNew,
};
