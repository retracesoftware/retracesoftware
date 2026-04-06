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

    struct Collector : PyObject {
        int multiplier;
        PyObject * collect;
        vectorcallfunc vectorcall;

        static PyObject* call(Collector* self, PyObject* const* args, size_t nargsf, PyObject* kwnames) {
            int gen = generation_to_collect(self->multiplier);

            if (gen != -1) {
                PyObject * generation = PyLong_FromLong(gen);
                if (!generation) return nullptr;

                PyObject * result = PyObject_CallOneArg(self->collect, generation);
                Py_DECREF(generation);
                if (!result) return nullptr;
                Py_DECREF(result);
            }

            Py_RETURN_NONE;
        }

        static int init(Collector *self, PyObject *args, PyObject *kwds) {

            int multiplier = 0;
            PyObject * collect = nullptr;

            static const char *kwlist[] = {"multiplier", "collect", NULL};

            if (!PyArg_ParseTupleAndKeywords(
                args,
                kwds,
                "IO",
                const_cast<char**>(kwlist),
                &multiplier,
                &collect)) {
                return -1;
            }

            if (!PyCallable_Check(collect)) {
                PyErr_Format(PyExc_TypeError, "Parameter 'collect' must be callable, but was: %S", collect);
                return -1;
            }

            Py_INCREF(collect);
            self->multiplier = multiplier;
            self->collect = collect;
            self->vectorcall = (vectorcallfunc)call;

            return 0;
        }

        static void dealloc(PyObject *self) {
            Collector * collector = reinterpret_cast<Collector *>(self);
            Py_XDECREF(collector->collect);
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

    PyTypeObject Collector_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "Collector",
        .tp_basicsize = sizeof(Collector),
        .tp_itemsize = 0,
        .tp_dealloc = Collector::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(Collector, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_doc = "TODO",
        .tp_init = (initproc)Collector::init,
        .tp_new = PyType_GenericNew,
    };
}
