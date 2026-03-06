// #include "functional.h"
// #include <structmember.h>
// #include <signal.h>

// struct When {
//     PyObject_HEAD
//     PyObject * pred;
//     vectorcallfunc pred_vectorcall;
//     PyObject * func;
//     vectorcallfunc func_vectorcall;

//     vectorcallfunc vectorcall;
// };

// static int run_predicate(When * self, PyObject** args, size_t nargsf, PyObject* kwnames) {
//     PyObject * res = self->pred_vectorcall(self->pred, args, nargsf, kwnames);

//     if (!res) return -1;
//     int status = PyObject_IsTrue(res);
//     Py_DECREF(res);
//     return status;
// }

// static PyObject * when(When * self, PyObject** args, size_t nargsf, PyObject* kwnames) {

//     switch (run_predicate(self, args, nargsf, kwnames)) {
//         case 0:
//             return Py_NewRef(args[0]);
//         case 1:
//             return self->func_vectorcall(self->func, args, nargsf, kwnames);
//         default:
//             assert(PyErr_Occurred());
//             return nullptr;
//     }
// }

// static int traverse(WhenPredicate* self, visitproc visit, void* arg) {
//     Py_VISIT(self->predicate);
//     Py_VISIT(self->function);

//     return 0;
// }

// static int clear(WhenPredicate* self) {
//     Py_CLEAR(self->predicate);
//     Py_CLEAR(self->function);
//     return 0;
// }

// static void dealloc(WhenPredicate *self) {
//     PyObject_GC_UnTrack(self);          // Untrack from the GC
//     clear(self);
//     Py_TYPE(self)->tp_free((PyObject *)self);  // Free the object
// }

// static PyMemberDef members[] = {
//     {"predicate", T_OBJECT, offsetof(WhenPredicate, predicate), READONLY, "TODO"},
//     {"function", T_OBJECT, offsetof(WhenPredicate, function), READONLY, "TODO"},
//     {NULL}  /* Sentinel */
// };

// static PyObject * create(PyTypeObject *type, PyObject *args, PyObject *kwds) {

//     PyObject * predicate;
//     PyObject * function;
    
//     static const char *kwlist[] = {"predicate", "function", NULL};

//     if (!PyArg_ParseTupleAndKeywords(args, kwds, "OO", (char **)kwlist, &predicate, &function))
//     {
//         return NULL; // Return NULL on failure
//     }

//     WhenPredicate * self = (WhenPredicate *)type->tp_alloc(type, 0);

//     if (!self) {
//         return NULL;
//     }

//     self->predicate = Py_NewRef(predicate);
//     self->function = Py_NewRef(function);
//     self->vectorcall = (vectorcallfunc)vectorcall;

//     return (PyObject *)self;
// }

// PyTypeObject WhenNot_Type = {
//     .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
//     .tp_name = MODULE "when_not",
//     .tp_basicsize = sizeof(WhenPredicate),
//     .tp_itemsize = 0,
//     .tp_dealloc = (destructor)dealloc,
//     .tp_vectorcall_offset = offsetof(WhenPredicate, vectorcall),
//     .tp_call = PyVectorcall_Call,
//     .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
//     .tp_doc = "TODO",
//     .tp_traverse = (traverseproc)traverse,
//     .tp_clear = (inquiry)clear,
//     // .tp_methods = methods,
//     .tp_members = members,
//     .tp_new = (newfunc)create,
// };

// PyTypeObject When_Type = {
//     .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
//     .tp_name = MODULE "when",
//     .tp_basicsize = sizeof(WhenPredicate),
//     .tp_itemsize = 0,
//     .tp_dealloc = (destructor)dealloc,
//     .tp_vectorcall_offset = offsetof(WhenPredicate, vectorcall),
//     .tp_call = PyVectorcall_Call,
//     .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL,
//     .tp_doc = "TODO",
//     .tp_traverse = (traverseproc)traverse,
//     .tp_clear = (inquiry)clear,
//     // .tp_methods = methods,
//     .tp_members = members,
//     .tp_new = (newfunc)create,
// };
