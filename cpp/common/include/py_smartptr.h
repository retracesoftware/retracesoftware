// common-headers/include/py_smartptr.h
// RAII smart pointers for PyObject management
#pragma once

#include <Python.h>
#include <memory>

// PyDeleter - custom deleter that calls Py_XDECREF
struct PyDeleter {
    void operator()(PyObject* p) const { Py_XDECREF(p); }
};

// PyUniquePtr - unique_ptr that automatically decrefs on destruction
// Use for temporary PyObject* that you own and need to clean up
using PyUniquePtr = std::unique_ptr<PyObject, PyDeleter>;

// PySafePtr - alternative using function pointer (same behavior)
using PySafePtr = std::unique_ptr<PyObject, decltype(&Py_DecRef)>;

// Helper to create PySafePtr (since Py_DecRef needs to be passed)
inline PySafePtr make_safe_ptr(PyObject* obj) {
    return PySafePtr(obj, &Py_DecRef);
}

// PyObjectGuard - RAII guard that decrefs on scope exit
// Useful when you can't use unique_ptr (e.g., need to return the object)
class PyObjectGuard {
    PyObject* ptr;
    bool released = false;
public:
    explicit PyObjectGuard(PyObject* p) : ptr(p) {}
    ~PyObjectGuard() { if (!released) Py_XDECREF(ptr); }
    
    PyObject* get() const { return ptr; }
    PyObject* release() { released = true; return ptr; }
    
    // Prevent copying
    PyObjectGuard(const PyObjectGuard&) = delete;
    PyObjectGuard& operator=(const PyObjectGuard&) = delete;
};
