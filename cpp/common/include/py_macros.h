// common-headers/include/py_macros.h
// Common Python extension macros
#pragma once

// OFFSET_OF_MEMBER - get byte offset of a member in a struct
// Used for tp_vectorcall_offset, tp_weaklistoffset, etc.
#define OFFSET_OF_MEMBER(type, member) \
    ((Py_ssize_t) &reinterpret_cast<const volatile char&>((((type*)0)->member)))

// SMALL_ARGS - threshold for stack-allocated argument arrays
// Arrays smaller than this can use alloca instead of heap allocation
#define SMALL_ARGS 5
