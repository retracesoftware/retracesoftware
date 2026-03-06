#include "utils.h"
#include <structmember.h>

namespace retracesoftware {

    struct ThreadSwitchMonitor : public PyObject {
        FastCall on_thread_switch;
        PyThreadState * last_thread_state;
        vectorcallfunc vectorcall;

        static PyObject * call(ThreadSwitchMonitor * self, PyObject* const* args, size_t nargsf, PyObject* kwnames) {
            if (PyThreadState_Get() != self->last_thread_state) {
                self->last_thread_state = PyThreadState_Get();

                PyObject * res = self->on_thread_switch();
                
                Py_XDECREF(res);

                if (!res) return nullptr;
            }
            Py_RETURN_NONE;
        }

        static int traverse(ThreadSwitchMonitor* self, visitproc visit, void* arg) {
            Py_VISIT(self->on_thread_switch.callable);
            return 0;
        }

        static int clear(ThreadSwitchMonitor* self) {
            Py_CLEAR(self->on_thread_switch.callable);
            return 0;
        }

        static void dealloc(ThreadSwitchMonitor *self) {
            PyObject_GC_UnTrack(self);          // Untrack from the GC
            clear(self);
            Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
        }

        static int init(ThreadSwitchMonitor *self, PyObject *args, PyObject *kwds) {

            PyObject * on_thread_switch = NULL;

            static const char *kwlist[] = { "on_thread_switch", NULL};

            if (!PyArg_ParseTupleAndKeywords(args, kwds, "O", (char **)kwlist, &on_thread_switch))
            {
                return -1; // Return NULL on failure
            }

            CHECK_CALLABLE(on_thread_switch);
            
            self->on_thread_switch = FastCall(on_thread_switch);
            Py_NewRef(self->on_thread_switch.callable);
            self->last_thread_state = PyThreadState_Get();
            self->vectorcall = (vectorcallfunc)call;

            return 0;
        }
    };

    // static PyMemberDef members[] = {
    //     // {"elements", T_OBJECT, offsetof(First, elements), READONLY, "TODO"},
    //     {NULL}  /* Sentinel */
    // };

    // static PyObject * str(ThreadAwareProxy *self) {
    //     return PyObject_Str(self->target);
    // }

    // static PyObject * repr(ThreadAwareProxy *self) {
    //     return PyObject_Repr(self->target);
    // }

    PyTypeObject ThreadSwitchMonitor_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "thread_switch_monitor",
        .tp_basicsize = sizeof(ThreadSwitchMonitor),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)ThreadSwitchMonitor::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(ThreadSwitchMonitor, vectorcall),
        // .tp_repr = (reprfunc)repr,
        .tp_call = PyVectorcall_Call,
        // .tp_str = (reprfunc)str,
        .tp_flags = Py_TPFLAGS_DEFAULT | 
                    Py_TPFLAGS_HAVE_GC | 
                    Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)ThreadSwitchMonitor::traverse,
        .tp_clear = (inquiry)ThreadSwitchMonitor::clear,
        // .tp_methods = ThreadSwitchMonitor_methods,
        // .tp_members = members,
        .tp_init = (initproc)ThreadSwitchMonitor::init,
        .tp_new = PyType_GenericNew,
    };
}