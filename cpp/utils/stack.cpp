#include "utils.h"

#include <internal/pycore_frame.h>

// Bring types into global scope for Stack/StackFactory definitions
using retracesoftware::Frame;
using retracesoftware::CodeLocation;

namespace retracesoftware {
    extern PyTypeObject StackFactory_Type;
    extern PyTypeObject Stack_Type;
}

// Forward declarations
static _PyInterpreterFrame * get_top_frame();
static PyObject * get_func(_PyInterpreterFrame * frame);

// Predicate: returns true if func should be included (not in exclude set)
static inline bool is_included(PyObject * func, PyObject * exclude) {
    return func && PySet_Contains(exclude, func) == 0;
}

// Count included frames from this point down
static int count_included_frames(_PyInterpreterFrame * frame, PyObject * exclude) {
    int count = 0;
    while (frame) {
        if (is_included(get_func(frame), exclude)) count++;
        frame = frame->previous;
    }
    return count;
}

// ============================================================================
// Stack - single frame in a linked list of call stack frames
// ============================================================================
// Each Stack represents one frame and points to the next (deeper) frame.
// This allows sharing common prefixes between stack snapshots.

struct Stack : public PyObject {
    PyObject * func;           // Reference to the function object
    uint16_t instruction;      // Bytecode instruction offset
    uint16_t index;            // Frame index (0 = first/newest frame)
    Stack * next;              // Next frame (older in stack), or nullptr
    
    // Get the code object from the function
    PyCodeObject * code() const {
        if (PyFunction_Check(func)) {
            return (PyCodeObject *)PyFunction_GET_CODE(func);
        }
        return nullptr;
    }
    
    // Get line number for this frame
    int lineno() const {
        PyCodeObject * co = code();
        return co ? PyCode_Addr2Line(co, instruction) : -1;
    }
    
    // Get filename for this frame  
    PyObject * filename() const {
        PyCodeObject * co = code();
        return co ? co->co_filename : nullptr;
    }
    
    // Create a new Stack frame
    static Stack * create(PyObject * func, uint16_t instruction, uint16_t index, Stack * next);
    
    // Create from current interpreter stack, filtering with exclude set
    static Stack * create_from_frame(PyObject * exclude, _PyInterpreterFrame * frame, int index, Stack * reuse);

    static Stack * create_from_frame(PyObject * exclude, _PyInterpreterFrame * frame, Stack * reuse);
    
    // Get frame at index (O(n) traversal)
    Stack * at(uint16_t idx) {
        Stack * current = this;
        while (current && current->index != idx) {
            current = current->next;
        }
        return current;
    }
    
    // Drop n frames from head, return remaining stack
    Stack * drop(int n) {
        Stack * current = this;
        while (current && n > 0) {
            current = current->next;
            n--;
        }
        return current;
    }
    
    // Python type slots
    static void tp_dealloc(Stack * self) {
        PyObject_GC_UnTrack(self);
        Py_XDECREF(self->func);
        Py_XDECREF(self->next);
        Py_TYPE(self)->tp_free(self);
    }
    
    static int tp_traverse(Stack * self, visitproc visit, void * arg) {
        Py_VISIT(self->func);
        Py_VISIT(self->next);
        return 0;
    }
    
    static int tp_clear(Stack * self) {
        Py_CLEAR(self->func);
        Py_CLEAR(self->next);
        return 0;
    }
    
    static Py_ssize_t sq_length(Stack * self) {
        return self->index + 1;
    }
    
    static PyObject * tp_iter(Stack * self);
    
    static PyObject * tp_richcompare(Stack * self, PyObject * other, int op) {
        if (!PyObject_TypeCheck(other, &retracesoftware::Stack_Type)) {
            Py_RETURN_NOTIMPLEMENTED;
        }
        Stack * a = self;
        Stack * b = (Stack *)other;
        
        // Walk both lists comparing frames
        while (a && b) {
            // Same object means rest is identical
            if (a == b) {
                switch (op) {
                    case Py_EQ: Py_RETURN_TRUE;
                    case Py_NE: Py_RETURN_FALSE;
                    default: Py_RETURN_NOTIMPLEMENTED;
                }
            }
            // Compare frame contents
            if (a->func != b->func || a->instruction != b->instruction) {
                switch (op) {
                    case Py_EQ: Py_RETURN_FALSE;
                    case Py_NE: Py_RETURN_TRUE;
                    default: Py_RETURN_NOTIMPLEMENTED;
                }
            }
            a = a->next;
            b = b->next;
        }
        
        // Both null = equal, otherwise different lengths
        bool equal = (a == nullptr && b == nullptr);
        switch (op) {
            case Py_EQ: return PyBool_FromLong(equal);
            case Py_NE: return PyBool_FromLong(!equal);
            default: Py_RETURN_NOTIMPLEMENTED;
        }
    }
    
