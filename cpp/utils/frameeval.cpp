#include "utils.h"
#include <structmember.h>
#include <thread>

typedef struct _PyInterpreterFrame {
    /* "Specials" section */
    PyFunctionObject *f_func; /* Strong reference */
    PyObject *f_globals; /* Borrowed reference */
    PyObject *f_builtins; /* Borrowed reference */
    PyObject *f_locals; /* Strong reference, may be NULL */
    PyCodeObject *f_code; /* Strong reference */
    PyFrameObject *frame_obj; /* Strong reference, may be NULL */
    /* Linkage section */
    struct _PyInterpreterFrame *previous;
    // NOTE: This is not necessarily the last instruction started in the given
    // frame. Rather, it is the code unit *prior to* the *next* instruction. For
    // example, it may be an inline CACHE entry, an instruction we just jumped
    // over, or (in the case of a newly-created frame) a totally invalid value:
    _Py_CODEUNIT *prev_instr;
    int stacktop;     /* Offset of TOS from localsplus  */
    bool is_entry;  // Whether this is the "root" frame for the current _PyCFrame.
    char owner;
    /* Locals and stack */
    PyObject *localsplus[1];
} _PyInterpreterFrame;

namespace retracesoftware {

    static PyObject * key() {
        static PyObject * name = nullptr;
        if (!name) name = PyUnicode_InternFromString("__retrace__");
        return name;
    }
    
    struct FrameWrapper : public PyObject {
        _PyInterpreterFrame * frame;
        
        static void dealloc(FrameWrapper *self) {
            // PyObject_GC_UnTrack(self);          // Untrack from the GC
            // clear(self);
            Py_TYPE(self)->tp_free(self);  // Free the object
        }

        static PyObject * function_getter(FrameWrapper *self, void *closure) {
            if (!self->frame) {
                PyErr_Format(PyExc_RuntimeError, "Cannot access frame after frame exit");
                return nullptr;
            }

            return Py_NewRef((PyObject *)self->frame->f_func);
        }

        static PyObject * locals_getter(FrameWrapper *self, void *closure) {

            if (!self->frame) {
                PyErr_Format(PyExc_RuntimeError, "Cannot access frame after frame exit");
                return nullptr;
            }

            PyObject * locals = PyDict_New();

            PyObject * names = self->frame->f_code->co_localsplusnames;

            for (Py_ssize_t i = 0; i < PyTuple_Size(names); i++) {
                PyObject * local = self->frame->localsplus[i];
                PyDict_SetItem(locals, PyTuple_GetItem(names, i), local ? local : Py_None);
            }
            return locals;
        }

        static PyObject * globals_getter(FrameWrapper *self, void *closure) {
            if (!self->frame) {
                PyErr_Format(PyExc_RuntimeError, "Cannot access frame after frame exit");
                return nullptr;
            }
            return Py_NewRef(self->frame->f_globals ? self->frame->f_globals : Py_None);
        }

        static PyMemberDef members[];
        static PyGetSetDef getset[];
    };

    PyGetSetDef FrameWrapper::getset[] = {
        {"function", (getter)FrameWrapper::function_getter, nullptr, "TODO", NULL},
        {"locals", (getter)FrameWrapper::locals_getter, nullptr, "TODO", NULL},
        {"globals", (getter)FrameWrapper::globals_getter, nullptr, "TODO", NULL},
        {NULL}
    };

    PyMemberDef FrameWrapper::members[] = {
        // {"pending", T_OBJECT, OFFSET_OF_MEMBER(Demultiplexer, next), READONLY, "TODO"}
        {NULL}
    };

    struct FrameEval : public PyObject {
        PyObject * callback;
        _PyFrameEvalFunction real_eval;

