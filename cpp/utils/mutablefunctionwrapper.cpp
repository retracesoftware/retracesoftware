#include "utils.h"

namespace retracesoftware {

    struct MutableFunctionWrapper : public Wrapped {
        vectorcallfunc target_vectorcall;
        vectorcallfunc vectorcall;

        static int set_target(MutableFunctionWrapper * self, PyObject * value, const char * name) {
            if (value == nullptr) {
                PyErr_Format(PyExc_AttributeError, "cannot delete %s", name);
                return -1;
            }

            if (!PyCallable_Check(value)) {
                PyErr_Format(PyExc_TypeError, "%s must be callable, got %S", name, value);
                return -1;
            }

            PyObject * next = Py_NewRef(value);
            Py_XDECREF(self->target);
            self->target = next;
            self->target_vectorcall = extract_vectorcall(next);
            return 0;
        }

        static PyObject * call(MutableFunctionWrapper * self, PyObject * const * args, size_t nargsf, PyObject * kwnames) {
            return self->target_vectorcall(self->target, args, nargsf, kwnames);
        }

        static PyObject * py_vectorcall(PyObject * self, PyObject * const * args, size_t nargsf, PyObject * kwnames) {
            return call(reinterpret_cast<MutableFunctionWrapper *>(self), args, nargsf, kwnames);
        }

        static PyObject * create(PyTypeObject * cls, PyObject * args, PyObject * kwargs) {
            static const char * kwlist[] = {"function", nullptr};
            PyObject * function = nullptr;

            if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O", (char **)kwlist, &function)) {
                return nullptr;
            }

            if (!PyCallable_Check(function)) {
                PyErr_Format(PyExc_TypeError, "function must be callable, got %S", function);
                return nullptr;
            }

            MutableFunctionWrapper * self = (MutableFunctionWrapper *)cls->tp_alloc(cls, 0);
            if (!self) {
                return nullptr;
            }

            self->target = Py_NewRef(function);
            self->weakreflist = nullptr;
            self->target_vectorcall = extract_vectorcall(function);
            self->vectorcall = py_vectorcall;
            return (PyObject *)self;
        }

        static PyObject * get(MutableFunctionWrapper * self, PyObject * Py_UNUSED(args)) {
            return Py_NewRef(self->target);
        }

        static PyObject * set(MutableFunctionWrapper * self, PyObject * value) {
            if (set_target(self, value, "function") < 0) {
                return nullptr;
            }
            Py_RETURN_NONE;
        }

        static PyObject * get_function(MutableFunctionWrapper * self, void * closure) {
            return Py_NewRef(self->target);
        }

        static int set_function(MutableFunctionWrapper * self, PyObject * value, void * closure) {
            return set_target(self, value, "function");
        }

        static PyObject * get_wrapped(MutableFunctionWrapper * self, void * closure) {
            return Py_NewRef(self->target);
        }

        static PyObject * repr(MutableFunctionWrapper * self) {
            return PyUnicode_FromFormat("<mutable_function_wrapper %R>", self->target);
        }

        static PyObject * getattro(MutableFunctionWrapper * self, PyObject * name) {
            PyObject * result = PyObject_GenericGetAttr((PyObject *)self, name);
            if (result) {
                return result;
            }

            PyErr_Clear();
            return PyObject_GetAttr(self->target, name);
        }
    };

    static PyMethodDef MutableFunctionWrapper_methods[] = {
        {"get", (PyCFunction)MutableFunctionWrapper::get, METH_NOARGS, "Get the wrapped callable"},
        {"set", (PyCFunction)MutableFunctionWrapper::set, METH_O, "Replace the wrapped callable"},
        {NULL, NULL, 0, NULL}
    };

    static PyGetSetDef MutableFunctionWrapper_getset[] = {
        {"function", (getter)MutableFunctionWrapper::get_function, (setter)MutableFunctionWrapper::set_function, "wrapped callable", NULL},
        {"__wrapped__", (getter)MutableFunctionWrapper::get_wrapped, NULL, "The current wrapped callable", NULL},
        {NULL, NULL, NULL, NULL, NULL}
    };

    PyTypeObject MutableFunctionWrapper_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "mutable_function_wrapper",
        .tp_basicsize = sizeof(MutableFunctionWrapper),
        .tp_itemsize = 0,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(MutableFunctionWrapper, vectorcall),
        .tp_repr = (reprfunc)MutableFunctionWrapper::repr,
        .tp_call = PyVectorcall_Call,
        .tp_getattro = (getattrofunc)MutableFunctionWrapper::getattro,
        .tp_flags = Py_TPFLAGS_DEFAULT |
                    Py_TPFLAGS_HAVE_GC |
                    Py_TPFLAGS_HAVE_VECTORCALL |
                    Py_TPFLAGS_BASETYPE,
        .tp_doc = "Callable wrapper around a mutable contained function.",
        .tp_traverse = Wrapped_Type.tp_traverse,
        .tp_clear = Wrapped_Type.tp_clear,
        .tp_methods = MutableFunctionWrapper_methods,
        .tp_getset = MutableFunctionWrapper_getset,
        .tp_base = &Wrapped_Type,
        .tp_new = (newfunc)MutableFunctionWrapper::create,
    };
}
