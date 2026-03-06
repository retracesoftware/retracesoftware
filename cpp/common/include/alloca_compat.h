// common-headers/include/alloca_compat.h
// Cross-platform alloca support
#pragma once

#if defined(__GNUC__) || defined(__clang__)
    #include <alloca.h>
#elif defined(_MSC_VER)
    #include <malloc.h>
    #define alloca _alloca
#endif