        PyObject* do_call(PyThreadState *tstate, struct _PyInterpreterFrame * frame, int throw_flag) {

            static thread_local bool in_callback = false;

            if (tstate->tracing || throw_flag || in_callback) {
                return real_eval(tstate, frame, throw_flag);
            }
            
            FrameWrapper * frame_wrapper = (FrameWrapper *)FrameWrapper_Type.tp_alloc(&FrameWrapper_Type, 0);

            if (!frame_wrapper) return nullptr;
            frame_wrapper->frame = frame;

            in_callback = true;
            
            Py_tracefunc trace_func = tstate->c_tracefunc;
            Py_tracefunc profile_func = tstate->c_profilefunc;
            tstate->c_tracefunc = tstate->c_profilefunc = nullptr;

            PyObject * on_return = PyObject_CallOneArg(callback, frame_wrapper);
            
            tstate->c_tracefunc = trace_func;
            tstate->c_profilefunc = profile_func;

            in_callback = false;

            if (!on_return) {
                return nullptr;
            }

            if (on_return != Py_None && !PyCallable_Check(on_return) && !PyTuple_Check(on_return)) {
                PyErr_Format(PyExc_TypeError, "callback: %S returned invalid response: %S, must be tuple of on_result,on_error or callable or None", callback, on_return);
                Py_DECREF(on_return);
                frame_wrapper->frame = nullptr;
                Py_DECREF(frame_wrapper);
                return nullptr;
            }
            if (PyTuple_Check(on_return)) {
                if (PyTuple_GET_SIZE(on_return) != 2) {
                    PyErr_Format(PyExc_TypeError, "Callback %S returned tuple: %S which doesn't have two elements on_result, on_error", callback, on_return);
                    Py_DECREF(on_return);
                    frame_wrapper->frame = nullptr;
                    Py_DECREF(frame_wrapper);
                    return nullptr;
                }

                if (!PyCallable_Check(PyTuple_GET_ITEM(on_return, 0))) {
                    PyErr_Format(PyExc_TypeError, "one of on_result callback: %S is not callable", PyTuple_GET_ITEM(on_return, 0));
                    Py_DECREF(on_return);
                    frame_wrapper->frame = nullptr;
                    Py_DECREF(frame_wrapper);
                    return nullptr;
                }
                if (!PyCallable_Check(PyTuple_GET_ITEM(on_return, 1))) {
                    PyErr_Format(PyExc_TypeError, "one of on_error callback: %S is not callable", PyTuple_GET_ITEM(on_return, 1));
                    Py_DECREF(on_return);
                    frame_wrapper->frame = nullptr;
                    Py_DECREF(frame_wrapper);
                    return nullptr;
                }
            }


            PyObject * result = real_eval(tstate, frame, throw_flag);

            if (on_return != Py_None) {
                in_callback = true;
                tstate->c_tracefunc = tstate->c_profilefunc = nullptr;
                
                if (result) {

                    PyObject * res = PyTuple_Check(on_return) 
                        ? PyObject_CallOneArg(PyTuple_GET_ITEM(on_return, 0), result)
                        : PyObject_CallNoArgs(on_return);

                    if (res) {
                        Py_DECREF(res);
                    } else {
                        Py_DECREF(result);
                        result = nullptr;
                    }
                } else {
                    PyObject * exc[] = {nullptr, nullptr, nullptr};
        
                    // Fetch the current exception
                    PyErr_Fetch(exc + 0, exc + 1, exc + 2);

                    PyObject * res = PyTuple_Check(on_return) 
                        ? PyObject_CallFunctionObjArgs(PyTuple_GET_ITEM(on_return, 1), exc[0], exc[1], exc[2], nullptr)
                        : PyObject_CallNoArgs(on_return);
                    
                    if (res) {
                        Py_DECREF(res);
                        PyErr_Restore(exc[0], exc[1], exc[2]);
                    } else {
                        Py_XDECREF(exc[0]);
                        Py_XDECREF(exc[1]);
                        Py_XDECREF(exc[2]);
                    }
                }
                tstate->c_tracefunc = trace_func;
                tstate->c_profilefunc = profile_func;

                in_callback = false;
            }
            Py_DECREF(on_return);
            
            frame_wrapper->frame = nullptr;
            Py_DECREF(frame_wrapper);
            return result;
        }

        static PyObject* wrapper(PyThreadState *tstate, struct _PyInterpreterFrame * frame, int throw_flag) {
            
            PyObject * interp_dict = PyInterpreterState_GetDict(PyThreadState_GetInterpreter(tstate));

            FrameEval * frame_eval = (FrameEval *)PyDict_GetItem(interp_dict, key());
            
            assert(frame_eval);

            // assert (!PyErr_Occurred());

            Py_INCREF(frame_eval);
            PyObject * result = frame_eval->do_call(tstate, frame, throw_flag);
            Py_DECREF(frame_eval);
            return result;
        }

        static int traverse(FrameEval* self, visitproc visit, void* arg) {
            Py_VISIT(self->callback);
            return 0;
        }
    
        static int clear(FrameEval * self) {
            Py_CLEAR(self->callback);
            return 0;
        }

        static void dealloc(FrameEval *self) {
            PyObject_GC_UnTrack(self);
            clear(self);
            Py_TYPE(self)->tp_free(self);
        }
    };
    
    PyTypeObject FrameWrapper_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "FrameWrapper",
        .tp_basicsize = sizeof(FrameWrapper),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)FrameWrapper::dealloc,
        // .tp_vectorcall_offset = OFFSET_OF_MEMBER(CurrentFrame, vectorcall),
        // .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT,
        .tp_doc = "TODO",
        .tp_members = FrameWrapper::members,
        .tp_getset = FrameWrapper::getset,
    };

    PyTypeObject FrameEval_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "FrameEval",
        .tp_basicsize = sizeof(FrameEval),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)FrameEval::dealloc,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)FrameEval::traverse,
        .tp_clear = (inquiry)FrameEval::clear,
    };

    bool FrameEval_Install(PyInterpreterState * is, PyObject * handler) {

        PyObject * dict = PyInterpreterState_GetDict(is);

        if (handler == Py_None) {

            FrameEval * eval = (FrameEval *)PyDict_GetItem(dict, key());

            if (eval) {
                _PyInterpreterState_SetEvalFrameFunc(is, eval->real_eval);
                PyDict_DelItem(dict, key());
            }

        } else if (!PyCallable_Check(handler)) {
            PyErr_Format(PyExc_TypeError, "handler: %S must be None or callable", handler);
            return false;

        } else {
            FrameEval * eval = (FrameEval *)PyDict_GetItem(dict, key());

            if (eval) {
                Py_DECREF(eval->callback);
                eval->callback = Py_NewRef(handler);
            } else {                
                FrameEval * eval = (FrameEval *)FrameEval_Type.tp_alloc(&FrameEval_Type, 0);

                eval->real_eval = _PyInterpreterState_GetEvalFrameFunc(is);
                eval->callback = Py_NewRef(handler);

                PyDict_SetItem(dict, key(), eval);
                Py_DECREF(eval);

                _PyInterpreterState_SetEvalFrameFunc(is, FrameEval::wrapper);
            }
        }
        return true;
    }
}