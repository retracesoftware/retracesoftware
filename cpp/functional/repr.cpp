#include "functional.h"

PyObject * join(const char * sep, PyObject * elements) {

    PyObject * str_list = PyList_New(PyTuple_Size(elements));

    for (Py_ssize_t i = 0; i < PyTuple_Size(elements); i++) {
        PyObject *item = PyTuple_GetItem(elements, i);  // Borrowed reference
        if (!item) {
            Py_DECREF(str_list);
            return NULL;  // Error retrieving item
        }

        PyObject *item_str = PyObject_Str(item);  // Call str(item)
        if (!item_str) {
            Py_DECREF(str_list);
            return NULL;  // Error in str()
        }
        PyList_SetItem(str_list, i, item_str);  // Store in new list (takes ownership)
    }
    
    PyObject *pysep = PyUnicode_FromString(sep);  // Separator
    PyObject *joined = PyUnicode_Join(pysep, str_list);  // Join list of strings
    Py_DECREF(pysep);
    Py_DECREF(str_list);

    return joined;
}
