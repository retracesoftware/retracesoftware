#include "functional.h"
#include "object.h"
#include <cstddef>

static PyObject * apply_impl(PyObject *self, PyObject *const *args, Py_ssize_t nargsf, PyObject *kwnames) {
    size_t nargs = PyVectorcall_NARGS(nargsf);

    assert(nargs > 0);
    
    PyObject * func = args[0];

    PyObject * result = PyObject_Vectorcall(func, args + 1, (nargs - 1) | PY_VECTORCALL_ARGUMENTS_OFFSET, kwnames);

    ((PyObject **)args)[0] = func;

    return result;
}

static PyObject * first_arg_impl(PyObject *self, PyObject *const *args, Py_ssize_t nargsf, PyObject *kwnames) {
    if (PyVectorcall_NARGS(nargsf) == 0) {
        PyErr_SetString(PyExc_TypeError, "first_arg() requires at least one positional argument");
        return nullptr;
    }
    return Py_NewRef(args[0]);
}

// static PyObject * partial_impl(PyObject *self, PyObject *const *args, Py_ssize_t nargs) {

//     if (nargs < 2) {
//         PyErr_SetString(PyExc_TypeError, "partial() requires at least two arguments");
//         return NULL;
//     }
//     return partial(args[0], args + 1, nargs - 1);
// }

static PyObject * dispatch_impl(PyObject *self, PyObject *const *args, Py_ssize_t nargs) {
    return dispatch(args + 1, nargs - 1);
}

static PyObject * firstof_impl(PyObject *self, PyObject *const *args, Py_ssize_t nargs) {
    return firstof(args, nargs);
}

static PyObject * py_typeof(PyObject *self, PyObject *obj) { return Py_NewRef((PyObject *)Py_TYPE(obj)); }

static PyObject * identity(PyObject *self, PyObject *obj) { return Py_NewRef(obj); }

static PyObject * py_instanceof(PyObject *self, PyObject * args, PyObject *kwds) { 
    PyTypeObject * cls = nullptr;
    PyTypeObject * andnot = nullptr;

    static const char *kwlist[] = {"cls", "andnot", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O!|O!", (char **)kwlist, 
        &PyType_Type, &cls, &PyType_Type, &andnot))
    {
        return nullptr; // Return NULL on failure
    }
    
    if (andnot) {
        return instanceof_andnot(cls, andnot);
    } else {
        return instanceof(cls);
    }
}

static PyObject * py_instance_test(PyObject *self, PyObject *obj) { 
    if (!PyType_Check(obj)) {
        PyErr_Format(PyExc_TypeError, "instance_test must be passed a type, was passed: %S", obj);
        return nullptr;
    }
    return instance_test(reinterpret_cast<PyTypeObject *>(obj));
}

static PyObject * py_notinstance_test(PyObject *self, PyObject *obj) { 
    if (!PyType_Check(obj)) {
        PyErr_Format(PyExc_TypeError, "notinstance_test must be passed a type, was passed: %S", obj);
        return nullptr;
    }
    return notinstance_test(reinterpret_cast<PyTypeObject *>(obj));
}

// Module-level methods
static PyMethodDef module_methods[] = {
    {"isinstanceof", (PyCFunction)py_instanceof, METH_VARARGS | METH_KEYWORDS, 
     "isinstanceof(cls, andnot=None)\n--\n\n"
     "Create a predicate that tests isinstance(obj, cls).\n\n"
     "If 'andnot' is provided, also checks that obj is NOT an instance of andnot.\n\n"
     "Args:\n"
     "    cls: The type to test for.\n"
     "    andnot: Optional type to exclude.\n\n"
     "Returns:\n"
     "    A callable predicate for isinstance checks."},
    {"instance_test", (PyCFunction)py_instance_test, METH_O, 
     "instance_test(cls)\n--\n\n"
     "Create a predicate: returns obj if isinstance(obj, cls), else None.\n\n"
     "Useful in 'first' chains to filter by type.\n\n"
     "Args:\n"
     "    cls: The type to test for.\n\n"
     "Returns:\n"
     "    A callable that returns obj or None."},
    {"notinstance_test", (PyCFunction)py_notinstance_test, METH_O, 
     "notinstance_test(cls)\n--\n\n"
     "Create a predicate: returns obj if NOT isinstance(obj, cls), else None.\n\n"
     "Inverse of instance_test.\n\n"
     "Args:\n"
     "    cls: The type to exclude.\n\n"
     "Returns:\n"
     "    A callable that returns obj or None."},
    {"typeof", (PyCFunction)py_typeof, METH_O, 
     "typeof(obj)\n--\n\n"
     "Return the exact type of obj (equivalent to type(obj)).\n\n"
     "Args:\n"
     "    obj: Any Python object.\n\n"
     "Returns:\n"
     "    The type object of obj."},
    {"identity", (PyCFunction)identity, METH_O, 
     "identity(obj)\n--\n\n"
     "Return obj unchanged (identity function).\n\n"
     "Useful as a default/no-op in functional pipelines.\n\n"
     "Args:\n"
     "    obj: Any Python object.\n\n"
     "Returns:\n"
     "    obj, unchanged."},
    {"apply", (PyCFunction)apply_impl, METH_FASTCALL | METH_KEYWORDS, 
     "apply(func, *args, **kwargs)\n--\n\n"
     "Call func with the given arguments (like func(*args, **kwargs)).\n\n"
     "Useful for applying functions stored in data structures.\n\n"
     "Args:\n"
     "    func: The callable to invoke.\n"
     "    *args, **kwargs: Arguments to pass to func.\n\n"
     "Returns:\n"
     "    The result of func(*args, **kwargs)."},
    {"first_arg", (PyCFunction)first_arg_impl, METH_FASTCALL | METH_KEYWORDS, 
     "first_arg(*args, **kwargs)\n--\n\n"
     "Return the first positional argument, ignoring the rest.\n\n"
     "Useful for extracting values in pipelines.\n\n"
     "Args:\n"
     "    *args: At least one positional argument required.\n\n"
     "Returns:\n"
     "    The first positional argument."},
    // {"partial", (PyCFunction)partial_impl, METH_FASTCALL, "TODO"},
    {"dispatch", (PyCFunction)dispatch_impl, METH_FASTCALL, 
     "dispatch(test1, then1, test2, then2, ..., [otherwise])\n--\n\n"
     "Create a dispatch/case expression with predicate-function pairs.\n\n"
     "See CasePredicate for details."},
    {"firstof", (PyCFunction)firstof_impl, METH_FASTCALL, 
     "firstof(*functions)\n--\n\n"
     "Return the first non-None result from a sequence of functions.\n\n"
     "See firstof type for details."},
    {NULL, NULL, 0, NULL}  // Sentinel
};

