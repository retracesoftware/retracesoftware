#include "persister.h"

#include <cerrno>

#ifndef _WIN32
    #include <unistd.h>
    #include <fcntl.h>
    #include <sys/file.h>
    #include <sys/stat.h>
    #include <sys/socket.h>
    #include <sys/un.h>
    #include <limits.h>
#endif

namespace retracesoftware_stream {
    void handle_write_error(bool quit_on_error) {
        if (quit_on_error) {
            fprintf(stderr, "retrace: serialization error (quit_on_error is set)\n");
            PyErr_Print();
            _exit(1);
        }
        PyErr_Clear();
    }

    void handle_debug_error(bool quit_on_error) {
        if (quit_on_error) {
            fprintf(stderr, "retrace: python persister callback error (quit_on_error is set)\n");
            PyErr_Print();
            _exit(1);
        }
        PyErr_Print();
        PyErr_Clear();
    }

    static PyObject* AsyncFilePersister_path_getter(PyObject* obj, void*) {
        AsyncFilePersister* self = (AsyncFilePersister*)obj;
        if (self->framed_writer_obj) return PyObject_GetAttrString(self->framed_writer_obj, "path");
        return PyUnicode_FromString("");
    }

    static PyObject* AsyncFilePersister_fd_getter(PyObject* obj, void*) {
        AsyncFilePersister* self = (AsyncFilePersister*)obj;
        return PyLong_FromLong(self->fw ? self->fw->fd() : -1);
    }

    static PyObject* AsyncFilePersister_is_fifo_getter(PyObject* obj, void*) {
        AsyncFilePersister* self = (AsyncFilePersister*)obj;
        if (self->framed_writer_obj) return PyObject_GetAttrString(self->framed_writer_obj, "is_fifo");
        return PyBool_FromLong(0);
    }

    static PyGetSetDef AsyncFilePersister_getset[] = {
        {"path", AsyncFilePersister_path_getter, nullptr, "File path", NULL},
        {"fd", AsyncFilePersister_fd_getter, nullptr, "Underlying file descriptor", NULL},
        {"is_fifo", AsyncFilePersister_is_fifo_getter, nullptr, "True if the output is a named pipe", NULL},
        {NULL}
    };

    PyTypeObject AsyncFilePersister_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "AsyncFilePersister",
        .tp_basicsize = sizeof(AsyncFilePersister),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)AsyncFilePersister::dealloc,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
        .tp_doc = "Async file persister sink",
        .tp_traverse = (traverseproc)AsyncFilePersister::traverse,
        .tp_clear = (inquiry)AsyncFilePersister::clear,
        .tp_getset = AsyncFilePersister_getset,
        .tp_init = (initproc)AsyncFilePersister::init,
        .tp_new = AsyncFilePersister::tp_new,
    };
}
