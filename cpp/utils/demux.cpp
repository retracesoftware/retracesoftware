#include "utils.h"
#include <exception>
#include <structmember.h>
#include <vector>
#include <condition_variable>
#include <mutex>
#include "unordered_dense.h"

using namespace ankerl::unordered_dense;

namespace retracesoftware {

    class PyGILGuard {
        PyGILState_STATE gstate_;
    public:
        PyGILGuard() {
            gstate_ = PyGILState_Ensure();
        }

        ~PyGILGuard() {
            PyGILState_Release(gstate_);
        }

        // Delete copy constructor and copy assignment
        PyGILGuard(const PyGILGuard&) = delete;
        PyGILGuard& operator=(const PyGILGuard&) = delete;

        // Allow move semantics (optional)
        PyGILGuard(PyGILGuard&& other) noexcept
            : gstate_(other.gstate_) {
            other.gstate_ = PyGILState_UNLOCKED; // or some sentinel
        }

        PyGILGuard& operator=(PyGILGuard&& other) noexcept {
            if (this != &other) {
                // Release current if held
                PyGILState_Release(gstate_);
                gstate_ = other.gstate_;
                other.gstate_ = PyGILState_UNLOCKED;
            }
            return *this;
        }
    };

    class WaitingContext {
        PyThreadState * save;
        PyObject * keys;
        PyObject * key;
        map<PyObject *, PyThreadState *> * pending;

    public:
        WaitingContext(
            PyObject * keys,
            map<PyObject *, PyThreadState *> &pending,
            PyObject * key)
        {
            this->keys = keys;
            this->pending = &pending;
            this->key = key;

            pending[key] = PyThreadState_Get();
            PySet_Add(keys, key);
            save = PyEval_SaveThread();
        }

        ~WaitingContext() {
            PyEval_RestoreThread(save);
            PySet_Discard(keys, key);
            pending->erase(key);
        }

        // Delete copy constructor and copy assignment
        WaitingContext(const WaitingContext&) = delete;
        WaitingContext& operator=(const WaitingContext&) = delete;
    };


    // when get is called, a key is presented
    // we dont need a lookahead
    // in the reader wrap the key into the stream, tuple returned

    struct Demultiplexer : public PyObject {

        PyObject * source;
        PyObject * key_function;
        PyObject * next;
        // PyObject * next_key;
        PyObject * pending_keys;
        map<PyObject *, PyThreadState *> pending;
        vectorcallfunc vectorcall;
        PyObject * on_timeout;
        int timeout_seconds;
        std::mutex mtx;
        std::condition_variable wakeup;
        
        bool test_pending(PyObject * key) {
            PyGILGuard gil;

            if (!ensure_next()) {
                throw std::exception();
            }

            PyObject * next_key = PyObject_CallOneArg(key_function, next);
            
            if (!next_key) {
                throw std::exception();
            }

            bool res;
            if (key == next_key) {
                res = true;
            } else {
                switch (PyObject_RichCompareBool(next_key, key, Py_EQ)) {
                    case 0: res = false; break;
                    case 1: res = true; break;
                    default:
                        Py_DECREF(next_key);
                        throw std::exception();
                }
            }
            Py_DECREF(next_key);
            return res;
        }

        static int init(Demultiplexer * self, PyObject* args, PyObject* kwds) {

            PyObject * source;
            PyObject * key_function;
            // PyObject * initial_key = nullptr;
            PyObject * on_timeout = nullptr;
            int timeout_seconds = 5;

            static const char* kwlist[] = {
                "source", 
                "key_function", 
                // "initial_key", 
                "on_timeout", 
                "timeout_seconds",
                nullptr};  // Keywords allowed

            if (!PyArg_ParseTupleAndKeywords(args, kwds, "OO|OI", (char **)kwlist, 
                &source, &key_function, &on_timeout, &timeout_seconds)) {
                return -1;  
                // Return NULL to propagate the parsing error
            }
            
            self->source = Py_NewRef(source);
            self->key_function = Py_NewRef(key_function);
            self->next = nullptr;
            // self->next_key = Py_XNewRef(initial_key);
            self->pending_keys = PySet_New(0);
            self->on_timeout = Py_XNewRef(on_timeout);
            new (&self->pending) map<PyObject *, PyThreadState *>();
            self->vectorcall = (vectorcallfunc)call;
            self->timeout_seconds = timeout_seconds;

            new (&self->mtx) std::mutex();
            new (&self->wakeup) std::condition_variable();

            return 0;
        }

