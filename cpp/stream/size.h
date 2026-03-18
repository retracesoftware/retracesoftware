#pragma once

#include <Python.h>

namespace retracesoftware_stream {

    inline size_t layout_size_bytes(PyObject *obj)
    {
        PyTypeObject *tp = Py_TYPE(obj);
        size_t n = (size_t)tp->tp_basicsize;
        if (tp->tp_itemsize != 0) {
            Py_ssize_t size = Py_SIZE(obj);
            if (size < 0) size = -size;
            n += (size_t)size * (size_t)tp->tp_itemsize;
        }
        return n;
    }

    inline size_t approximate_unicode_size_bytes(PyObject *obj)
    {
        return Py_TYPE(obj)->tp_basicsize + PyUnicode_GET_LENGTH(obj) * PyUnicode_KIND(obj);
    }

    inline size_t approximate_size_bytes(PyObject *obj)
    {
        if (PyUnicode_CheckExact(obj)) {
            return approximate_unicode_size_bytes(obj);
        }
        return layout_size_bytes(obj);
    }
}
