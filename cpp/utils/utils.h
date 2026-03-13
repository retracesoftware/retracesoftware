#pragma once
#include "defines.h"

#include <Python.h>
#include <functional>
#include <signal.h>
#include <string>
#include <vector>
#include <tuple>

#include "fastcall.h"
#include "unordered_dense.h"

using namespace ankerl::unordered_dense;

void dump_stack_trace(PyThreadState * tstate);

#define CHECK_CALLABLE(name) \
    if (name) { \
        if (name == Py_None) name = nullptr; \
        else if (!PyCallable_Check(name)) { \
            PyErr_Format(PyExc_TypeError, "Parameter '%s' must be callable, but was: %S", #name, name); \
            return -1; \
        } \
    }

extern "C" int generation_to_collect(int multiplier);

namespace retracesoftware {

    // extern PyTypeObject NullContext_Type;

    extern PyTypeObject Counter_Type;
    extern PyTypeObject BlockingCounter_Type;

    extern PyTypeObject Dispatch_Type;
    extern PyTypeObject ThreadState_Type;
    extern PyTypeObject ThreadStateContext_Type;
    extern PyTypeObject ThreadStateWrapped_Type;

    extern PyTypeObject Demultiplexer_Type;
    extern PyTypeObject Dispatcher_Type;
    extern PyTypeObject ThreadStatePredicate_Type;
    extern PyTypeObject StripTraceback_Type;

    extern PyTypeObject StableSet_Type;
    extern PyTypeObject StableFrozenSet_Type;
    extern PyTypeObject StableSetIterator_Type;

    extern PyTypeObject FastSet_Type;
    extern PyTypeObject InstanceCheck_Type;
    extern PyTypeObject Visitor_Type;
    extern PyTypeObject Wrapped_Type;
    extern PyTypeObject Proxy_Type;
    extern PyTypeObject Reference_Type;
    extern PyTypeObject WrappedFunction_Type;
    extern PyTypeObject WrappedMember_Type;
    extern PyTypeObject IdSet_Type;
    extern PyTypeObject IdSetTest_Type;
    extern PyTypeObject IdDict_Type;
    extern PyTypeObject IdSetLogical_Type;

    extern PyTypeObject ThreadSwitchMonitor_Type;
    extern PyTypeObject StripTraceback_Type;
    extern PyTypeObject Observer_Type;
    extern PyTypeObject WeakRefCallback_Type;
    extern PyTypeObject PerThread_Type;
    
    extern PyTypeObject FrameWrapper_Type;
    extern PyTypeObject FrameEval_Type;
    
    extern PyTypeObject NewWrapper_Type;
    extern PyTypeObject MethodDispatch_Type;
    extern PyTypeObject Marker_Type;
    extern PyTypeObject Patched_Type;
    extern PyTypeObject FastTypePredicate_Type;
    extern PyTypeObject DictIntercept_Type;
    extern PyTypeObject CollectPred_Type;
    extern PyTypeObject RunAll_Type;
    extern PyTypeObject StackFactory_Type;
    extern PyTypeObject Stack_Type;

    extern PyTypeObject Gate_Type;
    extern PyTypeObject BoundGate_Type;
    extern PyTypeObject GateContext_Type;
    extern PyTypeObject ApplyWith_Type;
    extern PyTypeObject GatePredicate_Type;
    extern PyTypeObject CacheSentinel_Type;

    extern PyTypeObject MemoryAddresses_Type;
    extern PyTypeObject Remover_Type;
    extern PyTypeObject Branch_Type;
    extern PyTypeObject DeallocBridge_Type;
    extern PyTypeObject TypePatchState_Type;

    extern PyTypeObject ThreadLocal_Type;
    extern PyTypeObject ThreadLocalContext_Type;

    extern PyTypeObject ThreadContextWrapper_Type;

    PyObject *start_new_thread_wrapper(PyObject *module, PyObject *const *args, Py_ssize_t nargs);
    bool threadcontext_init(PyObject *module);

    void force_full_gc(void);

    struct ModuleState {
        map<PyObject *, PyObject *> obj_to_id;
    };

    ModuleState* get_module_state(PyObject* module);

    PyObject * Wrapped_Target(PyObject * proxy);

    void * Reference_GetPointer(PyObject * reference);

    // PyObject * create_proxy(PyTypeObject * proxytype, PyObject * handler, PyObject * target);

    uint64_t Counter_Next(PyObject * counter);

    // Used by stack.cpp - struct for efficient stack tracking
    struct CodeLocation {
        PyObject * filename;
        uint16_t lineno;

        CodeLocation(PyObject * filename, uint16_t lineno) : filename(filename), lineno(lineno) {}

        bool operator==(const CodeLocation& other) const {
            return lineno == other.lineno && !PyUnicode_Compare(filename, other.filename);
        }

        PyObject * as_tuple() const {
            return PyTuple_Pack(2, Py_NewRef(filename), PyLong_FromLong(lineno));
        }

        bool operator!=(const CodeLocation& other) const {
            return !(*this == other);
        }
    };

    struct Frame {
        uint16_t instruction;
        PyCodeObject * code_object;
        
        Frame(PyCodeObject * code_object, uint16_t instruction) : 
            instruction(instruction), code_object(code_object) {
            assert(code_object);
        }

        uint16_t lineno() const {
            return PyCode_Addr2Line(code_object, instruction);
        }

        CodeLocation location() const {
            return CodeLocation(code_object->co_filename, lineno());
        }

        auto operator<=>(const Frame&) const = default;
    };


    PyObject * create_wrapped(PyTypeObject * cls, PyObject * target);

    void patch_hash(PyTypeObject * cls, PyObject * hashfunc);

    // PyObject * StableSet_GetItem(PyObject * set, int index);
    // static inline vectorcallfunc extract_vectorcall(PyObject *callable)
    // {
    //     PyTypeObject *tp = Py_TYPE(callable);
    //     if (!PyType_HasFeature(tp, Py_TPFLAGS_HAVE_VECTORCALL)) {
    //         return PyObject_Vectorcall;
    //     }
    //     Py_ssize_t offset = tp->tp_vectorcall_offset;

    //     vectorcallfunc ptr;
    //     memcpy(&ptr, (char *) callable + offset, sizeof(ptr));
    //     return ptr;
    // }

    bool FrameEval_Install(PyInterpreterState * is, PyObject * handler);

    bool install_new_wrapper(PyTypeObject * cls, PyObject * handler);
    
    struct Wrapped : public PyObject {
        PyObject * target;
        PyObject * weakreflist;
    };

    PyObject * set_on_alloc(PyTypeObject *type, PyObject * callback);
    bool is_alloc_patched(allocfunc func);

    bool intercept_dict_set(PyObject * dict, PyObject * on_set);
}
