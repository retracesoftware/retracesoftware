#pragma once

#include "fastcall.h"

#if defined(__GNUC__) || defined(__clang__)
#include <alloca.h>
#elif defined(_MSC_VER)
#include <malloc.h>
#define alloca _alloca
#endif

#include <signal.h>

#include <Python.h>

#define SMALL_ARGS 5

#define MODULE "retracesoftware.functional."

// Visibility macros for symbol export control
// With -fvisibility=hidden, only EXPORT_SYMBOL makes symbols visible
#if defined(__GNUC__) || defined(__clang__)
    #define EXPORT_SYMBOL __attribute__((visibility("default")))
    #define HIDDEN_SYMBOL __attribute__((visibility("hidden")))
#elif defined(_MSC_VER)
    #define EXPORT_SYMBOL __declspec(dllexport)
    #define HIDDEN_SYMBOL
#else
    #define EXPORT_SYMBOL
    #define HIDDEN_SYMBOL
#endif

#define OFFSET_OF_MEMBER(type, member) \
    ((Py_ssize_t) &reinterpret_cast<const volatile char&>((((type*)0)->member)))


extern PyType_Spec Repeatedly_Spec;
extern PyType_Spec DropArgs_Spec;

extern PyTypeObject InstanceTest_Type;
extern PyTypeObject CallAll_Type;
extern PyTypeObject Compose_Type;
extern PyTypeObject SideEffect_Type;
// extern PyTypeObject Repeatedly_Type;
extern PyTypeObject NotPredicate_Type;
extern PyTypeObject AndPredicate_Type;
extern PyTypeObject OrPredicate_Type;
extern PyTypeObject TypePredicate_Type;
extern PyTypeObject TransformArgs_Type;
extern PyTypeObject First_Type;
extern PyTypeObject Advice_Type;
extern PyTypeObject WhenPredicate_Type;
extern PyTypeObject CasePredicate_Type;
extern PyTypeObject Memoize_Type;
extern PyTypeObject ManyPredicate_Type;
extern PyTypeObject Walker_Type;
extern PyTypeObject TypePredWalker_Type;
extern PyTypeObject Partial_Type;
extern PyTypeObject MethodInvoker_Type;
extern PyTypeObject Intercept_Type;
extern PyTypeObject Indexer_Type;
extern PyTypeObject Param_Type;
extern PyTypeObject PositionalParam_Type;
extern PyTypeObject TernaryPredicate_Type;
extern PyTypeObject IfThenElse_Type;
extern PyTypeObject AnyArgs_Type;
extern PyTypeObject FirstOf_Type;
extern PyTypeObject Always_Type;
extern PyTypeObject SelfApply_Type;
extern PyTypeObject Spread_Type;
extern PyTypeObject Constantly_Type;
extern PyTypeObject Either_Type;
extern PyTypeObject Compose2_Type;
extern PyTypeObject Vector_Type;
extern PyTypeObject UseWith_Type;
extern PyTypeObject DeepWrap_Type;
extern PyTypeObject WhenNotNone_Type;
extern PyTypeObject Lazy_Type;
extern PyTypeObject ArityDispatch_Type;

// extern PyTypeObject When_Type;
// extern PyTypeObject WhenNot_Type;

PyObject * instanceof_andnot(PyTypeObject * cls, PyTypeObject * andnot);
PyObject * instance_test(PyTypeObject * cls);
PyObject * notinstance_test(PyTypeObject * cls);
PyObject * instanceof(PyTypeObject * cls);

extern PyObject * ThreadLocalError;

PyObject * join(const char * sep, PyObject * elements);

// PyObject * find_first(std::function<PyObject * (PyObject *)> f, PyObject * obj);

PyObject * partial(PyObject * function, PyObject * const * args, size_t nargs);
PyObject * dispatch(PyObject * const * args, size_t nargs);
PyObject * firstof(PyObject * const * args, size_t nargs);

struct ManyPredicate : public PyObject {
    PyObject * elements;
    vectorcallfunc vectorcall;
    PyObject *weakreflist;
};

inline int check_callable(PyObject *obj, void *out) {
    if (!PyCallable_Check(obj)) {
        PyErr_Format(PyExc_TypeError, "Expected a callable object, but recieved: %S", obj);
        return 0;
    }
    *((PyObject **)out) = obj;
    return 1;
}

static inline vectorcallfunc extract_vectorcall(PyObject *callable)
{
    PyTypeObject *tp = Py_TYPE(callable);
    if (!PyType_HasFeature(tp, Py_TPFLAGS_HAVE_VECTORCALL)) {
        return PyObject_Vectorcall;
    }
    Py_ssize_t offset = tp->tp_vectorcall_offset;

    vectorcallfunc ptr;
    memcpy(&ptr, (char *) callable + offset, sizeof(ptr));
    return ptr;
}

#define CHECK_CALLABLE(name) \
    if (name) { \
        if (name == Py_None) name = nullptr; \
        else if (!PyCallable_Check(name)) { \
            PyErr_Format(PyExc_TypeError, "Parameter '%s' must be callable, but was: %S", #name, name); \
            return -1; \
        } \
    }

