#include "utils.h"

namespace retracesoftware {

    struct CollectPred : PyObject {
        int multiplier;
        vectorcallfunc vectorcall;

        static PyObject* call(CollectPred* self, PyObject* const* args, size_t nargsf, PyObject* kwnames) {
            int gen = generation_to_collect(self->multiplier);
            
            return gen == -1 ? Py_NewRef(Py_None) : PyLong_FromLong(gen);
        }

        static int init(CollectPred *self, PyObject *args, PyObject *kwds) {

            int multiplier = 0; 
    
            // A format string to parse one argument:
            // 'I' stands for an unsigned int (C unsigned int)
            const char *format = "I"; 

            // Note: We use static keywords for clearer error messages, though optional here.
            static const char *kwlist[] = {"multiplier", NULL};

            // 2. Parse the arguments
            // PyArg_ParseTupleAndKeywords attempts to extract the arguments from args/kwds
            if (!PyArg_ParseTupleAndKeywords(args, kwds, format, const_cast<char**>(kwlist), &multiplier)) {
                // PyArg_ParseTupleAndKeywords sets the exception (e.g., TypeError) upon failure
                return -1; // Return -1 to signal failure
            }

            self->multiplier = multiplier;
            self->vectorcall = (vectorcallfunc)call;

            return 0;
        }

        static void dealloc(PyObject *self) {
            Py_TYPE(self)->tp_free(self); 
        }
    };

    // ---- type object
    PyTypeObject CollectPred_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "CollectPred",
        .tp_basicsize = sizeof(CollectPred),
        .tp_itemsize = 0,
        .tp_dealloc = CollectPred::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(CollectPred, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_doc = "TODO",
        .tp_init = (initproc)CollectPred::init,
        .tp_new = PyType_GenericNew,
    };
}