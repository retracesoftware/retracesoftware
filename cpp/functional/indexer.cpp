#include "functional.h"
#include <structmember.h>

struct Indexer : public PyObject {
    
    int index;
    vectorcallfunc vectorcall;

    PyObject * get(PyObject* obj) {

        if (Py_TYPE(obj) == &PyTuple_Type) {
            return Py_XNewRef(PyTuple_GET_ITEM(obj, index));
        }
        if (Py_TYPE(obj) == &PyList_Type) {
            return Py_XNewRef(PyList_GET_ITEM(obj, index));
        }
        if (PyTuple_Check(obj)) {
            return Py_XNewRef(PyTuple_GET_ITEM(obj, index));
        }
        if (PyList_Check(obj)) {
            return Py_XNewRef(PyList_GET_ITEM(obj, index));
        }
        
        PyErr_Format(PyExc_TypeError, "passed object: %S to demux must be tuple or list", obj);
        return nullptr;
    }

    static PyObject * call(Indexer * self, PyObject* const* args, size_t nargsf, PyObject* kwnames) {
        if (kwnames || PyVectorcall_NARGS(nargsf) != 1) {
            PyErr_SetString(PyExc_TypeError, "indexer take one positional argument, a tuple or list");
            return nullptr;
        }

        return self->get(args[0]);
    }

    static int init(Indexer * self, PyObject* args, PyObject* kwds) {

        uint64_t index;

        static const char* kwlist[] = {"index", nullptr};  // Keywords allowed

        if (!PyArg_ParseTupleAndKeywords(args, kwds, "K", (char **)kwlist, &index)) {
            return -1;  
            // Return NULL to propagate the parsing error
        }

        self->index = index;
        self->vectorcall = (vectorcallfunc)call;

        return 0;
    }
};

PyTypeObject Indexer_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "indexed",
    .tp_basicsize = sizeof(Indexer),
    .tp_itemsize = 0,
    // .tp_dealloc = (destructor)Demultiplexer::dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(Indexer, vectorcall),
    // .tp_repr = (reprfunc)Gateway::tp_str,
    .tp_call = PyVectorcall_Call,
    // .tp_str = (reprfunc)Gateway::tp_str,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_doc = "indexed(index)\n--\n\n"
               "Create a callable that extracts element at index from a sequence.\n\n"
               "Works with tuples and lists using fast unchecked access.\n\n"
               "Args:\n"
               "    index: The integer index to extract.\n\n"
               "Returns:\n"
               "    A callable that returns seq[index].\n\n"
               "Example:\n"
               "    >>> get_first = indexed(0)\n"
               "    >>> get_first([1, 2, 3])  # returns 1\n"
               "    >>> get_first(('a', 'b'))  # returns 'a'",
    // .tp_traverse = (traverseproc)Demultiplexer::traverse,
    // .tp_clear = (inquiry)Demultiplexer::clear,
    // .tp_methods = methods,
    // .tp_members = members,
    // .tp_dictoffset = OFFSET_OF_MEMBER(Gateway, dict),
    .tp_init = (initproc)Indexer::init,
    .tp_new = PyType_GenericNew,
};

