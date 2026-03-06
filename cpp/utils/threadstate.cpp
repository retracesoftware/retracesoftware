#include "utils.h"
#include <structmember.h>

namespace retracesoftware {

    static PyObject* tp_descr_get(PyObject *self, PyObject *obj, PyObject *type) {
        return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
    }

    static void ThreadState_SetIndex(PyObject * state, int index);
    static int ThreadState_GetIndex(PyObject * state);
    static PyObject * ThreadState_AvaliableStates(PyObject * state);
    static PyObject * ThreadState_ValueForIndex(PyObject * state, int index);

    static PyObject * ThreadStateWrapped_New(
        PyObject * thread_state, 
        PyObject * function,
        int desired_index,
        bool sticky);

    struct Dispatch : public PyVarObject {
        PyObject * state;
        vectorcallfunc vectorcall;
        FastCall handlers[];

        // PyObject ** handlers() {
        //     return (PyObject **)(this + 1);
        // }

        PyObject * handler() {
            return (handlers + ThreadState_GetIndex(state))->callable;
        }

        static int setattro(Dispatch *self, PyObject *name, PyObject * value) {
            PyObject * handler = self->handler();
            return handler ? PyObject_SetAttr(handler, name, value) : 0;
        }

        static PyObject * getattro(Dispatch *self, PyObject *name) {
            PyObject * handler = self->handler();
            return handler ? PyObject_GetAttr(handler, name) : Py_NewRef(Py_None);
        }

        static PyObject * call(Dispatch * self, PyObject** args, size_t nargsf, PyObject* kwnames) {
        
            FastCall * fc = self->handlers + ThreadState_GetIndex(self->state);

            return fc->callable ? fc->operator()(args, nargsf, kwnames) : Py_NewRef(Py_None);
        }

        static void dealloc(Dispatch *self) {
            PyObject_GC_UnTrack(self);          // Untrack from the GC
            clear(self);
            Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
        }

        static int clear(Dispatch* self) {
            Py_CLEAR(self->state);
            for (int i = 0; i < self->ob_size; i++) {
                Py_CLEAR(self->handlers[i].callable);
            }
            return 0;
        }

        PyObject * table() {
            PyObject * avaliable = ThreadState_AvaliableStates(state);

            assert (PyTuple_Check(avaliable));

            Py_ssize_t n = PyTuple_Size(avaliable);

            PyObject * res = PyDict_New();

            for (Py_ssize_t i = 0; i < n; i++) {
                PyObject * name = PyTuple_GetItem(avaliable, i);

                PyDict_SetItem(res, name, handlers[i].callable);
            }
            return res;
        }

        static PyObject * dispatch_table(PyObject * cls, PyObject * obj) {
            if (!PyObject_TypeCheck(obj, &Dispatch_Type)) {
                PyErr_Format(PyExc_TypeError, "parameter %S not of Dispatch type", obj);
                return nullptr;
            }
            Dispatch * dispatch = reinterpret_cast<Dispatch *>(obj);
            return dispatch->table();
        }

        static PyObject * repr(Dispatch *self) {

            PyObject * avaliable = ThreadState_AvaliableStates(self->state);

            assert (PyTuple_Check(avaliable));

            Py_ssize_t n = PyTuple_Size(avaliable);

            PyObject * parts = PyList_New(n);

            if (!parts) return nullptr;

            for (Py_ssize_t i = 0; i < n; i++) {
                PyObject * repr = PyObject_Repr(self->handlers[i].callable);

                PyObject * part = PyUnicode_FromFormat("%S = %S", PyTuple_GetItem(avaliable, i), repr);

                Py_DECREF(repr);

                PyList_SET_ITEM(parts, i, part);  // steals reference
            }

            PyObject *joined = PyUnicode_Join(PyUnicode_FromString(",\n"), parts);
            
            Py_DECREF(parts);
            
            if (!joined)
                return NULL;
            
            PyObject *result = PyUnicode_FromFormat("<Dispatch thread_state = %S dispatch = \n%S>",
                                            self->state,
                                            joined);
                                          
            Py_DECREF(joined);

            return result;
        }

