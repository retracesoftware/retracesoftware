#include "stream.h"
#include "wireformat.h"

// #include <frameobject.h>

// void print_current_stack(void)
// {
//     // Get the current frame (top of the stack)
//     PyFrameObject *frame = PyEval_GetFrame();  // borrowed reference!

//     if (frame == NULL) {
//         PySys_WriteStderr("<no Python frame>\n");
//         return;
//     }

//     PySys_WriteStderr("Traceback (most recent call last):\n");

//     // Walk up the frame chain and print each one
//     for (PyFrameObject *f = frame; f != NULL; f = f->f_back) {
//         PyCodeObject *code = PyFrame_GetCode(f);  // new reference
//         if (!code) continue;

//         const char *filename = PyUnicode_AsUTF8(code->co_filename);
//         const char *name     = PyUnicode_AsUTF8(code->co_name);
//         int lineno           = PyFrame_GetLineNumber(f);

//         if (!filename) filename = "<unknown file>";
//         if (!name)     name     = "<unknown function>";

//         PySys_WriteStderr("  File \"%s\", line %d, in %s\n",
//                           filename, lineno, name);

//         Py_DECREF(code);
//     }
// }

namespace retracesoftware_stream {

    struct ReaderWriterBase : public PyObject {
        PyObject * path;
        PyObject * normalize_path;
    };
}