    // Returns list of (filename, lineno) tuples
    static PyObject * locations(Stack * self, PyObject * Py_UNUSED(args)) {
        Py_ssize_t len = sq_length(self);
        PyObject * result = PyList_New(len);
        if (!result) return nullptr;
        
        Stack * current = self;
        for (Py_ssize_t i = 0; i < len && current; i++, current = current->next) {
            PyObject * fn = current->filename();
            if (!fn) fn = Py_None;
            PyObject * tuple = PyTuple_Pack(2, fn, PyLong_FromLong(current->lineno()));
            if (!tuple) {
                Py_DECREF(result);
                return nullptr;
            }
            PyList_SET_ITEM(result, i, tuple);
        }
        return result;
    }
    
    // Get the next frame (property)
    static PyObject * get_next(Stack * self, void * closure) {
        if (self->next) {
            return Py_NewRef(self->next);
        }
        Py_RETURN_NONE;
    }
    
    // Get the function (property)
    static PyObject * func_getter(Stack * self, void * closure) {
        if (self->func) {
            return Py_NewRef(self->func);
        }
        Py_RETURN_NONE;
    }
    
    // Get line number (property)
    static PyObject * get_lineno(Stack * self, void * closure) {
        return PyLong_FromLong(self->lineno());
    }
    
    // Get filename (property)
    static PyObject * get_filename(Stack * self, void * closure) {
        PyObject * fn = self->filename();
        if (fn) return Py_NewRef(fn);
        Py_RETURN_NONE;
    }
    
    // Get instruction offset (property)
    static PyObject * get_instruction(Stack * self, void * closure) {
        return PyLong_FromLong(self->instruction);
    }
    
    // Get index (property)
    static PyObject * get_index(Stack * self, void * closure) {
        return PyLong_FromLong(self->index);
    }
    
    // __getitem__ - get frame by index
    static PyObject * sq_item(Stack * self, Py_ssize_t idx) {
        // Handle negative indices
        if (idx < 0) {
            Py_ssize_t len = sq_length(self);
            idx = len + idx;
        }
        
        if (idx < 0) {
            PyErr_SetString(PyExc_IndexError, "stack index out of range");
            return nullptr;
        }
        
        // Find the frame with matching index
        Stack * frame = self->at((uint16_t)idx);
        if (!frame) {
            PyErr_SetString(PyExc_IndexError, "stack index out of range");
            return nullptr;
        }
        
        return Py_NewRef(frame);
    }
    
    // changes_from(other) -> (pop_count, tuple_of_frames_to_add)
    // Returns changes needed to transform `other` into `self`

    static PyObject * changes_from(Stack * self, PyObject * arg) {
        // Handle None as empty stack
        Stack * other = nullptr;
        if (arg != Py_None) {
            if (!PyObject_TypeCheck(arg, &retracesoftware::Stack_Type)) {
                PyErr_SetString(PyExc_TypeError, "argument must be Stack or None");
                return nullptr;
            }
            other = (Stack *)arg;
        }
        
        // Same object = no changes
        if (self == other) {
            PyObject * empty = PyTuple_New(0);
            if (!empty) return nullptr;
            return Py_BuildValue("(nN)", (Py_ssize_t)0, empty);
        }
        
        // Find lengths
        Py_ssize_t len_self = sq_length(self);
        Py_ssize_t len_other = other ? sq_length(other) : 0;
        
        // Find common suffix by walking to same depth then comparing
        Stack * p_self = self;
        Stack * p_other = other;
        
        Py_ssize_t to_add = 0;
        Py_ssize_t to_remove = 0;
        
        // Advance the longer one to align depths
        if (len_self > len_other) {
            for (Py_ssize_t i = 0; i < len_self - len_other; i++) {
                to_add++;
                p_self = p_self->next;
            }
        } else if (len_other > len_self) {
            for (Py_ssize_t i = 0; i < len_other - len_self; i++) {
                to_remove++;
                p_other = p_other->next;
            }
        }
        
        // Walk together until they meet (common suffix)
        while (p_self != p_other) {
            to_add++;
            to_remove++;
            p_self = p_self->next;
            p_other = p_other ? p_other->next : nullptr;
        }
        
        // Build tuple of frames to add (from self's head up to common point)
        PyObject * add_tuple = PyTuple_New(to_add);
        if (!add_tuple) return nullptr;
        
        Stack * frame = self;
        for (Py_ssize_t i = 0; i < to_add; i++) {
            PyTuple_SET_ITEM(add_tuple, i, Py_NewRef(frame));
            frame = frame->next;
        }
        
        // Return (to_remove, add_tuple)
        return Py_BuildValue("(nN)", to_remove, add_tuple);
    }
};

