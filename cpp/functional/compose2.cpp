#include "functional.h"
#include "object.h"
#include <structmember.h>
#include <signal.h>
#include <functional>

struct Compose2 : public PyObject {
    retracesoftware::FastCall f;
    retracesoftware::FastCall g;
    vectorcallfunc vectorcall;
};

static PyObject * vectorcall(Compose2 * self, PyObject** args, size_t nargsf, PyObject* kwnames) {

    assert (!PyErr_Occurred());

    PyObject * g_res = self->g(args, nargsf, kwnames);

    assert ((g_res && !PyErr_Occurred()) || (!g_res && PyErr_Occurred()));

    if (!g_res) return nullptr;

    PyObject * f_res = self->f(g_res);

    Py_DECREF(g_res);

    assert ((f_res && !PyErr_Occurred()) || (!f_res && PyErr_Occurred()));

    return f_res;
}

static int traverse(Compose2* self, visitproc visit, void* arg) {
    Py_VISIT(self->f.callable);
    Py_VISIT(self->g.callable);
    return 0;
}

static int clear(Compose2* self) {
    Py_CLEAR(self->f.callable);
    Py_CLEAR(self->g.callable);
    return 0;
}

static void dealloc(Compose2 *self) {    
    PyObject_GC_UnTrack(self);          // Untrack from the GC
    clear(self);
    Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
}

static PyObject * repr(Compose2 *self) {
    return PyUnicode_FromFormat(MODULE "Compose(f = %S, g = %S)", self->f.callable, self->g.callable);
}

// static PyMemberDef members[] = {
//     {"functions", T_OBJECT, offsetof(Compose, functions), READONLY, "TODO"},
//     {NULL}  /* Sentinel */
// };

static PyObject * getattro(Compose2 *self, PyObject *name) {
    PyObject * g_res = PyObject_GetAttr(self->g.callable, name);

    if (!g_res) return nullptr;

    PyObject * f_res = self->f(g_res);

    Py_DECREF(g_res);

    return f_res;
}

static int setattro(Compose2 *self, PyObject *name, PyObject * value) {
    return PyObject_SetAttr(self->g.callable, name, value);
}

static int init(Compose2 *self, PyObject *args, PyObject *kwds) {

    PyObject * f;
    PyObject * g;

    static const char *kwlist[] = {"f", "g", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OO", (char **)kwlist, &f, &g))
    {
        return -1; // Return NULL on failure
    }

    if (!PyCallable_Check(f)) {
        PyErr_SetString(PyExc_TypeError, "Parameter f must be callable");
        return -1;
    }
    if (!PyCallable_Check(g)) {
        PyErr_SetString(PyExc_TypeError, "Parameter g must be callable");
        return -1;
    }

    self->f = retracesoftware::FastCall(f);
    self->g = retracesoftware::FastCall(g);
    Py_INCREF(f);
    Py_INCREF(g);

    self->vectorcall = (vectorcallfunc)vectorcall;

    return 0;
}

static PyObject* descr_get(PyObject *self, PyObject *obj, PyObject *type) {
    return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
}

PyTypeObject Compose2_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "compose",
    .tp_basicsize = sizeof(Compose2),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(Compose2, vectorcall),
    .tp_repr = (reprfunc)repr,
    .tp_call = PyVectorcall_Call,
    .tp_str = (reprfunc)repr,
    .tp_getattro = (getattrofunc)getattro,
    .tp_setattro = (setattrofunc)setattro,
    .tp_flags = Py_TPFLAGS_DEFAULT | 
                Py_TPFLAGS_HAVE_GC | 
                Py_TPFLAGS_HAVE_VECTORCALL | 
                Py_TPFLAGS_METHOD_DESCRIPTOR,
    .tp_doc = "compose(f, g)\n--\n\n"
               "Compose two functions: compose(f, g)(x) == f(g(x)).\n\n"
               "An optimized two-function composition using cached vectorcall.\n"
               "Attribute access is also composed: getattr(compose(f, g), 'x') == f(g.x).\n\n"
               "Args:\n"
               "    f: The outer function to apply to g's result.\n"
               "    g: The inner function to call with the arguments.\n\n"
               "Returns:\n"
               "    A callable where calling it applies g then f to the result.\n\n"
               "Example:\n"
               "    >>> c = compose(str.upper, str.strip)\n"
               "    >>> c('  hello  ')\n"
               "    'HELLO'",
    .tp_traverse = (traverseproc)traverse,
    .tp_clear = (inquiry)clear,
    // .tp_methods = methods,
    // .tp_members = members,
    .tp_descr_get = descr_get,
    .tp_init = (initproc)init,
    .tp_new = PyType_GenericNew,
};
