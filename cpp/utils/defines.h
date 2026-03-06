#pragma once
#define MODULE "retracesoftware_utils."

#define OFFSET_OF_MEMBER(type, member) \
    ((Py_ssize_t) &reinterpret_cast<const volatile char&>((((type*)0)->member)))
