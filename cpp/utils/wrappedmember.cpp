#include "utils.h"
#include <structmember.h>

namespace retracesoftware {

    struct WrappedMember : public Wrapped {
        PyObject * handler;

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

            static PyObject * name = nullptr;
            if (!name) name = PyUnicode_InternFromString("__get__");

            PyObject * getter = PyObject_GetAttr(self->target, name);
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
                static PyObject * name = nullptr;
                if (!name) name = PyUnicode_InternFromString("__set__");

                PyObject * setter = PyObject_GetAttr(self->target, name);
                if (!setter) return -1;

                PyObject * result = PyObject_CallFunctionObjArgs(self->handler, setter, instance, value, nullptr);
                Py_DECREF(setter);
                Py_XDECREF(result);
                return result ? 0 : -1;
            } else {
                static PyObject * name = nullptr;
                if (!name) name = PyUnicode_InternFromString("__delete__");

                PyObject * deleter = PyObject_GetAttr(self->target, name);
                if (!deleter) return -1;

                PyObject * result = PyObject_CallFunctionObjArgs(self->handler, deleter, instance, nullptr);
                Py_XDECREF(result);
                return result ? 0 : -1;
            }
        }

        static PyObject * repr(WrappedMember *self) {
            return PyUnicode_FromFormat("<wrapped_member %S>", self->target);
        }
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