#include "stream.h"

#include <Python.h>

namespace retracesoftware_stream {

    PyObject * all_gc_objects() {
        PyObject* gc_module = PyImport_ImportModule("gc");
        if (!gc_module) {
            return nullptr;
        }

        // Get gc.get_objects
        PyObject* get_objects_func = PyObject_GetAttrString(gc_module, "get_objects");
        Py_DECREF(gc_module);
        if (!get_objects_func) {
            return nullptr;
        }

        // Call gc.get_objects()
        PyObject* all_objects = PyObject_CallObject(get_objects_func, nullptr);
        Py_DECREF(get_objects_func);

        return all_objects;
    }

    PyObject * filter_list(std::function<bool(PyObject*)> pred, PyObject * coll)
    {
        PyObject * result = PyList_New(0);

        for (Py_ssize_t i = 0; i < PyList_Size(coll); i++) {
            PyObject * elem = PyList_GetItem(coll, i);

            if (pred(elem)) {
                PyList_Append(result, elem);
            }
        }
        return result;
    }

    PyObject * filter_gc_objects(std::function<bool(PyObject*)> pred) {
        PyObject * all = all_gc_objects();
        if (!all) return nullptr;

        PyObject * filtered = filter_list(pred, all);

        Py_DECREF(all);

        return filtered;
    }
}