        bool wait(PyObject * key) {

            auto pred = [this, key]() {
                return test_pending(key);
            };

            assert (PyGILState_Check());
            
            WaitingContext context(pending_keys, pending, key);

            assert(!PyGILState_Check());

            std::unique_lock<std::mutex> lock(mtx);

            return wakeup.wait_for(lock, std::chrono::seconds(timeout_seconds), pred);
        }

        bool ensure_next() {
            
            if (!next) {                
                next = PyObject_CallNoArgs(source);
                if (!next) return false;

                if (!pending.empty()) {
                    wakeup.notify_all();
                }
            }
            return true;
        }

        PyObject * get(PyObject * key) {

            // very first time through
            if (!ensure_next()) return nullptr;

            // fast path
            if (test_pending(key)) {
                PyObject * res = next;
                next = nullptr;
                return res;

            } else if (PySet_Contains(pending_keys, key)) {
                PyErr_Format(PyExc_ValueError, "Key %S already in set of pending gets", key);
                return nullptr;
            } else {
                if (!wait(key)) {
                    
                    if (on_timeout) {
                        return PyObject_CallFunctionObjArgs(on_timeout, this, key, nullptr);
                    } else {
                        PyErr_Format(PyExc_RuntimeError, "Error in demux waiting for: %S", key);
                        return nullptr;
                    }
                }
                PyObject * res = next;
                next = nullptr;
                return res;
            }
        }

        static int traverse(Demultiplexer * self, visitproc visit, void* arg) {
            Py_VISIT(self->on_timeout);
            Py_VISIT(self->key_function);
            Py_VISIT(self->source);
            Py_VISIT(self->next);
            Py_VISIT(self->pending_keys);

            for (auto it = self->pending.begin(); it != self->pending.end(); it++) {
                Py_VISIT(it->first);
            }
            
            return 0;
        }

        static int clear(Demultiplexer * self) {
            Py_CLEAR(self->on_timeout);
            Py_CLEAR(self->key_function);
            Py_CLEAR(self->source);
            Py_CLEAR(self->next);
            Py_CLEAR(self->pending_keys);

            for (auto it = self->pending.begin(); it != self->pending.end(); it++) {
                Py_DECREF(it->first);
            }
            self->pending.clear();
            return 0;
        }

        static void dealloc(Demultiplexer *self) {
            PyObject_GC_UnTrack(self);          // Untrack from the GC
            clear(self);
            Py_TYPE(self)->tp_free(self);  // Free the object
        }

        static PyObject * pending_getter(Demultiplexer *self, void *closure) {
            PyObject * res = PyTuple_New(self->pending.size());
    
            int i = 0;

            for (auto it = self->pending.begin(); it != self->pending.end(); it++) {
                PyTuple_SetItem(res, i++, Py_NewRef(it->first));
            }
            return res;
            // return Py_NewRef(self->per_thread_state());
        }

        static PyObject * call(Demultiplexer * self, PyObject* const * args, size_t nargsf, PyObject* kwnames) {
            if (kwnames || PyVectorcall_NARGS(nargsf) != 1) {
                PyErr_SetString(PyExc_TypeError, "demux take one positional argument, a predicate");
                return nullptr;
            }
            return self->get(args[0]);
        }
    };

    static PyGetSetDef getset[] = {
        {"pending_keys", (getter)Demultiplexer::pending_getter, nullptr, "TODO", NULL},
        {NULL}  // Sentinel
    };

    static PyMemberDef members[] = {
        {"pending", T_OBJECT, OFFSET_OF_MEMBER(Demultiplexer, next), READONLY, "TODO"},
        // {"pending_key", T_OBJECT, OFFSET_OF_MEMBER(Demultiplexer, next_key), READONLY, "TODO"},
        {NULL}  /* Sentinel */
    };

    PyTypeObject Demultiplexer_Type = {

        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "demux",
        .tp_basicsize = sizeof(Demultiplexer),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)Demultiplexer::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(Demultiplexer, vectorcall),
        // .tp_repr = (reprfunc)Gateway::tp_str,
        .tp_call = PyVectorcall_Call,
        // .tp_str = (reprfunc)Gateway::tp_str,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)Demultiplexer::traverse,
        .tp_clear = (inquiry)Demultiplexer::clear,
        // .tp_methods = methods,
        .tp_members = members,
        .tp_getset = getset,

        // .tp_dictoffset = OFFSET_OF_MEMBER(Gateway, dict),
        .tp_init = (initproc)Demultiplexer::init,
        .tp_new = PyType_GenericNew,
    };
}