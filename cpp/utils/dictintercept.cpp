#include "utils.h"
#include <structmember.h>
#include <mutex>
#include <condition_variable>
#include "unordered_dense.h"

using namespace ankerl::unordered_dense;

namespace retracesoftware {

    static map<PyObject *, PyObject *> on_set;
    // static map<PyObject *, PyObject *> on_get;
    
    static int set_item(PyObject * dict, PyObject *key, PyObject *value) {

        if (value) {
            assert(on_set.contains(dict));

            PyObject * setter = on_set[dict];

            PyObject * new_value = PyObject_CallFunctionObjArgs(setter, key, value, nullptr);

            Py_DECREF(value);

            if (!new_value) return -1;

            int result = PyDict_Type.tp_as_mapping->mp_ass_subscript(dict, key, new_value);
            Py_DECREF(new_value);
            return result;
        } else {
            return PyDict_Type.tp_as_mapping->mp_ass_subscript(dict, key, value);
        }
    }

    static PyObject * get_item(PyObject * self, PyObject * key) {
        return PyDict_Type.tp_as_mapping->mp_subscript(self, key);
    }

    static Py_ssize_t len(PyObject * self) {
        return PyDict_Type.tp_as_mapping->mp_length(self);
    }

    static void set_type(PyObject * obj, PyTypeObject * cls) {
        Py_DECREF(obj->ob_type);
        Py_INCREF(cls);
        obj->ob_type = cls;
    }

    static void dealloc(PyObject *self) {
        auto it = on_set.find(self);

        Py_DECREF(it->second);
        on_set.erase(it);

        PyDict_Type.tp_dealloc(self);
    }

    bool intercept_dict_set(PyObject * dict, PyObject * on_set) {

        if (on_set != Py_None && !PyCallable_Check(on_set)) {
            PyErr_Format(PyExc_TypeError, "on_set must be None or callable");
            return false;
        }

        if (Py_TYPE(dict) == &PyDict_Type) {
            if (on_set == Py_None) return true;

            retracesoftware::on_set[dict] = Py_NewRef(on_set);
            set_type(dict, &DictIntercept_Type);
            return true;
        } else if (Py_TYPE(dict) == &DictIntercept_Type) {

            auto it = retracesoftware::on_set.find(dict);
            
            assert (it != retracesoftware::on_set.end());

            Py_DECREF(it->second);

            if (on_set == Py_None) {
                retracesoftware::on_set.erase(it);
                set_type(dict, &PyDict_Type);
            } else {
                it->second = Py_NewRef(on_set);
            }
            return true;

        } else {
            PyErr_Format(PyExc_TypeError, "");
            return false;
        }
    }

    static PyObject * update(PyObject *self, PyObject *args, PyObject *kwds)
    {
        PyObject *other = NULL;

        // Parse arguments: update accepts a single positional argument (optional)
        if (!PyArg_ParseTuple(args, "|O:update", &other)) {
            return NULL;
        }

        // Handle 'other' if provided
        if (other != NULL) {
            // If 'other' is a mapping, use keys() and getitem
            PyObject *keys = PyMapping_Keys(other);
            if (keys == NULL) {
                // If not a mapping, try treating as an iterable
                PyObject *iter = PyObject_GetIter(other);
                if (iter == NULL) {
                    PyErr_SetString(PyExc_TypeError,
                                    "argument must be a mapping or an iterable");
                    return NULL;
                }

                // Iterate over items, expecting (key, value) pairs
                PyObject *item;
                while ((item = PyIter_Next(iter))) {
                    PyObject *key, *value;
                    if (!PyArg_UnpackTuple(item, "item", 2, 2, &key, &value)) {
                        Py_DECREF(item);
                        Py_DECREF(iter);
                        return NULL;
                    }
                    // Call mp_ass_subscript via PyObject_SetItem
                    if (PyObject_SetItem(self, key, value) < 0) {
                        Py_DECREF(item);
                        Py_DECREF(iter);
                        return NULL;
                    }
                    Py_DECREF(item);
                }
                Py_DECREF(iter);
                if (PyErr_Occurred()) {
                    return NULL;
                }
            } else {
                // 'other' is a mapping; iterate over keys
                PyObject *iter = PyObject_GetIter(keys);
                Py_DECREF(keys);
                if (iter == NULL) {
                    return NULL;
                }
                PyObject *key;
                while ((key = PyIter_Next(iter))) {
                    PyObject *value = PyObject_GetItem(other, key);
                    if (value == NULL) {
                        Py_DECREF(key);
                        Py_DECREF(iter);
                        return NULL;
                    }
                    // Call mp_ass_subscript via PyObject_SetItem
                    if (PyObject_SetItem(self, key, value) < 0) {
                        Py_DECREF(key);
                        Py_DECREF(value);
                        Py_DECREF(iter);
                        return NULL;
                    }
                    Py_DECREF(key);
                    Py_DECREF(value);
                }
                Py_DECREF(iter);
                if (PyErr_Occurred()) {
                    return NULL;
                }
            }
        }

        // Handle keyword arguments if provided
        if (kwds != NULL && PyDict_Check(kwds)) {
            PyObject *keys = PyDict_Keys(kwds);
            if (keys == NULL) {
                return NULL;
            }
            PyObject *iter = PyObject_GetIter(keys);
            Py_DECREF(keys);
            if (iter == NULL) {
                return NULL;
            }
            PyObject *key;
            while ((key = PyIter_Next(iter))) {
                PyObject *value = PyDict_GetItem(kwds, key);  // Borrowed reference
                if (value == NULL) {
                    Py_DECREF(key);
                    Py_DECREF(iter);
                    return NULL;
                }
                // Call mp_ass_subscript via PyObject_SetItem
                if (PyObject_SetItem(self, key, value) < 0) {
                    Py_DECREF(key);
                    Py_DECREF(iter);
                    return NULL;
                }
                Py_DECREF(key);
            }
            Py_DECREF(iter);
            if (PyErr_Occurred()) {
                return NULL;
            }
        }

        Py_RETURN_NONE;
    }

    static PyMethodDef methods[] = {
        {"update", (PyCFunction)update, METH_VARARGS | METH_KEYWORDS,
                "Update the dictionary with key-value pairs from another dict or iterable"},
        {NULL,           NULL}              /* sentinel */
    };

    static PyMappingMethods mapping = {
        .mp_length = len,           // len
        .mp_subscript = get_item,  // getitem
        .mp_ass_subscript = set_item  // setitem
    };

    PyTypeObject DictIntercept_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "dictintercept",
        .tp_basicsize = sizeof(PyDictObject),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)dealloc,
        .tp_as_mapping = &mapping,
        .tp_flags = Py_TPFLAGS_DEFAULT,
        .tp_doc = "TODO",
        .tp_methods = methods,
        .tp_base = &PyDict_Type,
    };
}