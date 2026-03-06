#include "utils.h"
#include <structmember.h>
#include <vector>

namespace retracesoftware {

    struct StripTraceback : public PyObject {
        PyObject * target;
        PyObject * weakreflist;
        vectorcallfunc vectorcall;
        
        static int traverse(StripTraceback* self, visitproc visit, void* arg) {
            Py_VISIT(self->target);
            return 0;
        }
        
        static int clear(StripTraceback * self) {
            Py_CLEAR(self->target);
            return 0;
        }

        static void dealloc(StripTraceback *self) {
            
            PyObject_GC_UnTrack(self);          // Untrack from the GC
            if (self->weakreflist) {
                PyObject_ClearWeakRefs(self);
            }
            clear(self);
            Py_TYPE(self)->tp_free(self);  // Free the object
        }

        static PyObject * call(StripTraceback * self, PyObject* const * args, size_t nargsf, PyObject* kwnames) {

            PyObject * result = PyObject_Vectorcall(self->target, args, nargsf, kwnames);

            if (!result) {
                PyObject *exc_type, *exc_value, *exc_tb;
                PyErr_Fetch(&exc_type, &exc_value, &exc_tb);
                // PyErr_NormalizeException(&exc_type, &exc_value, &exc_tb);
            
                // printf("strip_straceback, exc_value: %p\n", exc_value);
                // if (exc_value)
                //     printf("strip_straceback, exc_value type: %s\n", Py_TYPE(exc_value)->tp_name);

                assert(!exc_value || PyObject_TypeCheck(exc_value, (PyTypeObject *)PyExc_BaseException));

                if (exc_value && PyObject_TypeCheck(exc_value, (PyTypeObject *)PyExc_BaseException)) {
                    // Py_INCREF(Py_None);
                    if (PyObject_SetAttrString(exc_value, "__traceback__", Py_NewRef(Py_None)) < 0) {
                        fprintf(stderr, "SIGTRAP(striptraceback): failed to set __traceback__\n"); fflush(stderr);
                        raise(SIGTRAP);
                    }
                    if (PyObject_SetAttrString(exc_value, "__context__", Py_NewRef(Py_None)) < 0) {
                        fprintf(stderr, "SIGTRAP(striptraceback): failed to set __context__\n"); fflush(stderr);
                        raise(SIGTRAP);
                    }
                    if (PyObject_SetAttrString(exc_value, "__cause__", Py_NewRef(Py_None)) < 0) {
                        fprintf(stderr, "SIGTRAP(striptraceback): failed to set __cause__\n"); fflush(stderr);
                        raise(SIGTRAP);
                    }
                    
                    assert(Py_REFCNT(exc_tb) == 1);

                    Py_XDECREF(exc_tb);  // no longer needed
                    PyErr_Restore(exc_type, exc_value, NULL);
                }
                else {
                    PyErr_Restore(exc_type, exc_value, exc_tb);
                }
            }
            return result;
        }

        static int init(StripTraceback * self, PyObject * args, PyObject * kwds) {

            PyObject * target;

            static const char *kwlist[] = { "target", nullptr };

            if (!PyArg_ParseTupleAndKeywords(args, kwds, "O", (char **)kwlist, &target))
            {
                return -1; // Return NULL on failure
            }
            self->target = Py_NewRef(target);
            self->vectorcall = (vectorcallfunc)call;
            return 0;
        }

        static PyObject* tp_descr_get(PyObject *self, PyObject *obj, PyObject *type) {
            return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
        }
    };

    PyMemberDef members[] = {
        {"__wrapped__", T_OBJECT, OFFSET_OF_MEMBER(StripTraceback, target), READONLY, "TODO"},
        {NULL}
    };

    PyTypeObject StripTraceback_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "striptraceback",
        .tp_basicsize = sizeof(StripTraceback),
        .tp_dealloc = (destructor)StripTraceback::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(StripTraceback, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | 
                    Py_TPFLAGS_HAVE_GC |
                    Py_TPFLAGS_HAVE_VECTORCALL |
                    Py_TPFLAGS_METHOD_DESCRIPTOR,

        .tp_traverse = (traverseproc)StripTraceback::traverse,
        .tp_clear = (inquiry)StripTraceback::clear,
        // .tp_methods = StubProxy_methods,
        .tp_members = members,
        // .tp_base = &Proxy_Type,
        .tp_descr_get = StripTraceback::tp_descr_get,
        .tp_init = (initproc)StripTraceback::init,
        .tp_new = PyType_GenericNew,
    };
}