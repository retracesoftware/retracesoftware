#include "utils.h"
#include <structmember.h>

namespace retracesoftware {

    struct WrappedMember : public Wrapped {
        PyObject * handler;

        static PyObject * call_descr_get(PyObject * callable_self, PyObject * const * args, Py_ssize_t nargs) {
            if (nargs != 2) {
                PyErr_Format(PyExc_TypeError, "__get__ expected 2 arguments, got %zd", nargs);
                return nullptr;
            }

            WrappedMember * self = reinterpret_cast<WrappedMember *>(callable_self);
            descrgetfunc getter = Py_TYPE(self->target)->tp_descr_get;
            if (!getter) {
                PyErr_SetString(PyExc_AttributeError, "wrapped member target has no __get__");
                return nullptr;
            }

            PyObject * instance = args[0] == Py_None ? nullptr : args[0];
            PyObject * type = args[1] == Py_None ? nullptr : args[1];
            return getter(self->target, instance, type);
        }

        static PyObject * call_descr_set(PyObject * callable_self, PyObject * const * args, Py_ssize_t nargs) {
            if (nargs != 2) {
                PyErr_Format(PyExc_TypeError, "__set__ expected 2 arguments, got %zd", nargs);
                return nullptr;
            }

            WrappedMember * self = reinterpret_cast<WrappedMember *>(callable_self);
            descrsetfunc setter = Py_TYPE(self->target)->tp_descr_set;
            if (!setter) {
                PyErr_SetString(PyExc_AttributeError, "wrapped member target has no __set__");
                return nullptr;
            }

            if (setter(self->target, args[0], args[1]) < 0) {
                return nullptr;
            }
            Py_RETURN_NONE;
        }

        static PyObject * call_descr_delete(PyObject * callable_self, PyObject * const * args, Py_ssize_t nargs) {
            if (nargs != 1) {
                PyErr_Format(PyExc_TypeError, "__delete__ expected 1 argument, got %zd", nargs);
                return nullptr;
            }

            WrappedMember * self = reinterpret_cast<WrappedMember *>(callable_self);
            descrsetfunc deleter = Py_TYPE(self->target)->tp_descr_set;
            if (!deleter) {
                PyErr_SetString(PyExc_AttributeError, "wrapped member target has no __delete__");
                return nullptr;
            }

            if (deleter(self->target, args[0], nullptr) < 0) {
                return nullptr;
            }
            Py_RETURN_NONE;
        }

        static PyMethodDef descr_get_def;
        static PyMethodDef descr_set_def;
        static PyMethodDef descr_delete_def;

        static int traverse(WrappedMember* self, visitproc visit, void* arg) {
            Py_VISIT(self->handler);
            Py_VISIT(self->target);
            return 0;
        }
    
        static int clear(WrappedMember * self) {
            Py_CLEAR(self->handler);
            Py_CLEAR(self->target);
            return 0;
        }

        static int init(WrappedMember * self, PyObject * args, PyObject * kwargs) {

            PyObject * handler;
            PyObject * target;
            static const char *kwlist[] = {"target", "handler", NULL};  // List of keyword

            if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OO", (char **)kwlist, &target, &handler)) {
                return -1;
            }

            self->target = Py_NewRef(target);
            self->handler = Py_NewRef(handler);

            return 0;
        }

        static PyObject* tp_descr_get(WrappedMember * self, PyObject * instance, PyObject * type) {
            PyObject * getter = PyCFunction_NewEx(&descr_get_def, reinterpret_cast<PyObject *>(self), nullptr);
            if (!getter) return nullptr;

            PyObject * result = PyObject_CallFunctionObjArgs(
                self->handler, 
                getter,
                instance ? instance : Py_None, 
                type,
                nullptr);
            Py_DECREF(getter);
            return result;
        }

        static int tp_descr_set(WrappedMember *self, PyObject *instance, PyObject *value) {
            if (value) {
                PyObject * setter = PyCFunction_NewEx(&descr_set_def, reinterpret_cast<PyObject *>(self), nullptr);
                if (!setter) return -1;

                PyObject * result = PyObject_CallFunctionObjArgs(self->handler, setter, instance, value, nullptr);
                Py_DECREF(setter);
                Py_XDECREF(result);
                return result ? 0 : -1;
            } else {
                PyObject * deleter = PyCFunction_NewEx(&descr_delete_def, reinterpret_cast<PyObject *>(self), nullptr);
                if (!deleter) return -1;

                PyObject * result = PyObject_CallFunctionObjArgs(self->handler, deleter, instance, nullptr);
                Py_DECREF(deleter);
                Py_XDECREF(result);
                return result ? 0 : -1;
            }
        }

        static PyObject * repr(WrappedMember *self) {
            return PyUnicode_FromFormat("<wrapped_member %S>", self->target);
        }
    };

    PyMethodDef WrappedMember::descr_get_def = {
        "__get__",
        reinterpret_cast<PyCFunction>(WrappedMember::call_descr_get),
        METH_FASTCALL,
        nullptr,
    };

    PyMethodDef WrappedMember::descr_set_def = {
        "__set__",
        reinterpret_cast<PyCFunction>(WrappedMember::call_descr_set),
        METH_FASTCALL,
        nullptr,
    };

    PyMethodDef WrappedMember::descr_delete_def = {
        "__delete__",
        reinterpret_cast<PyCFunction>(WrappedMember::call_descr_delete),
        METH_FASTCALL,
        nullptr,
    };

    PyTypeObject WrappedMember_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "wrapped_member",
        .tp_basicsize = sizeof(WrappedMember),
        .tp_itemsize = 0,
        // .tp_dealloc = (destructor)MethodDescriptor_dealloc,
        .tp_repr = (reprfunc)WrappedMember::repr,
        // .tp_getattro = (binaryfunc)MethodDescriptor_getattro,
        .tp_str = (reprfunc)WrappedMember::repr,

        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)WrappedMember::traverse,
        .tp_clear = (inquiry)WrappedMember::clear,
        // .tp_members = MethodDescriptor_members,
        .tp_base = &Wrapped_Type,
        .tp_descr_get = (descrgetfunc)WrappedMember::tp_descr_get,
        .tp_descr_set = (descrsetfunc)WrappedMember::tp_descr_set,
        .tp_init = (initproc)WrappedMember::init,
        .tp_new = PyType_GenericNew,
    };

}
