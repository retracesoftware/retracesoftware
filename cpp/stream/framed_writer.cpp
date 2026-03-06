#include "stream.h"
#include "framed_writer.h"
#include <string>

#ifndef _WIN32
    #include <fcntl.h>
    #include <sys/stat.h>
    #include <sys/socket.h>
    #include <sys/un.h>
    #include <limits.h>
#endif

namespace retracesoftware_stream {

    struct PyFramedWriter : PyObject {
        FramedWriter* writer;
        std::string stored_path;
        bool is_fifo;
        bool is_socket;

        static PyObject* tp_new(PyTypeObject* type, PyObject* args, PyObject* kwds) {
            auto* self = (PyFramedWriter*)type->tp_alloc(type, 0);
            if (self) {
                self->writer = nullptr;
                new (&self->stored_path) std::string();
                self->is_fifo = false;
                self->is_socket = false;
            }
            return (PyObject*)self;
        }

        static size_t query_socket_sndbuf(int fd) {
            int sndbuf = 0;
            socklen_t len = sizeof(sndbuf);
            if (getsockopt(fd, SOL_SOCKET, SO_SNDBUF, &sndbuf, &len) == 0 && sndbuf > 0) {
                return (size_t)sndbuf;
            }
            return 65536;
        }

        static int init(PyFramedWriter* self, PyObject* args, PyObject* kwds) {
            const char* path;
            int raw = 0;

            static const char* kwlist[] = {"path", "raw", nullptr};
            if (!PyArg_ParseTupleAndKeywords(args, kwds, "s|p", (char**)kwlist, &path, &raw))
                return -1;

            struct stat st;
            bool path_is_fifo = false;
            bool path_is_socket = false;

            if (::stat(path, &st) == 0) {
                path_is_fifo = S_ISFIFO(st.st_mode);
                path_is_socket = S_ISSOCK(st.st_mode);
            }

            int fd;
            size_t frame_size;

            if (path_is_socket) {
                int sock = ::socket(AF_UNIX, SOCK_DGRAM, 0);
                if (sock < 0) {
                    PyErr_Format(PyExc_IOError,
                        "Could not create socket for: %s: %s", path, strerror(errno));
                    return -1;
                }

                struct sockaddr_un addr;
                memset(&addr, 0, sizeof(addr));
                addr.sun_family = AF_UNIX;
                strncpy(addr.sun_path, path, sizeof(addr.sun_path) - 1);

                if (::connect(sock, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
                    PyErr_Format(PyExc_IOError,
                        "Could not connect to socket: %s: %s", path, strerror(errno));
                    ::close(sock);
                    return -1;
                }

                fd = sock;
                frame_size = query_socket_sndbuf(sock);
            } else {
                int flags = O_WRONLY;
                if (!path_is_fifo) flags |= O_CREAT | O_APPEND;

                fd = ::open(path, flags, 0644);
                if (fd < 0) {
                    PyErr_Format(PyExc_IOError,
                        "Could not open file: %s for writing: %s", path, strerror(errno));
                    return -1;
                }

                frame_size = path_is_fifo ? PIPE_BUF : 65536;
            }

            self->writer = new FramedWriter(fd, frame_size, (bool)raw);
            self->stored_path = path;
            self->is_fifo = path_is_fifo;
            self->is_socket = path_is_socket;

            return 0;
        }

        static void dealloc(PyFramedWriter* self) {
            if (self->writer) {
                self->writer->close();
                delete self->writer;
                self->writer = nullptr;
            }
            self->stored_path.~basic_string();
            Py_TYPE(self)->tp_free((PyObject*)self);
        }

        // --- Python methods ---

        static PyObject* py_write(PyFramedWriter* self, PyObject* arg) {
            Py_buffer buf;
            if (PyObject_GetBuffer(arg, &buf, PyBUF_SIMPLE) < 0) return nullptr;
            self->writer->write_bytes((const uint8_t*)buf.buf, buf.len);
            PyBuffer_Release(&buf);
            Py_RETURN_NONE;
        }

        static PyObject* py_write_uint16(PyFramedWriter* self, PyObject* arg) {
            unsigned long v = PyLong_AsUnsignedLong(arg);
            if (v == (unsigned long)-1 && PyErr_Occurred()) return nullptr;
            if (v > UINT16_MAX) {
                PyErr_SetString(PyExc_OverflowError, "value too large for uint16");
                return nullptr;
            }
            self->writer->write_uint16((uint16_t)v);
            Py_RETURN_NONE;
        }

        static PyObject* py_write_uint32(PyFramedWriter* self, PyObject* arg) {
            unsigned long v = PyLong_AsUnsignedLong(arg);
            if (v == (unsigned long)-1 && PyErr_Occurred()) return nullptr;
            if (v > UINT32_MAX) {
                PyErr_SetString(PyExc_OverflowError, "value too large for uint32");
                return nullptr;
            }
            self->writer->write_uint32((uint32_t)v);
            Py_RETURN_NONE;
        }

        static PyObject* py_write_uint64(PyFramedWriter* self, PyObject* arg) {
            unsigned long long v = PyLong_AsUnsignedLongLong(arg);
            if (v == (unsigned long long)-1 && PyErr_Occurred()) return nullptr;
            self->writer->write_uint64((uint64_t)v);
            Py_RETURN_NONE;
        }

        static PyObject* py_write_int64(PyFramedWriter* self, PyObject* arg) {
            long long v = PyLong_AsLongLong(arg);
            if (v == -1 && PyErr_Occurred()) return nullptr;
            self->writer->write_int64((int64_t)v);
            Py_RETURN_NONE;
        }

        static PyObject* py_write_float64(PyFramedWriter* self, PyObject* arg) {
            double v = PyFloat_AsDouble(arg);
            if (v == -1.0 && PyErr_Occurred()) return nullptr;
            self->writer->write_float64(v);
            Py_RETURN_NONE;
        }

        static PyObject* py_flush(PyFramedWriter* self, PyObject*) {
            self->writer->flush();
            Py_RETURN_NONE;
        }

        static PyObject* py_close(PyFramedWriter* self, PyObject*) {
            if (self->writer) {
                self->writer->close();
            }
            Py_RETURN_NONE;
        }

        static PyObject* py_drain(PyFramedWriter* self, PyObject*) {
            self->writer->flush();
            Py_RETURN_NONE;
        }

        static PyObject* py_resume(PyFramedWriter* self, PyObject*) {
            self->writer->stamp_pid();
            Py_RETURN_NONE;
        }
    };

