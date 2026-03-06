// common-headers/include/module_macros.h
// Macros for parameterized module initialization (release/debug builds)
#pragma once

// MODULE_NAME should be defined by the build system via -DMODULE_NAME=xxx
// These macros enable PyInit_<MODULE_NAME> function name generation

#define _RS_STR(x) #x
#define RS_STR(x) _RS_STR(x)
#define _RS_CONCAT(a, b) a##b
#define RS_CONCAT(a, b) _RS_CONCAT(a, b)

// Usage in module.cpp:
//   #include "module_macros.h"
//   
//   #ifndef MODULE_NAME
//   #define MODULE_NAME my_module  // fallback
//   #endif
//   
//   static PyModuleDef moduledef = {
//       PyModuleDef_HEAD_INIT,
//       RS_STR(MODULE_NAME),  // module name as string
//       "docstring",
//       0,
//       module_methods
//   };
//   
//   PyMODINIT_FUNC RS_CONCAT(PyInit_, MODULE_NAME)(void) {
//       return PyModule_Create(&moduledef);
//   }
