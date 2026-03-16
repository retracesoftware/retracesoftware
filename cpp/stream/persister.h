#pragma once

#include <Python.h>
#include <unordered_map>

#include "writer.h"

namespace retracesoftware_stream {
    void handle_write_error(bool quit_on_error);
    void handle_debug_error(bool quit_on_error);

    struct AsyncFilePersister : PyObject {
        PyObject* framed_writer_obj;
        FramedWriter* fw;
        MessageStream* stream;
        PyObject* writer_key;
        PyThreadState* last_tstate;
        std::unordered_map<PyThreadState*, PyObject*>* thread_cache;
        bool quit_on_error;

        void clear_thread_cache() {
            if (!thread_cache) return;
            for (auto& kv : *thread_cache) Py_DECREF(kv.second);
            thread_cache->clear();
        }

        static PyObject* tp_new(PyTypeObject* type, PyObject*, PyObject*) {
            AsyncFilePersister* self = (AsyncFilePersister*)type->tp_alloc(type, 0);
            if (!self) return nullptr;
            self->framed_writer_obj = nullptr;
            self->fw = nullptr;
            self->stream = nullptr;
            self->writer_key = nullptr;
            self->last_tstate = nullptr;
            self->thread_cache = nullptr;
            self->quit_on_error = false;
            return (PyObject*)self;
        }

        static int init(AsyncFilePersister* self, PyObject* args, PyObject* kwds) {
            PyObject* writer_obj;
            PyObject* serializer = Py_None;
            PyObject* thread_key = nullptr;
            int quit_on_error = 0;

            static const char* kwlist[] = {"writer", "serializer", "thread", "quit_on_error", nullptr};
            if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|OOp", (char**)kwlist,
                                             &writer_obj, &serializer, &thread_key, &quit_on_error)) {
                return -1;
            }

            FramedWriter* fw_ptr = FramedWriter_get(writer_obj);
            if (!fw_ptr) return -1;

            self->framed_writer_obj = Py_NewRef(writer_obj);
            self->fw = fw_ptr;
            self->writer_key = thread_key && thread_key != Py_None ? Py_NewRef(thread_key) : nullptr;
            self->quit_on_error = quit_on_error;
            self->last_tstate = nullptr;
            self->thread_cache = new std::unordered_map<PyThreadState*, PyObject*>();
            self->stream = new MessageStream(*fw_ptr, serializer, self->quit_on_error);
            return 0;
        }

        static int traverse(AsyncFilePersister* self, visitproc visit, void* arg) {
            Py_VISIT(self->framed_writer_obj);
            Py_VISIT(self->writer_key);
            if (self->stream) self->stream->traverse(visit, arg);
            return 0;
        }

        static int clear(AsyncFilePersister* self) {
            if (self->stream) {
                self->stream->gc_clear();
                delete self->stream;
                self->stream = nullptr;
            }
            self->clear_thread_cache();
            if (self->thread_cache) {
                delete self->thread_cache;
                self->thread_cache = nullptr;
            }
            Py_CLEAR(self->framed_writer_obj);
            Py_CLEAR(self->writer_key);
            self->fw = nullptr;
            self->last_tstate = nullptr;
            return 0;
        }

        static void dealloc(AsyncFilePersister* self) {
            PyObject_GC_UnTrack(self);
            clear(self);
            Py_TYPE(self)->tp_free((PyObject*)self);
        }
    };
}