    static PyObject* PyFramedWriter_path_getter(PyObject* obj, void*) {
        auto* self = (PyFramedWriter*)obj;
        return PyUnicode_FromStringAndSize(
            self->stored_path.c_str(), self->stored_path.size());
    }

    static PyObject* PyFramedWriter_fd_getter(PyObject* obj, void*) {
        auto* self = (PyFramedWriter*)obj;
        return PyLong_FromLong(self->writer ? self->writer->fd() : -1);
    }

    static PyObject* PyFramedWriter_is_fifo_getter(PyObject* obj, void*) {
        return PyBool_FromLong(((PyFramedWriter*)obj)->is_fifo);
    }

    static PyObject* PyFramedWriter_bytes_written_getter(PyObject* obj, void*) {
        auto* self = (PyFramedWriter*)obj;
        return PyLong_FromUnsignedLongLong(
            self->writer ? self->writer->bytes_written() : 0);
    }

    static PyMethodDef PyFramedWriter_methods[] = {
        {"write",        (PyCFunction)PyFramedWriter::py_write,       METH_O,      "Write raw bytes to the buffer"},
        {"write_uint16", (PyCFunction)PyFramedWriter::py_write_uint16, METH_O,     "Write a uint16 (little-endian)"},
        {"write_uint32", (PyCFunction)PyFramedWriter::py_write_uint32, METH_O,     "Write a uint32 (little-endian)"},
        {"write_uint64", (PyCFunction)PyFramedWriter::py_write_uint64, METH_O,     "Write a uint64 (little-endian)"},
        {"write_int64",  (PyCFunction)PyFramedWriter::py_write_int64,  METH_O,     "Write an int64 (little-endian)"},
        {"write_float64",(PyCFunction)PyFramedWriter::py_write_float64,METH_O,     "Write a float64 (IEEE 754)"},
        {"flush",        (PyCFunction)PyFramedWriter::py_flush,        METH_NOARGS, "Flush buffer as PID-framed chunks"},
        {"close",        (PyCFunction)PyFramedWriter::py_close,        METH_NOARGS, "Flush and close the file descriptor"},
        {"drain",        (PyCFunction)PyFramedWriter::py_drain,        METH_NOARGS, "Flush pending data (pre-fork)"},
        {"resume",       (PyCFunction)PyFramedWriter::py_resume,       METH_NOARGS, "Re-stamp PID after fork"},
        {NULL}
    };

    static PyGetSetDef PyFramedWriter_getset[] = {
        {"path",          PyFramedWriter_path_getter,          nullptr, "File path",                       NULL},
        {"fd",            PyFramedWriter_fd_getter,            nullptr, "Underlying fd",                   NULL},
        {"is_fifo",       PyFramedWriter_is_fifo_getter,       nullptr, "True if output is FIFO",          NULL},
        {"bytes_written", PyFramedWriter_bytes_written_getter, nullptr, "Total unframed payload bytes written", NULL},
        {NULL}
    };

    PyTypeObject FramedWriter_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "FramedWriter",
        .tp_basicsize = sizeof(PyFramedWriter),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)PyFramedWriter::dealloc,
        .tp_flags = Py_TPFLAGS_DEFAULT,
        .tp_doc = "PID-framed binary writer with typed primitive methods",
        .tp_methods = PyFramedWriter_methods,
        .tp_getset = PyFramedWriter_getset,
        .tp_init = (initproc)PyFramedWriter::init,
        .tp_new = PyFramedWriter::tp_new,
    };

    FramedWriter* FramedWriter_get(PyObject* obj) {
        if (Py_TYPE(obj) != &FramedWriter_Type) {
            PyErr_SetString(PyExc_TypeError, "expected FramedWriter");
            return nullptr;
        }
        return ((PyFramedWriter*)obj)->writer;
    }
}