// Stack iterator - walks the linked list
struct StackIterator : public PyObject {
    Stack * current;  // Current frame in iteration
    
    static void tp_dealloc(StackIterator * self) {
        Py_XDECREF(self->current);
        Py_TYPE(self)->tp_free(self);
    }
    
    static PyObject * tp_iternext(StackIterator * self) {
        if (!self->current) {
            return nullptr;  // StopIteration
        }
        Stack * frame = self->current;
        self->current = frame->next;
        if (self->current) Py_INCREF(self->current);
        
        // Return (filename, lineno) tuple
        PyObject * fn = frame->filename();
        if (!fn) fn = Py_None;
        PyObject * result = PyTuple_Pack(2, fn, PyLong_FromLong(frame->lineno()));
        Py_DECREF(frame);
        return result;
    }
};

static PyTypeObject StackIterator_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "retracesoftware.utils.StackIterator",
    .tp_basicsize = sizeof(StackIterator),
    .tp_dealloc = (destructor)StackIterator::tp_dealloc,
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_iternext = (iternextfunc)StackIterator::tp_iternext,
};

PyObject * Stack::tp_iter(Stack * self) {
    // Lazy init the iterator type
    if (!PyType_HasFeature(&StackIterator_Type, Py_TPFLAGS_READY)) {
        if (PyType_Ready(&StackIterator_Type) < 0) return nullptr;
    }
    StackIterator * iter = PyObject_New(StackIterator, &StackIterator_Type);
    if (!iter) return nullptr;
    iter->current = (Stack *)Py_NewRef(self);
    return (PyObject *)iter;
}

static PyMethodDef Stack_methods[] = {
    {"locations", (PyCFunction)Stack::locations, METH_NOARGS, "Return list of (filename, lineno) tuples"},
    {"changes_from", (PyCFunction)Stack::changes_from, METH_O, "Returns (pop_count, frames_to_add) to transform other into self"},
    {nullptr}
};

static PyGetSetDef Stack_getset[] = {
    {"next", (getter)Stack::get_next, nullptr, "Next frame in stack (older)", nullptr},
    {"func", (getter)Stack::func_getter, nullptr, "Function for this frame", nullptr},
    {"lineno", (getter)Stack::get_lineno, nullptr, "Line number", nullptr},
    {"filename", (getter)Stack::get_filename, nullptr, "Source filename", nullptr},
    {"instruction", (getter)Stack::get_instruction, nullptr, "Bytecode instruction offset", nullptr},
    {"index", (getter)Stack::get_index, nullptr, "Frame index (0 = first/newest)", nullptr},
    {nullptr}
};

static PySequenceMethods Stack_as_sequence = {
    .sq_length = (lenfunc)Stack::sq_length,
    .sq_item = (ssizeargfunc)Stack::sq_item,
};

namespace retracesoftware {
    PyTypeObject Stack_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = "retracesoftware.utils.Stack",
        .tp_basicsize = sizeof(Stack),
        .tp_dealloc = (destructor)Stack::tp_dealloc,
        .tp_as_sequence = &Stack_as_sequence,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
        .tp_traverse = (traverseproc)Stack::tp_traverse,
        .tp_clear = (inquiry)Stack::tp_clear,
        .tp_richcompare = (richcmpfunc)Stack::tp_richcompare,
        .tp_iter = (getiterfunc)Stack::tp_iter,
        .tp_methods = Stack_methods,
        .tp_getset = Stack_getset,
    };
}

// ============================================================================
// StackFactory - callable that creates Stack snapshots
// ============================================================================

