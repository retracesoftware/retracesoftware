#include "utils.h"
#include <structmember.h>

namespace retracesoftware {

    struct RunAll : public PyVarObject {
        vectorcallfunc vectorcall;
        FastCall functions[];

        static PyObject * call(RunAll * self, PyObject** args, size_t nargsf, PyObject* kwnames) {
        
            for (int i = 0; i < self->ob_size; i++) {
                PyObject * result = self->functions[i](args, nargsf, kwnames);
                Py_XDECREF(result);
                if (!result) return nullptr;
            }
            Py_RETURN_NONE;
        }

        static PyObject* create(PyTypeObject* type, PyObject* args, PyObject* kwargs) {

            auto* self = reinterpret_cast<RunAll*>(type->tp_alloc(type, PyTuple_Size(args)));
            if (!self) return nullptr;

            self->vectorcall = (vectorcallfunc)call;

            for (size_t i = 0; i < PyTuple_Size(args); i++) {
                self->functions[i] = FastCall(PyTuple_GetItem(args, i));
                Py_INCREF(self->functions[i].callable);
            }
            return (PyObject*)self;
        }

        static void dealloc(RunAll *self) {
            PyObject_GC_UnTrack(self);          // Untrack from the GC
            clear(self);
            Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
        }

        static int clear(RunAll* self) {
            for (int i = 0; i < self->ob_size; i++) {
                Py_CLEAR(self->functions[i].callable);
            }
            return 0;
        }

        // static PyObject * repr(Dispatch *self) {

        //     PyObject * avaliable = ThreadState_AvaliableStates(self->state);

        //     assert (PyTuple_Check(avaliable));

        //     Py_ssize_t n = PyTuple_Size(avaliable);

        //     PyObject * parts = PyList_New(n);

        //     if (!parts) return nullptr;

        //     for (Py_ssize_t i = 0; i < n; i++) {
        //         PyObject * repr = PyObject_Repr(self->handlers[i].callable);

        //         PyObject * part = PyUnicode_FromFormat("%S = %S", PyTuple_GetItem(avaliable, i), repr);

        //         Py_DECREF(repr);

        //         PyList_SET_ITEM(parts, i, part);  // steals reference
        //     }

        //     PyObject *joined = PyUnicode_Join(PyUnicode_FromString(",\n"), parts);
            
        //     Py_DECREF(parts);
            
        //     if (!joined)
        //         return NULL;
            
        //     PyObject *result = PyUnicode_FromFormat("<Dispatch thread_state = %S dispatch = \n%S>",
        //                                     self->state,
        //                                     joined);
                                          
        //     Py_DECREF(joined);

        //     return result;
        // }

        static int traverse(RunAll* self, visitproc visit, void* arg) {
            for (int i = 0; i < self->ob_size; i++) {
                Py_VISIT(self->functions[i].callable);
            }
            return 0;
        }
    };

    PyTypeObject RunAll_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "runall",
        .tp_basicsize = sizeof(RunAll),
        .tp_itemsize = sizeof(FastCall),
        .tp_dealloc = (destructor)RunAll::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(RunAll, vectorcall),
        // .tp_repr = (reprfunc)RunAll::repr,
        .tp_call = PyVectorcall_Call,
        // .tp_str = (reprfunc)RunAll::repr,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL | Py_TPFLAGS_BASETYPE,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)RunAll::traverse,
        .tp_clear = (inquiry)RunAll::clear,
        .tp_new = RunAll::create,
    };
}