// Module name macros - allows building as _release or _debug
#ifndef MODULE_NAME
#define MODULE_NAME _retracesoftware_functional
#endif

#define _STR(x) #x
#define STR(x) _STR(x)
#define _CONCAT(a, b) a##b
#define CONCAT(a, b) _CONCAT(a, b)

// Module definition
static PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    STR(MODULE_NAME),
    "High-performance functional programming utilities for Python.\n\n"
    "This module provides optimized implementations of common functional\n"
    "patterns including composition, partial application, memoization,\n"
    "predicate combinators, and more. All types support Python 3.11+\n"
    "vectorcall for minimal call overhead.",
    0,
    module_methods
};

PyObject *ThreadLocalError = NULL;

// Module initialization
PyMODINIT_FUNC CONCAT(PyInit_, MODULE_NAME)(void) {
    PyObject* module;

    // Create the module
    module = PyModule_Create(&moduledef);
    if (!module) {
        return NULL;
    }

    ThreadLocalError = PyErr_NewException(MODULE "ThreadLocalError", PyExc_RuntimeError, NULL);
    if (!ThreadLocalError) return nullptr;

    PyTypeObject * hidden_types[] = {
        &FirstOf_Type,
        &InstanceTest_Type,
        nullptr
    };

    for (int i = 0; hidden_types[i]; i++) {
        PyType_Ready(hidden_types[i]);
    }

    PyType_Spec * specs[] = {
        &Repeatedly_Spec,
        &DropArgs_Spec,
        NULL
    };

    for (int i = 0; specs[i]; i++) {
        PyTypeObject * cls = (PyTypeObject *)PyType_FromSpec(specs[i]);
        if (!cls) return nullptr;
        
        const char *last_dot = strrchr(cls->tp_name, '.');

        // If a dot is found, the substring starts after the dot
        const char *name = (last_dot != NULL) ? (last_dot + 1) : cls->tp_name;

        PyModule_AddObject(module, name, (PyObject *)cls);
    }


    PyTypeObject * types[] = {
        &CallAll_Type,
        &Compose_Type,
        &SideEffect_Type,
        // &Repeatedly_Type,
        &ManyPredicate_Type,
        &NotPredicate_Type,
        &AndPredicate_Type,

        &OrPredicate_Type,
        &TypePredicate_Type,
        &TransformArgs_Type,
        &First_Type,
        &Advice_Type,
        &WhenPredicate_Type,
        &CasePredicate_Type,
        &Memoize_Type,

        &Partial_Type,
        &MethodInvoker_Type,
        &Intercept_Type,
        &Indexer_Type,
        &Param_Type,
        &PositionalParam_Type,
        &TernaryPredicate_Type,
        &IfThenElse_Type,
        &AnyArgs_Type,
        &Walker_Type,
        &Always_Type,
        &SelfApply_Type,
        &Spread_Type,
        &Constantly_Type,
        &Either_Type,
        &Compose2_Type,
        &Vector_Type,
        &UseWith_Type,
        &DeepWrap_Type,
        &WhenNotNone_Type,
        &Lazy_Type,
        &ArityDispatch_Type,
        NULL
    };
    
    for (int i = 0; types[i]; i++) {
        if (PyType_Ready(types[i]) != 0) {
            return nullptr;
        }
    }

    for (int i = 0; types[i]; i++) {

        // Find the last dot in the string
        const char *last_dot = strrchr(types[i]->tp_name, '.');

        // If a dot is found, the substring starts after the dot
        const char *name = (last_dot != NULL) ? (last_dot + 1) : types[i]->tp_name;

        PyModule_AddObject(module, name, (PyObject *)types[i]);
        // Py_DECREF(types[i]);
    }
    return module;
}