struct StackFactory : public PyObject {
    PyObject * exclude;   // Python set of functions to exclude
    PyObject * cache_key; // Unique key for thread-local cache lookup
    
    static int tp_init(StackFactory * self, PyObject * args, PyObject * kwargs) {
        static const char * kwlist[] = {nullptr};
        if (!PyArg_ParseTupleAndKeywords(args, kwargs, "", const_cast<char**>(kwlist))) {
            return -1;
        }
        self->exclude = PySet_New(nullptr);
        if (!self->exclude) return -1;
        
        // Create a unique cache key for this factory instance
        // Using a tuple of (module_name, id) as key
        self->cache_key = PyUnicode_FromFormat("_stack_cache_%p", (void *)self);
        if (!self->cache_key) {
            Py_DECREF(self->exclude);
            return -1;
        }
        return 0;
    }
    
    static void tp_dealloc(StackFactory * self) {
        PyObject_GC_UnTrack(self);
        
        // Clean up cache entry from current thread's dict
        if (self->cache_key) {
            PyObject * tdict = PyThreadState_GetDict();
            if (tdict) {
                PyDict_DelItem(tdict, self->cache_key);
                PyErr_Clear();  // Ignore KeyError if not present
            }
        }
        
        Py_XDECREF(self->exclude);
        Py_XDECREF(self->cache_key);
        Py_TYPE(self)->tp_free(self);
    }
    
    static int tp_traverse(StackFactory * self, visitproc visit, void * arg) {
        Py_VISIT(self->exclude);
        // cache_key is a string, no need to visit
        return 0;
    }
    
    static int tp_clear(StackFactory * self) {
        Py_CLEAR(self->exclude);
        return 0;
    }
    
    // Get cached stack from thread state dict
    Stack * get_cached_stack() {
        PyObject * tdict = PyThreadState_GetDict();
        if (!tdict) return nullptr;
        
        PyObject * cached = PyDict_GetItem(tdict, cache_key);  // Borrowed ref
        if (cached && PyObject_TypeCheck(cached, &retracesoftware::Stack_Type)) {
            return (Stack *)cached;
        }
        return nullptr;
    }
    
    // Store stack in thread state dict
    void set_cached_stack(Stack * stack) {
        PyObject * tdict = PyThreadState_GetDict();
        if (!tdict) return;
        
        if (stack) {
            PyDict_SetItem(tdict, cache_key, (PyObject *)stack);
        } else {
            PyDict_DelItem(tdict, cache_key);
            PyErr_Clear();
        }
    }
    
    // __call__ - create a new Stack snapshot
    static PyObject * tp_call(StackFactory * self, PyObject * args, PyObject * kwargs) {
        if (PyTuple_GET_SIZE(args) != 0 || (kwargs && PyDict_Size(kwargs) != 0)) {
            PyErr_SetString(PyExc_TypeError, "StackFactory takes no arguments");
            return nullptr;
        }
        
        // Get cached stack from thread-local storage
        Stack * cached = self->get_cached_stack();
        
        Stack * stack = Stack::create_from_frame(
            self->exclude, 
            get_top_frame(),
            cached
        );
        
        // Update thread-local cache for next call's reuse optimization
        self->set_cached_stack(stack);
        
        return (PyObject *)stack;
    }
    
    // delta() -> (pop_count, frames_to_add)
    // Capture current stack, compare to cached, update cache, return changes
    static PyObject * delta(StackFactory * self, PyObject * Py_UNUSED(args)) {
        // Get cached stack from thread-local storage
        Stack * cached = self->get_cached_stack();
        
        Stack * stack = Stack::create_from_frame(
            self->exclude,
            get_top_frame(),
            cached
        );
        
        if (!stack) {
            // Empty stack - return changes from cached
            PyObject * empty = PyTuple_New(0);
            if (!empty) return nullptr;
            Py_ssize_t pop_count = cached ? Stack::sq_length(cached) : 0;
            self->set_cached_stack(nullptr);
            return Py_BuildValue("(nN)", pop_count, empty);
        }
        
        // Compute delta: current.changes_from(cached)
        PyObject * result = Stack::changes_from(stack, cached ? (PyObject *)cached : Py_None);
        
        // Update cache
        self->set_cached_stack(stack);
        Py_DECREF(stack);  // set_cached_stack took a reference via PyDict_SetItem
        
        return result;
    }
    