        static int traverse(Dispatch* self, visitproc visit, void* arg) {
            Py_VISIT(self->state);
            for (int i = 0; i < self->ob_size; i++) {
                Py_VISIT(self->handlers[i].callable);
            }
            return 0;
        }
    };

    static PyMethodDef Dispatch_methods[] = {
        {"table", (PyCFunction)Dispatch::dispatch_table, METH_O | METH_STATIC, "TODO"},
        // {"set", (PyCFunction)Dispatch::set_classmethod, METH_VARARGS | METH_KEYWORDS | METH_CLASS, "Set the thread-local target"},
        // {"get", (PyCFunction)ThreadLocalProxy::get_classmethod, METH_VARARGS | METH_KEYWORDS | METH_CLASS, "Set the thread-local target"},
        {NULL, NULL, 0, NULL}
    };

    PyTypeObject Dispatch_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "dispatch",
        .tp_basicsize = sizeof(Dispatch),
        .tp_itemsize = sizeof(FastCall),
        .tp_dealloc = (destructor)Dispatch::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(Dispatch, vectorcall),
        .tp_repr = (reprfunc)Dispatch::repr,
        .tp_call = PyVectorcall_Call,
        .tp_str = (reprfunc)Dispatch::repr,
        .tp_getattro = (getattrofunc)Dispatch::getattro,
        .tp_setattro = (setattrofunc)Dispatch::setattro,
        .tp_flags = Py_TPFLAGS_DEFAULT | 
                    Py_TPFLAGS_HAVE_GC | 
                    Py_TPFLAGS_HAVE_VECTORCALL |
                    Py_TPFLAGS_DISALLOW_INSTANTIATION |
                    Py_TPFLAGS_BASETYPE,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)Dispatch::traverse,
        .tp_clear = (inquiry)Dispatch::clear,
        .tp_methods = Dispatch_methods,
    };

    PyTypeObject MethodDispatch_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "method_dispatch",
        .tp_basicsize = Dispatch_Type.tp_basicsize,
        .tp_itemsize = 0,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(Dispatch, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | 
                    Py_TPFLAGS_HAVE_GC | 
                    Py_TPFLAGS_HAVE_VECTORCALL |
                    Py_TPFLAGS_METHOD_DESCRIPTOR |
                    Py_TPFLAGS_DISALLOW_INSTANTIATION,
        .tp_traverse = Dispatch_Type.tp_traverse,
        .tp_clear = Dispatch_Type.tp_clear,
        // .tp_methods = Dispatch_methods,
        .tp_base = &Dispatch_Type,
        .tp_descr_get = tp_descr_get,
    };

    struct ThreadStateWrapped : public PyObject {
        PyObject * thread_state;
        int desired_index;
        PyObject * function;
        bool sticky;
        vectorcallfunc vectorcall;

        static void dealloc(ThreadStateWrapped *self) {
            PyObject_GC_UnTrack(self);          // Untrack from the GC
            clear(self);
            Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
        }

        static int setattro(ThreadStateWrapped *self, PyObject *name, PyObject * value) {
            int previous = ThreadState_GetIndex(self->thread_state);

            ThreadState_SetIndex(self->thread_state, self->desired_index);
            int result = PyObject_SetAttr(self->function, name, value);
            ThreadState_SetIndex(self->thread_state, previous);

            return result;
        }

        static PyObject * repr(ThreadStateWrapped *self) {
            return PyUnicode_FromFormat("<ThreadStateWrapped thread_state = %S desired_state = %S, function = %S>", 
                self->thread_state, 
                ThreadState_ValueForIndex(self->thread_state, self->desired_index),
                self->function);
        }

        static PyObject * str(ThreadStateWrapped *self) {
            return PyUnicode_FromFormat("<ThreadStateWrapped thread_state = %S desired_state = %S, function = %S>", 
                self->thread_state, 
                ThreadState_ValueForIndex(self->thread_state, self->desired_index),
                self->function);
        }

        PyObject * wrap_result(PyObject * result) {
            if (sticky && result && PyCallable_Check(result)) {
                PyObject * wrapped = ThreadStateWrapped_New(thread_state, result, desired_index, sticky);
                Py_DECREF(result);
                return wrapped;
            } else {
                return result;
            }
        }

        static PyObject * getattro(ThreadStateWrapped *self, PyObject *name) {
            int previous = ThreadState_GetIndex(self->thread_state);

            ThreadState_SetIndex(self->thread_state, self->desired_index);
            PyObject * result = PyObject_GetAttr(self->function, name);
            ThreadState_SetIndex(self->thread_state, previous);
            return self->wrap_result(result);
        }

        static int clear(ThreadStateWrapped* self) {
            Py_CLEAR(self->thread_state);
            Py_CLEAR(self->function);
            return 0;
        }
        
        static int traverse(ThreadStateWrapped* self, visitproc visit, void* arg) {
            Py_VISIT(self->thread_state);
            Py_VISIT(self->function);
            return 0;
        }

        static PyObject * call(ThreadStateWrapped * self, PyObject** args, size_t nargsf, PyObject* kwnames) {
            assert(!PyErr_Occurred());
            Py_INCREF(self);

            int previous = ThreadState_GetIndex(self->thread_state);

            PyObject * result;
            if (previous == self->desired_index) {
                result = PyObject_Vectorcall(self->function, args, nargsf, kwnames);
            } else {
                ThreadState_SetIndex(self->thread_state, self->desired_index);

                result = PyObject_Vectorcall(self->function, args, nargsf, kwnames);
                
                ThreadState_SetIndex(self->thread_state, previous);            
                result = self->wrap_result(result);
            }
            Py_DECREF(self);
            return result;
        }
    };

    PyTypeObject ThreadStateWrapped_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "ThreadStateWrapped",
        .tp_basicsize = sizeof(ThreadStateWrapped),
        .tp_dealloc = (destructor)ThreadStateWrapped::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(ThreadStateWrapped, vectorcall),
        .tp_repr = (reprfunc)ThreadStateWrapped::repr,
        .tp_call = PyVectorcall_Call,
        .tp_str = (reprfunc)ThreadStateWrapped::repr,
        .tp_getattro = (getattrofunc)ThreadStateWrapped::getattro,
        .tp_setattro = (setattrofunc)ThreadStateWrapped::setattro,
        .tp_flags = Py_TPFLAGS_DEFAULT |
                    Py_TPFLAGS_HAVE_GC |
                    Py_TPFLAGS_HAVE_VECTORCALL |
                    Py_TPFLAGS_METHOD_DESCRIPTOR |
                    Py_TPFLAGS_DISALLOW_INSTANTIATION,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)ThreadStateWrapped::traverse,
        .tp_clear = (inquiry)ThreadStateWrapped::clear,
        .tp_descr_get = tp_descr_get,
    };

    static PyObject * ThreadStateWrapped_New(
        PyObject * thread_state, 
        PyObject * function,
        int desired_index,
        bool sticky) {

        if (!PyCallable_Check(function)) {
            PyObject * name = ThreadState_ValueForIndex(thread_state, desired_index);
            PyErr_Format(PyExc_TypeError, "Cannot wrap %S with state: \"%S\" as %S is not callable", function, name, function);
            return nullptr;
        }

        ThreadStateWrapped * self = (ThreadStateWrapped *)ThreadStateWrapped_Type.tp_alloc(&ThreadStateWrapped_Type, 0);

        if (self) {
            self->thread_state = Py_NewRef(thread_state);
            self->desired_index = desired_index;
            self->function = Py_NewRef(function);
            self->sticky = sticky;
            self->vectorcall = (vectorcallfunc)ThreadStateWrapped::call;
        }
        return self;
    }

    struct ThreadStateContext : public PyObject {
        PyObject * thread_state;
        int desired_state;
        int previous_state;


        static PyObject* enter(ThreadStateContext* self, PyObject* Py_UNUSED(args)) {

            self->previous_state = ThreadState_GetIndex(self->thread_state);
            if (self->previous_state != self->desired_state) {
                ThreadState_SetIndex(self->thread_state, self->desired_state);
            }
            return Py_NewRef(self);
        }

        static int clear(ThreadStateContext* self) {
            Py_CLEAR(self->thread_state);
            return 0;
        }
        
        static int traverse(ThreadStateContext* self, visitproc visit, void* arg) {
            Py_VISIT(self->thread_state);
            return 0;
        }

        static void dealloc(ThreadStateContext *self) {
            PyObject_GC_UnTrack(self);          // Untrack from the GC
            clear(self);
            Py_TYPE(self)->tp_free(self);
        }

        static PyObject * repr(ThreadStateContext *self) {
            return PyUnicode_FromFormat("<ThreadStateContext thread_state = %S context_state = %S>", 
                self->thread_state, 
                ThreadState_ValueForIndex(self->thread_state, self->desired_state));
        }

        static PyObject * str(ThreadStateContext *self) {
            return PyUnicode_FromFormat("<ThreadStateContext thread_state = %S context_state = %S>", 
                self->thread_state,
                ThreadState_ValueForIndex(self->thread_state, self->desired_state));
        }

        static PyObject *exit(ThreadStateContext* self, PyObject* args) {

            if (self->previous_state != self->desired_state)
                ThreadState_SetIndex(self->thread_state, self->previous_state);

            PyObject *exc_type, *exc_value, *traceback;

            if (!PyArg_UnpackTuple(args, "__exit__", 3, 3,
                                &exc_type, &exc_value, &traceback)) {
                return NULL;
            }
            Py_RETURN_FALSE;  // Do not suppress exceptions
        }
    };

    static PyMethodDef ThreadStateContext_methods[] = {
        {"__enter__", (PyCFunction)ThreadStateContext::enter, METH_NOARGS, ""},
        {"__exit__",  (PyCFunction)ThreadStateContext::exit,  METH_VARARGS, ""},
        {NULL, NULL, 0, NULL}
    };

    PyTypeObject ThreadStateContext_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "ThreadStateContext",
        .tp_basicsize = sizeof(ThreadStateContext),
        .tp_dealloc = (destructor)ThreadStateContext::dealloc,
        .tp_repr = (reprfunc)ThreadStateContext::repr,
        .tp_str = (reprfunc)ThreadStateContext::str,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_DISALLOW_INSTANTIATION,
        .tp_traverse = (traverseproc)ThreadStateContext::traverse,
        .tp_clear = (inquiry)ThreadStateContext::clear,
        .tp_methods = ThreadStateContext_methods,
    };

    static PyObject * ThreadStateContext_New(PyObject * thread_state, int desired_state) {
        ThreadStateContext *self = (ThreadStateContext *)ThreadStateContext_Type.tp_alloc(&ThreadStateContext_Type, 0);

        self->thread_state = Py_NewRef(thread_state);
        self->desired_state = desired_state;
        return self;
    }

    struct ThreadStatePredicate : public PyObject {
        PyObject * thread_state;
        int test_index;
        vectorcallfunc vectorcall;

        static int clear(ThreadStatePredicate* self) {
            Py_CLEAR(self->thread_state);
            return 0;
        }
        
        static int traverse(ThreadStatePredicate* self, visitproc visit, void* arg) {
            Py_VISIT(self->thread_state);
            return 0;
        }

        static void dealloc(ThreadStatePredicate *self) {
            PyObject_GC_UnTrack(self);          // Untrack from the GC
            clear(self);
            Py_TYPE(self)->tp_free(self);
        }

        static PyObject * call(ThreadStatePredicate * self, PyObject** args, size_t nargsf, PyObject* kwnames) {
            return PyBool_FromLong(ThreadState_GetIndex(self->thread_state) == self->test_index);
        }
    };

    PyTypeObject ThreadStatePredicate_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "ThreadStatePredicate",
        .tp_basicsize = sizeof(ThreadStatePredicate),
        .tp_dealloc = (destructor)ThreadStatePredicate::dealloc,
        .tp_vectorcall_offset = OFFSET_OF_MEMBER(ThreadStatePredicate, vectorcall),
        .tp_call = PyVectorcall_Call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
        .tp_traverse = (traverseproc)ThreadStatePredicate::traverse,
        .tp_clear = (inquiry)ThreadStatePredicate::clear,
    };

    static PyObject * ThreadStatePredicate_New(PyObject * thread_state, int test_index) {
        ThreadStatePredicate *self = (ThreadStatePredicate *)ThreadStatePredicate_Type.tp_alloc(&ThreadStatePredicate_Type, 0);

        self->thread_state = Py_NewRef(thread_state);
        self->test_index = test_index;
        self->vectorcall = (vectorcallfunc)ThreadStatePredicate::call;

        return self;
    }

    struct ThreadState;

    static thread_local ThreadState * last_thread_state = nullptr;
    static thread_local int last_per_thread_state = -1;

    // const char * cached_thread_state() {
    //     if (last_per_thread_state) {
    //         PyObject * pystr = PyObject_Str(last_per_thread_state);
    //         const char * str = PyUnicode_AsUTF8(pystr);
    //         Py_DECREF(pystr);
    //         return str;
    //     } else {
    //         return nullptr;
    //     }
    // }

    struct ThreadState : public PyObject {
        PyObject * avaliable_states;
        PyObject * default_state;
        PyObject * predicates;

        // PyThreadState_GetDict()

        int index_of(PyObject * state) {
            for (int i = 0; i < PyTuple_GET_SIZE(avaliable_states); i++) {
                PyObject * item = PyTuple_GET_ITEM(avaliable_states, i);

                int eq = PyObject_RichCompareBool(state, item, Py_EQ);

                if (eq < 0) return -1;
                else if (eq > 0) return i;
            }
            PyErr_Format(PyExc_TypeError, "value: %S was not one of: %S", state, avaliable_states);
            return -1;
        }

        int index() {
            if (last_thread_state == this) {
                return last_per_thread_state;
            } else {

                PyObject * dict = PyThreadState_GetDict();
                PyObject * per_thread = PyDict_GetItem(dict, this);

                if (per_thread == nullptr) {
                    PyDict_SetItem(dict, this, default_state);
                    per_thread = Py_NewRef(default_state);
                }
                
                int index = index_of(per_thread);
                if (index >= 0) {
                    last_thread_state = this;
                    last_per_thread_state = index;
                }
                return index;
            }
        }

        PyObject * per_thread_state() {
            int i = index();
            return i >= 0 ? PyTuple_GET_ITEM(avaliable_states, i) : nullptr;
        }

        void set_state_by_index(int i) {
            assert(i >= 0);

            ThreadState* cached = last_thread_state;  // single TLS read
            if (cached && cached != this) {
                PyObject * dict = PyThreadState_GetDict();

                PyObject * state = PyTuple_GET_ITEM(avaliable_states, last_per_thread_state);
                PyDict_SetItem(dict, cached, state);
                last_thread_state = this;
            }
            last_per_thread_state = i;
        }

        int get_state_index() {
            ThreadState* cached = last_thread_state;  // single TLS read
            if (cached != this) {
                PyObject * dict = PyThreadState_GetDict();

                PyObject * state = PyTuple_GET_ITEM(avaliable_states, last_per_thread_state);
                PyDict_SetItem(dict, cached, state);
                
                last_thread_state = this;
                last_per_thread_state = index_of(PyDict_GetItem(dict, this));
            }
            return last_per_thread_state;
        }

        static int init(ThreadState * self, PyObject * args, PyObject * kwds) {
            if (kwds) {
                PyErr_Format(PyExc_TypeError, "%S does not take keyword arguments", Py_TYPE(self));
                return -1;
            }

            if (PyTuple_Size(args) < 2) {
                PyErr_Format(PyExc_TypeError, "%S requires at least two positional arguments", Py_TYPE(self));
                return -1;
            }

            // self->avaliable_states = copy_tuple(args);
            self->avaliable_states = Py_NewRef(args);

            // TODO - check all elements are distinct
            
            self->predicates = PyTuple_New(PyTuple_Size(self->avaliable_states));
            if (!self->predicates) return -1;

            for (Py_ssize_t i = 0; i < PyTuple_Size(self->avaliable_states); i++) {
                PyObject * pred = ThreadStatePredicate_New(self, i);

                PyTuple_SetItem(self->predicates, i, pred);
            }

            // printf("Avaliable states: %p\n", self->avaliable_states);

            self->default_state = Py_NewRef(PyTuple_GetItem(self->avaliable_states, 0));

            // Py_NewRef(default_state ? default_state : PyTuple_GetItem(avaliable_states, 0));

            return 0;
        }
        
        static int clear(ThreadState* self) {
            Py_CLEAR(self->avaliable_states);
            Py_CLEAR(self->default_state);
            Py_CLEAR(self->predicates);
            return 0;
        }
        
        static int traverse(ThreadState* self, visitproc visit, void* arg) {
            Py_VISIT(self->avaliable_states);
            Py_VISIT(self->default_state);
            Py_VISIT(self->predicates);
            return 0;
        }

        static void dealloc(ThreadState *self) {
            PyObject_GC_UnTrack(self);          // Untrack from the GC
            clear(self);
            Py_TYPE(self)->tp_free(self);
        }

        static PyObject * repr(ThreadState *self) {
            return PyUnicode_FromFormat("<ThreadState %S of %S>", self->per_thread_state(), self->avaliable_states);
        }

        static PyObject * str(ThreadState *self) {
            return PyUnicode_FromFormat("<ThreadState %S of %S>", self->per_thread_state(), self->avaliable_states);
        }

        // static PyObject * iter(ThreadLocalProxy *self) {
        //     PyObject * obj = self->target();
        //     return obj ? PyObject_GetIter(obj) : nullptr;
        // }

        // static PyObject * iternext(ThreadLocalProxy *self) {
        //     PyObject * obj = self->target();
        //     return obj ? PyIter_Next(obj) : nullptr;
        // }

        static PyObject * select(ThreadState *self, PyObject * desired_state) {

            int index = self->index_of(desired_state);

            return index >= 0 ? ThreadStateContext_New(self, index) : nullptr;
        }

        static PyObject * predicate(ThreadState *self, PyObject * test_state) {

            int index = self->index_of(test_state);

            return index >= 0 ? Py_NewRef(PyTuple_GetItem(self->predicates, index)) : nullptr;
        }

        static PyObject * set_dispatch(ThreadState * self, PyObject * args, PyObject *kwds) {
            if (!kwds) {
                Py_RETURN_NONE;
            }

            if (PyTuple_Size(args) != 1) {
                PyErr_Format(PyExc_TypeError, "dispatch takes the dispatch object as the only positional arguemnt");
                return nullptr;
            }
            if (!PyObject_TypeCheck(PyTuple_GET_ITEM(args, 0), &Dispatch_Type)) {
                PyErr_Format(PyExc_TypeError, "dispatch parameter not of Dispatch type");
                return nullptr;
            }

            Dispatch * dispatch = reinterpret_cast<Dispatch *>(PyTuple_GET_ITEM(args, 0));

            for (int i = 0; i < PyTuple_GET_SIZE(self->avaliable_states); i++) {
                PyObject * key = PyTuple_GET_ITEM(self->avaliable_states, i);

                if (PyDict_Contains(kwds, key)) {
                    Py_DECREF(dispatch->handlers[i].callable);
                    dispatch->handlers[i] = FastCall(Py_NewRef(PyDict_GetItem(kwds, key)));
                }
            }
            Py_RETURN_NONE;
        }
        
        PyObject * create_dispatch(PyTypeObject * cls, PyObject * default_dispatch, PyObject *kwds) {
            Dispatch * dispatch = (Dispatch *)cls->tp_alloc(cls, PyTuple_Size(avaliable_states));

            if (!dispatch) return nullptr;

            dispatch->vectorcall = (vectorcallfunc)Dispatch::call;
            dispatch->state = Py_NewRef(this);

            for (int i = 0; i < PyTuple_GET_SIZE(avaliable_states); i++) {
                PyObject * key = PyTuple_GET_ITEM(avaliable_states, i);

                PyObject * val = kwds ? PyDict_GetItem(kwds, key) : nullptr;

                if (val) {
                    dispatch->handlers[i] = FastCall(Py_XNewRef(val));
                } else {
                    if (PyErr_Occurred()) {
                        Py_DECREF(dispatch);
                        return nullptr;
                    } else if (default_dispatch) {
                        dispatch->handlers[i] = FastCall(Py_XNewRef(default_dispatch));
                    } else {
                        PyErr_Format(PyExc_TypeError, "unhandled case: %S, an no default dispatch given", key);
                        return nullptr;
                    }
                }
            }
            return (PyObject *)dispatch;
        }

        static PyObject * method_dispatch(ThreadState * self, PyObject * args, PyObject *kwds) {
            if (PyTuple_Size(args) > 1) {
                PyErr_Format(PyExc_TypeError, "method_dispatch takes a single optional positional arguemnt, the default dispatch");
                return nullptr;
            }

            PyObject * default_dispatch = PyTuple_Size(args) > 0 ? PyTuple_GetItem(args, 0) : nullptr;
            
            return self->create_dispatch(&MethodDispatch_Type, default_dispatch, kwds);
        }

        static PyObject * dispatch(ThreadState * self, PyObject * args, PyObject *kwds) {
            if (PyTuple_Size(args) > 1) {
                PyErr_Format(PyExc_TypeError, "dispatch takes a single optional positional arguemnt, the default dispatch");
                return nullptr;
            }

            PyObject * default_dispatch = PyTuple_Size(args) > 0 ? PyTuple_GetItem(args, 0) : nullptr;
            
            return self->create_dispatch(&Dispatch_Type, default_dispatch, kwds);

            // Dispatch * dispatch = (Dispatch *)Dispatch_Type.tp_alloc(&Dispatch_Type, PyTuple_Size(self->avaliable_states));

            // if (!dispatch) return nullptr;

            // dispatch->vectorcall = (vectorcallfunc)Dispatch::call;
            // dispatch->state = Py_NewRef(self);

            // for (int i = 0; i < PyTuple_GET_SIZE(self->avaliable_states); i++) {
            //     PyObject * key = PyTuple_GET_ITEM(self->avaliable_states, i);

            //     PyObject * val = kwds ? PyDict_GetItem(kwds, key) : nullptr;

            //     if (val) {
            //         dispatch->handlers[i] = FastCall(Py_XNewRef(val));
            //     } else {
            //         if (PyErr_Occurred()) {
            //             Py_DECREF(dispatch);
            //             return nullptr;
            //         } else if (default_dispatch) {
            //             dispatch->handlers[i] = FastCall(Py_XNewRef(default_dispatch));
            //         } else {
            //             PyErr_Format(PyExc_TypeError, "unhandled case: %S, an no default dispatch given", key);
            //             return nullptr;
            //         }
            //     }
            // }
            // return (PyObject *)dispatch;
        }

        static PyObject * wrap(ThreadState * self, PyObject * args, PyObject *kwds) {
            static const char *kwlist[] = { "desired_state", "function", "sticky", NULL };

            PyObject * function;
            PyObject * desired_state;
            int sticky = 0;

            if (!PyArg_ParseTupleAndKeywords(args, kwds, "OO|p", (char **)kwlist, &desired_state, &function, &sticky))
            {
                 return nullptr; // Return NULL on failure
            }

            int index = self->index_of(desired_state);

            return index >= 0 ? ThreadStateWrapped_New(self, function, index, sticky) : nullptr;
        }

        static PyObject * value_getter(ThreadState *self, void *closure) {
            return Py_NewRef(self->per_thread_state());
        }

        PyObject * state_for(int index) {
            return PyTuple_GET_ITEM(avaliable_states, index);
        }

        static int value_setter(ThreadState *self, PyObject * value, void *closure) {
            int index = self->index_of(value);

            if (index < 0) return -1;

            ThreadState_SetIndex(self, index);
            return 0;
        }
    };

    // static PyObject * avaliable_states(PyqObject * state) {
    //     return reinterpret_cast<ThreadState *>(state)->avaliable_states;
    // }

    static PyMethodDef methods[] = {
        // {"set", (PyCFunction)ThreadLocalProxy::set_classmethod, METH_VARARGS | METH_KEYWORDS | METH_CLASS, "Set the thread-local target"},
        {"select", (PyCFunction)ThreadState::select, METH_O, "Set the thread-local target"},
        {"predicate", (PyCFunction)ThreadState::predicate, METH_O, "Set the thread-local target"},
        {"wrap", (PyCFunction)ThreadState::wrap, METH_VARARGS | METH_KEYWORDS, "Set the thread-local target"},
        {"dispatch", (PyCFunction)ThreadState::dispatch, METH_VARARGS | METH_KEYWORDS, "Set the thread-local target"},
        {"method_dispatch", (PyCFunction)ThreadState::method_dispatch, METH_VARARGS | METH_KEYWORDS, "Set the thread-local target"},
        {"set_dispatch", (PyCFunction)ThreadState::set_dispatch, METH_VARARGS | METH_KEYWORDS, "Set the thread-local target"},
        // {"dispatch_table", (PyCFunction)ThreadState::dispatch_table, METH_O, "TODO"},
        {NULL, NULL, 0, NULL}
    };

    // static PyObject * ThreadState_Value(PyObject * state) {
    //     assert(Py_TYPE(state) == &ThreadState_Type);

    //     return reinterpret_cast<ThreadState *>(state)->per_thread_state();
    // }

    static PyObject * ThreadState_AvaliableStates(PyObject * state) {
        assert(Py_TYPE(state) == &ThreadState_Type);

        return reinterpret_cast<ThreadState *>(state)->avaliable_states;
    }

    static PyObject * ThreadState_ValueForIndex(PyObject * state, int index) {
        assert(Py_TYPE(state) == &ThreadState_Type);

        return reinterpret_cast<ThreadState *>(state)->state_for(index);
    }

    static void ThreadState_SetIndex(PyObject * state, int index) {
        assert(Py_TYPE(state) == &ThreadState_Type);

        return reinterpret_cast<ThreadState *>(state)->set_state_by_index(index);
    }

    static int ThreadState_GetIndex(PyObject * state) {
        assert(Py_TYPE(state) == &ThreadState_Type);

        return reinterpret_cast<ThreadState *>(state)->index();
    }

    static PyGetSetDef getset[] = {
        {"value", (getter)ThreadState::value_getter, (setter)ThreadState::value_setter, "TODO", NULL},
        {NULL}  // Sentinel
    };

    PyTypeObject ThreadState_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "ThreadState",
        .tp_basicsize = sizeof(ThreadState),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)ThreadState::dealloc,
        .tp_repr = (reprfunc)ThreadState::repr,
        .tp_str = (reprfunc)ThreadState::str,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_HAVE_GC,
        .tp_doc = "TODO",
        .tp_traverse = (traverseproc)ThreadState::traverse,
        .tp_clear = (inquiry)ThreadState::clear,

        // .tp_iter = (getiterfunc)ThreadState::iter,
        // .tp_iternext = (iternextfunc)ThreadState::iternext,
        // .tp_methods = methods,
        // .tp_members = members,

        .tp_methods = methods,
        .tp_getset = getset,
        .tp_init = (initproc)ThreadState::init,
        .tp_new = PyType_GenericNew,
    };
}