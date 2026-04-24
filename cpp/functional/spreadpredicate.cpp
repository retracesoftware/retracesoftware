#include "functional.h"
#include <new>

namespace {

    struct SpreadPredicate : public PyObject {
        vectorcallfunc vectorcall;
        retracesoftware::FastCall predicate;
        Py_ssize_t starting;

        static int clear(SpreadPredicate* self) {
            Py_CLEAR(self->predicate.callable);
            return 0;
        }

        static int traverse(SpreadPredicate* self, visitproc visit, void* arg) {
            Py_VISIT(self->predicate.callable);
            return 0;
        }

        static void dealloc(SpreadPredicate* self) {
            PyObject_GC_UnTrack(self);
            clear(self);
            Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
        }

        static PyObject* descr_get(PyObject* self, PyObject* obj, PyObject* type) {
            return obj == NULL || obj == Py_None ? Py_NewRef(self) : PyMethod_New(self, obj);
        }

        template <bool MatchAny>
        static PyObject* call_impl(SpreadPredicate* self, PyObject* const* args, size_t nargsf, PyObject* kwnames) {
            const Py_ssize_t nargs = PyVectorcall_NARGS(nargsf);
            const Py_ssize_t kwcount = kwnames ? PyTuple_GET_SIZE(kwnames) : 0;
            const Py_ssize_t start = self->starting < nargs ? self->starting : nargs;

            for (Py_ssize_t i = start; i < nargs; i++) {
                PyObject* result = self->predicate(const_cast<PyObject*>(args[i]));
                if (!result) {
                    return nullptr;
                }

                const int truthy = PyObject_IsTrue(result);
                Py_DECREF(result);
                if (truthy < 0) {
                    return nullptr;
                }

                if constexpr (MatchAny) {
                    if (truthy) {
                        Py_RETURN_TRUE;
                    }
                } else {
                    if (!truthy) {
                        Py_RETURN_FALSE;
                    }
                }
            }

            for (Py_ssize_t i = nargs; i < nargs + kwcount; i++) {
                PyObject* result = self->predicate(const_cast<PyObject*>(args[i]));
                if (!result) {
                    return nullptr;
                }

                const int truthy = PyObject_IsTrue(result);
                Py_DECREF(result);
                if (truthy < 0) {
                    return nullptr;
                }

                if constexpr (MatchAny) {
                    if (truthy) {
                        Py_RETURN_TRUE;
                    }
                } else {
                    if (!truthy) {
                        Py_RETURN_FALSE;
                    }
                }
            }

            if constexpr (MatchAny) {
                Py_RETURN_FALSE;
            } else {
                Py_RETURN_TRUE;
            }
        }

        static PyObject* call_and(SpreadPredicate* self, PyObject* const* args, size_t nargsf, PyObject* kwnames) {
            return call_impl<false>(self, args, nargsf, kwnames);
        }

        static PyObject* call_or(SpreadPredicate* self, PyObject* const* args, size_t nargsf, PyObject* kwnames) {
            return call_impl<true>(self, args, nargsf, kwnames);
        }

        static PyObject* create(PyTypeObject* type, PyObject* args, PyObject* kwds, vectorcallfunc vectorcall) {
            PyObject* predicate = nullptr;
            Py_ssize_t starting = 0;
            static const char* kwlist[] = {"predicate", "starting", NULL};
            if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|n", (char**)kwlist, &predicate, &starting)) {
                return nullptr;
            }
            if (!PyCallable_Check(predicate)) {
                PyErr_Format(PyExc_TypeError, "%S requires a callable predicate, got %S", type, predicate);
                return nullptr;
            }
            if (starting < 0) {
                PyErr_Format(PyExc_ValueError, "%S starting must be >= 0", type);
                return nullptr;
            }

            SpreadPredicate* self = reinterpret_cast<SpreadPredicate*>(type->tp_alloc(type, 0));
            if (!self) {
                return nullptr;
            }

            new (&self->predicate) retracesoftware::FastCall(Py_NewRef(predicate));
            self->vectorcall = vectorcall;
            self->starting = starting;
            return reinterpret_cast<PyObject*>(self);
        }
    };

    static PyObject* spread_and_create(PyTypeObject* type, PyObject* args, PyObject* kwds) {
        return SpreadPredicate::create(type, args, kwds, reinterpret_cast<vectorcallfunc>(SpreadPredicate::call_and));
    }

    static PyObject* spread_or_create(PyTypeObject* type, PyObject* args, PyObject* kwds) {
        return SpreadPredicate::create(type, args, kwds, reinterpret_cast<vectorcallfunc>(SpreadPredicate::call_or));
    }
}

PyTypeObject SpreadAnd_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "spread_and",
    .tp_basicsize = sizeof(SpreadPredicate),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)SpreadPredicate::dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(SpreadPredicate, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT |
                Py_TPFLAGS_HAVE_GC |
                Py_TPFLAGS_HAVE_VECTORCALL |
                Py_TPFLAGS_METHOD_DESCRIPTOR |
                Py_TPFLAGS_BASETYPE,
    .tp_doc = "spread_and(predicate, starting=0)\n--\n\n"
              "Apply predicate to each positional argument from `starting` onward and keyword value.\n\n"
              "Returns True iff predicate(value) is truthy for every value.\n"
              "Keyword names are ignored; only values are checked.\n\n"
              "Example:\n"
              "    >>> all_positive = spread_and(lambda x: x > 0, starting=1)\n"
              "    >>> all_positive('fn', 1, 2, three=3)\n"
              "    True",
    .tp_traverse = (traverseproc)SpreadPredicate::traverse,
    .tp_clear = (inquiry)SpreadPredicate::clear,
    .tp_descr_get = SpreadPredicate::descr_get,
    .tp_new = (newfunc)spread_and_create,
};

PyTypeObject SpreadOr_Type = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = MODULE "spread_or",
    .tp_basicsize = sizeof(SpreadPredicate),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)SpreadPredicate::dealloc,
    .tp_vectorcall_offset = OFFSET_OF_MEMBER(SpreadPredicate, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_flags = Py_TPFLAGS_DEFAULT |
                Py_TPFLAGS_HAVE_GC |
                Py_TPFLAGS_HAVE_VECTORCALL |
                Py_TPFLAGS_METHOD_DESCRIPTOR |
                Py_TPFLAGS_BASETYPE,
    .tp_doc = "spread_or(predicate)\n--\n\n"
              "Apply predicate to each positional argument and keyword value.\n\n"
              "Returns True iff predicate(value) is truthy for any value.\n"
              "Keyword names are ignored; only values are checked.\n\n"
              "Example:\n"
              "    >>> any_negative = spread_or(lambda x: x < 0)\n"
              "    >>> any_negative(1, two=2, three=-3)\n"
              "    True",
    .tp_traverse = (traverseproc)SpreadPredicate::traverse,
    .tp_clear = (inquiry)SpreadPredicate::clear,
    .tp_descr_get = SpreadPredicate::descr_get,
    .tp_new = (newfunc)spread_or_create,
};
