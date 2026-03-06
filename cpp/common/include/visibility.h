// common-headers/include/visibility.h
// Cross-platform symbol visibility macros
#pragma once

// With -fvisibility=hidden, only EXPORT_SYMBOL makes symbols visible in the shared object
// Use EXPORT_SYMBOL on functions/classes that need to be accessible from Python
// Everything else remains hidden, reducing symbol table size and improving load time

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