    // Get the exclude set (read-only property, user can modify the set directly)
    static PyObject * get_exclude(StackFactory * self, void * closure) {
        return Py_NewRef(self->exclude);
    }
};

static PyMethodDef StackFactory_methods[] = {
    {"delta", (PyCFunction)StackFactory::delta, METH_NOARGS, "Capture stack, return (pop_count, frames_to_add) vs cached, update cache"},
    {nullptr}
};

static PyGetSetDef StackFactory_getset[] = {
    {"exclude", (getter)StackFactory::get_exclude, nullptr, "Set of excluded functions", nullptr},
    {nullptr}
};

// ============================================================================
// Internal helpers for stack walking
// ============================================================================

#if PY_VERSION_HEX >= 0x030C0000  // Python 3.12 or higher
static PyObject * get_func(_PyInterpreterFrame * frame) {
    // Skip incomplete frames (being constructed on C stack) - f_funcobj may be garbage
    if (frame->owner == FRAME_OWNED_BY_CSTACK) {
        return nullptr;
    }
    
    PyObject * func = frame->f_funcobj;
    if (!func) {
        return nullptr;
    }
    
    // Skip dicts (class definition frames) - they're unhashable
    if (PyDict_Check(func)) {
        return nullptr;
    }
    return func;
}
#else
static PyObject * get_func(_PyInterpreterFrame * frame) {
    return (PyObject *)frame->f_func;
}
#endif

static _PyInterpreterFrame * get_top_frame() {
    return PyThreadState_Get()->cframe->current_frame;
}

// Stack::create - create a single Stack frame
Stack * Stack::create(PyObject * func, uint16_t instruction, uint16_t index, Stack * next) {
    Stack * self = PyObject_GC_New(Stack, &retracesoftware::Stack_Type);
    if (!self) return nullptr;
    
    self->func = Py_NewRef(func);
    self->instruction = instruction;
    self->index = index;
    self->next = next;  // Takes ownership of the reference
    
    PyObject_GC_Track(self);
    return self;
}

// Stack::create_from_frame - recursive implementation with known index
// Builds list NEWEST to OLDEST with suffix sharing
Stack * Stack::create_from_frame(PyObject * exclude, _PyInterpreterFrame * frame, int index, Stack * reuse) {
    // Base case: no more frames
    if (!frame) return nullptr;
    
    // Get function, skip if excluded (don't increment index for excluded frames)
    PyObject * func = get_func(frame);

    if (is_included(func, exclude)) {
        uint16_t instr = _PyInterpreterFrame_LASTI(frame) * 2;

        if (reuse && index == reuse->index) {
            Stack * rest = create_from_frame(exclude, frame->previous, index - 1, reuse->next);

            if (reuse->next == rest &&
                reuse->func == func && 
                reuse->instruction == instr)  {

                Py_XDECREF(rest);
                return (Stack *)Py_NewRef(reuse);
            } else {
                return create(func, instr, (uint16_t)index, rest);
            }

        } else {
            Stack * rest = create_from_frame(exclude, frame->previous, index - 1, reuse);
            return create(func, instr, (uint16_t)index, rest);
        }
    }
    else {
        return create_from_frame(exclude, frame->previous, index, reuse);
    }
}

// Wrapper that counts frames to determine starting index
Stack * Stack::create_from_frame(PyObject * exclude, _PyInterpreterFrame * frame, Stack * reuse) {
    int count = count_included_frames(frame, exclude);
    
    // If reuse is longer than count, drop extra frames to align indices
    if (reuse) {
        int reuse_len = reuse->index + 1;
        if (reuse_len > count) {
            reuse = reuse->drop(reuse_len - count);
        }
    }
    
    return create_from_frame(exclude, frame, count - 1, reuse);
}

namespace retracesoftware {
    PyTypeObject StackFactory_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = "retracesoftware.utils.StackFactory",
        .tp_basicsize = sizeof(StackFactory),
        .tp_dealloc = (destructor)StackFactory::tp_dealloc,
        .tp_call = (ternaryfunc)StackFactory::tp_call,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
        .tp_traverse = (traverseproc)StackFactory::tp_traverse,
        .tp_clear = (inquiry)StackFactory::tp_clear,
        .tp_methods = StackFactory_methods,
        .tp_getset = StackFactory_getset,
        .tp_init = (initproc)StackFactory::tp_init,
        .tp_new = PyType_GenericNew,
    };
}
