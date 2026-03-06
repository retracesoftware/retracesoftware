/*
 * Copyright 2025 Retrace Software
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "utils.h"
#include <cstdint>
#include <vector>
#include <algorithm>
// #include "methodobject.h"
// #include "object.h"
#include "unordered_dense.h"
#include <utility>  // for std::pair
#include <functional>

using namespace ankerl::unordered_dense;

#ifndef LINEAR_PROBES
#define LINEAR_PROBES 9
#endif

/* This must be >= 1 */
#define PERTURB_SHIFT 5

// Cut and paste from setobject.c, v3.11
static setentry *
set_lookkey(PySetObject *so, PyObject *key, Py_hash_t hash)
{
    setentry *table;
    setentry *entry;
    size_t perturb = hash;
    size_t mask = so->mask;
    size_t i = (size_t)hash & mask; /* Unsigned for defined overflow behavior */
    int probes;
    int cmp;

    while (1) {
        entry = &so->table[i];
        probes = (i + LINEAR_PROBES <= mask) ? LINEAR_PROBES: 0;
        do {
            if (entry->hash == 0 && entry->key == NULL)
                return entry;
            if (entry->hash == hash) {
                PyObject *startkey = entry->key;
                // assert(startkey != dummy);
                if (startkey == key)
                    return entry;
                if (PyUnicode_CheckExact(startkey)
                    && PyUnicode_CheckExact(key)
                    && _PyUnicode_EQ(startkey, key))
                    return entry;
                table = so->table;
                Py_INCREF(startkey);
                cmp = PyObject_RichCompareBool(startkey, key, Py_EQ);
                Py_DECREF(startkey);
                if (cmp < 0)
                    return NULL;
                if (table != so->table || entry->key != startkey)
                    return set_lookkey(so, key, hash);
                if (cmp > 0)
                    return entry;
                mask = so->mask;
            }
            entry++;
        } while (probes--);
        perturb >>= PERTURB_SHIFT;
        i = (i * 5 + 1 + perturb) & mask;
    }
}

static PyObject * find(PySetObject *so, PyObject * key) {
    Py_hash_t hash;

    if (!PyUnicode_CheckExact(key) ||
        (hash = _PyASCIIObject_CAST(key)->hash) == -1) {
        hash = PyObject_Hash(key);
        if (hash == -1)
            return nullptr;
    }

    setentry * entry = set_lookkey(so, key, hash);

    return entry ? entry->key : nullptr;
}

namespace retracesoftware {

    template <typename T>
    struct ReversedView {
        T& container;

        auto begin() const { return container.rbegin(); }
        auto end() const { return container.rend(); }
    };

    template <typename T>
    ReversedView<T> reversed(T& container) {
        return {container};
    }

    static Py_ssize_t coll_size(PyObject * coll) {
        if (PyTuple_Check(coll)) {
            return PyTuple_GET_SIZE(coll);
        } else if (PyList_Check(coll)) {
            return PyList_GET_SIZE(coll);
        } else if (PySet_Check(coll)) {
            return PySet_GET_SIZE(coll);
        } else {
            return 0;
        }
    }

    // 0xFFFF0F30

    // 1111 1111 1111 0000 1111 0011 0000

    // imagine we have set {a, b, c}, [a, b, c]
    // we add d: {a, b, c, d}, [a, b, c, d]
    // we add b: {a, b, c}, [a, b, c]
    // we remove c: {a, b}, [a, b, c, c-]
    // we add d: {a, b, d}, [a, b, c, c-, d]
    // we add d: {a, b, d}, [a, b, d]

    // collapse: [a, b, c, c-, d] => [a, b, d]
    //           [#, #, #, #,  #] {}
    //           [#, #, #, #,  d] {}
    //           [#, #, #, #, d] {c}
    //           [#, #, #, #, d] {}
    //           [a, b, #, #, d] {}
    //           [a, b, d]

    // have a stable set, where we keep a shadow
    // set ordering, can extend stable set
    // advantage is this approach is we can replace the type where neccessary
    // we can build the order 

    // for difference_update?
    // run through original order checking if elements are in updated set, removing if so.
    // simple

    // empty sets or sets with one element dont need an ordering


    // type pointer -> set
    // refernce count
    // foo
    // bar

    // type pointer
    // refernce count
    // foo
    // bar

    // replacements

    // builtins.set => _functional.stable_set
    // builtins.frozen_set => _functional.frozen_stable_set


    template <typename T, typename Pred>
    std::pair<std::vector<T>, std::vector<T>> partition(Pred pred, const std::vector<T>& input) {
        std::vector<T> yes, no;
        
        yes.reserve(input.size());
        no.reserve(input.size());

        for (const auto& elem : input) {
            if (pred(elem))
                yes.push_back(elem);
            else
                no.push_back(elem);
        }
        return {yes, no};
    }

    template <typename Container, typename T>
    void remove_value(Container& container, const T& value) {
        container.erase(
            std::remove(container.begin(), container.end(), value),
            container.end()
        );
    }

    static bool is_delete(uintptr_t obj) {
        return (obj & 0x1) == 0x1;
    }

    static uintptr_t remove_delete_flag(uintptr_t obj) {
        return obj - 1;
    }

    static uintptr_t add_delete_flag(uintptr_t obj) {
        return obj + 1;
    }

    static void clean(std::vector<uintptr_t> &order) {
        set<uintptr_t> deletes;

        for (auto &obj : reversed(order)) {
            assert(obj);

            if (is_delete(obj)) {
                deletes.insert(remove_delete_flag(obj));
                obj = 0;
            } else if (deletes.contains(obj)) {
                deletes.erase(obj);
                obj = 0;
            }
        }
    }

    template <typename T>
    bool remove_first(std::vector<T>& vec, const T& value) {
        auto it = std::find(vec.begin(), vec.end(), value);
        if (it != vec.end()) {
            vec.erase(it);
            return true;  // Successfully removed
        }
        return false;  // Value not found
    }

    static void remove_deletes(std::vector<uintptr_t> &order) {
        clean(order);
        remove_value(order, 0);
    }

    static bool foreach(std::function<bool (PyObject *)> f, PyObject * coll) {
        if (PyTuple_Check(coll)) {
            for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(coll); i++) {
                if (!f(PyTuple_GET_ITEM(coll, i))) {
                    return false;
                }
            }
        } else if (PyList_Check(coll)) {
            for (Py_ssize_t i = 0; i < PyList_GET_SIZE(coll); i++) {
                if (!f(PyList_GET_ITEM(coll, i))) {
                    return false;
                }
            }
        } else {
            PyObject *iterator = PyObject_GetIter(coll);
            if (!iterator) {
                return -1;  // Not iterable
            }
            PyObject *item;

            while ((item = PyIter_Next(iterator))) {
                if (!f(item)) {
                    Py_DECREF(item);
                    Py_DECREF(iterator);
                    return false;
                }
                Py_DECREF(item);  // PyIter_Next returns new ref
            }
            Py_DECREF(iterator);
            if (PyErr_Occurred()) {
                return false;  // Iteration error
            }
        }
        return true;
    }

    static PyObject * as_set(PyObject * coll) {
        if (PySet_Check(coll)) {
            return Py_NewRef(coll);
        } else {
            PyObject * set = PySet_New(0);
            if (!set) return nullptr;

            auto add = [set](PyObject * obj) {
                return PySet_Add(set, obj) >= 0;
            };

            if (!foreach(add, coll)) {
                Py_DECREF(set);
                return nullptr;
            }
            return set;
        }
    }

    static PyObject * StableSetIterator_New(PyObject * set);

    static bool StableSet_Check(PyObject * set);

    struct StableSet : public PySetObject {
        std::vector<uintptr_t> order;

        static void dealloc(StableSet *self) {
            self->order.~vector<uintptr_t>();

            PyTypeObject * cls = Py_TYPE(self);

            while (cls->tp_dealloc != (destructor)dealloc) {
                cls = cls->tp_base;
            }
            while (cls->tp_dealloc == (destructor)dealloc) {
                cls = cls->tp_base;
            }

            cls->tp_dealloc((PyObject *)self);
        };

        int contains(PyObject * obj) {
            return PySet_Contains(reinterpret_cast<PyObject *>(this), obj);
        }

        size_t size() {
            return (size_t)PySet_GET_SIZE(this);
        }

        bool remove_deletes() {
            if (order.size() > size()) {
                retracesoftware::remove_deletes(order);
                assert (order.size() == size());
                return true;
            }
            return false;
        }

        bool add(PyObject *key) {

            if ((order.size() * 2) > size()) {
                retracesoftware::remove_deletes(order);
                assert (order.size() == size());
            }

            size_t before = size();

            switch (PySet_Add(reinterpret_cast<PyObject *>(this), key)) {
                case 0:
                    if (size() != before) {
                        order.push_back((uintptr_t)key);
                    }
                    return true;
                default:
                    assert(PyErr_Occurred());
                    return false;
            }
        }
        
        int discard(PyObject *key) {        
            
            PyObject * orig_key = find(this, key);

            int status = PySet_Discard((PyObject *)this, key);
            if (status == 1) {
                if (order.size() < 8 && order.size() == size() + 1) {
                    remove_first(order, (uintptr_t)orig_key);
                    // just delete the element
                } else {
                    uintptr_t tombstone = add_delete_flag((uintptr_t)orig_key);
                    assert(is_delete(tombstone));
                    order.push_back(tombstone);
                }
            } else if (status < 0) {
                assert (PyErr_Occurred());
            }
            return status;
        }

        bool remove(PyObject *key) {
            switch (discard(key)) {
            case 0: case 1: return true;
                // PyErr_Format(PyExc_KeyError, "Required key: %S not in set for removal", key);
            default:
                assert(PyErr_Occurred());
                return false;
            }
        }

        bool add_all(PyObject * coll) {
            return retracesoftware::foreach([this] (PyObject * key) { return add(key); }, coll);
        }

        bool add_all(std::vector<PyObject *> &coll) {
            for (auto key : coll) {
                if (!add(key)) return false;
            }
            return true;
        }

        bool remove_all(std::vector<PyObject *> &coll) {
            for (auto obj : coll) {
                if (PySet_Discard((PyObject *)this, obj) < 0) {
                    elements_removed();
                    return false;        
                }
            }
            elements_removed();
            return true;
        }

        static PyObject * create(PyTypeObject *type, PyObject *args, PyObject *kwds) {

            PyTypeObject * cls = type;

            while (cls->tp_new != create) {
                cls = cls->tp_base;
            }
            while (cls->tp_new == create) {
                cls = cls->tp_base;
            }

            StableSet * self = (StableSet *)cls->tp_new(type, args, kwds);

            if (!self) return nullptr;

            // if (!PyObject_TypeCheck(self, &StableSet_Type) || !PyObject_TypeCheck(self, &StableFrozenSet_Type)) {
            //     PyErr_Format(PyExc_SystemError, "Internal error in creating stable set");
            //     Py_DECREF(self);
            //     return nullptr;
            // }

            new (&self->order) std::vector<uintptr_t>();
            return (PyObject *)self;
        }

        static PyObject * frozenset_create(PyTypeObject *type, PyObject *args, PyObject *kwds) {

            PyObject *iterable = NULL;

            if (!_PyArg_NoKeywords("frozenset", kwds))
                return nullptr;

            if (!PyArg_UnpackTuple(args, "frozenset", 0, 1, &iterable))
                return nullptr;

            PyTypeObject * cls = type;

            while (cls->tp_new != frozenset_create) {
                cls = cls->tp_base;
            }
            while (cls->tp_new == frozenset_create) {
                cls = cls->tp_base;
            }

            PyObject * empty = PyTuple_New(0);

            StableSet * self = (StableSet *)cls->tp_new(type, empty, nullptr);

            Py_DECREF(empty);

            if (iterable == NULL || !self) {
                return (PyObject *)self;
            }
            
            new (&self->order) std::vector<uintptr_t>();

            if (!self->add_all(iterable)) {
                Py_DECREF(self);
                return nullptr;
            }
            return (PyObject *)self;
        }

        static int init(StableSet *self, PyObject *args, PyObject *kwds) {
            
            PyObject *iterable = NULL;

            if (!_PyArg_NoKeywords("set", kwds))
                return -1;

            if (!PyArg_UnpackTuple(args, Py_TYPE(self)->tp_name, 0, 1, &iterable))
                return -1;

            if (iterable == NULL)
                return 0;

            // PyObject * it = PyObject_GetIter(iterable);

            // if (it == NULL)
            //     return -1;

            return self->add_all(iterable);

            // Py_DECREF(it);

            // return added;
        }


        PyObject * pop() {
            // is the last element added a 
            if (PySet_GET_SIZE(this) == 0) {
                PyErr_SetNone(PyExc_KeyError);
                return nullptr;
            }

            assert(!order.empty());

            PyObject * last_added = (PyObject *)order.back();
                
            if (is_delete((uintptr_t)last_added)) {
                remove_deletes();
                return pop();
            } else {
                Py_INCREF(last_added);

                switch (PySet_Discard((PyObject *)this, last_added)) {
                    case 1:
                        order.pop_back();
                        return last_added;
                    case 0:
                        PyErr_Format(PyExc_SystemError, "Sync error between order and set in pop");
                        return nullptr;
                    default:
                        Py_DECREF(last_added);
                        assert(PyErr_Occurred());
                        return nullptr;
                }       
            }
        }

        bool elements_removed() {
            retracesoftware::remove_deletes(order);

            for (auto &obj : order) {
                switch (contains((PyObject *)obj)) {
                    case 0: break;
                    case 1: 
                        obj = 0;
                        break;
                    default:
                        return false;
                }
            }
            retracesoftware::clean(order);
            return true;
        }

        static PyTypeObject * base_type(PyObject * base);

        static PyObject * create_set(PyObject * iterable) {
            return PyObject_CallOneArg((PyObject *)&StableSet_Type, iterable);

            // PyObject * args = PyTuple_Pack(1, iterable);

            // PyObject * self = StableSet_Type.tp_new(&StableSet_Type, args, nullptr);

            // Py_DECREF(args);

            // return self;
        }

        static PyObject * create_frozenset(PyObject * iterable) {
            return PyObject_CallOneArg((PyObject *)&StableFrozenSet_Type, iterable);

            // PyObject * args = PyTuple_Pack(1, iterable);

            // PyObject * self = StableFrozenSet_Type.tp_new(&StableFrozenSet_Type, args, nullptr);

            // Py_DECREF(args);

            // return self;
        }


        StableSet * copy() {

            PyTypeObject * cls = base_type((PyObject *)this);

            PyObject * args = PyTuple_New(0);

            StableSet * self = (StableSet *)cls->tp_new(cls, args, nullptr);

            Py_DECREF(args);

            if (!self) return nullptr;

            retracesoftware::remove_deletes(order);

            self->order.reserve(order.size());

            for (uintptr_t obj : order) {
                Py_INCREF((PyObject *)obj);
            }
            
            for (uintptr_t obj : order) {
                if (!self->add((PyObject *)obj)) {
                    Py_DECREF(self);
                    self = nullptr;
                    break;
                }
            }
            for (uintptr_t obj : order) {
                Py_DECREF((PyObject *)obj);
            }
            return self;
        }

        static StableSet * py_copy(StableSet *self, PyObject * unused) {
            return self->copy();
        }

        static PyObject * py_remove(StableSet *self, PyObject *key) {
            return self->discard(key) >= 0 ? Py_NewRef(Py_None) : nullptr;
        }
        
        static PyObject * py_pop(StableSet *self, PyObject *unused) {
            return self->pop();
        }

        static PyObject * clear(StableSet *self, PyObject *unused) {

            if (PySet_Clear((PyObject *)self) == -1) return nullptr;

            self->order.clear();
            Py_RETURN_NONE;
        }

        static PyObject * iter(StableSet *self)
        {
            // if the length of order != size of set, might need to collate
            self->remove_deletes();

            return StableSetIterator_New(reinterpret_cast<PyObject *>(self));
        }

        static PyObject * py_add(StableSet *self, PyObject *key) {
            return self->add(key) ? Py_NewRef(Py_None) : nullptr;
        }

        static PyObject * py_discard(StableSet *self, PyObject *key) {
            return self->discard(key) >= 0 ? Py_NewRef(Py_None) : nullptr;
        }

        bool intersection_update(PyObject * coll) {

            PyObject * other = retracesoftware::as_set(coll);

            if (!other) return false;

            remove_deletes();

            for (auto& obj : order) {
                switch (PySet_Contains(other, (PyObject *)obj)) {
                    case 0: 
                        switch(PySet_Discard((PyObject *)this, (PyObject *)obj)) {
                            case 1:
                                obj = 0;
                                break;
                            case 0:
                                PyErr_Format(PyExc_SystemError, "Out of sync");
                            default:
                                Py_DECREF(other);
                                return false;   
                        }
                        break;
                    case 1:
                        break;
                    default:
                        Py_DECREF(other);
                        return -1;    
                }
            }
            Py_DECREF(other);
            retracesoftware::remove_value(order, 0);
            // retracesoftware::clean(order); 

            return true;
        }

        bool intersection_update_multi(PyObject *args) {

            for (Py_ssize_t i = 0 ; i < PyTuple_GET_SIZE(args); i++) {
                PyObject *other = PyTuple_GET_ITEM(args, i);
                
                if (!intersection_update(other)) {
                    return false;
                }
            }
            return true;
        }

        static StableSet * py_intersection_multi(StableSet *self, PyObject *args) {
            StableSet * copy = self->copy();

            if (!copy || !copy->intersection_update_multi(args)) {
                Py_XDECREF(copy);
                return nullptr;
            } 
            return copy;
        } 

        static PyObject * py_intersection_update_multi(StableSet *self, PyObject *args) {
            return self->intersection_update_multi(args) ? Py_NewRef(Py_None) : nullptr;
        } 

        bool difference_update(PyObject * coll) {
            return retracesoftware::foreach([this](PyObject * obj) { return remove(obj); }, coll);
        }

        // static PyStableSet * difference(PyObject * left, PyObject * right) {
        //     ...
        // }

        bool difference_update_multi(PyObject *args) {
            for (Py_ssize_t i = 0 ; i < PyTuple_GET_SIZE(args); i++) {
                if (!difference_update(PyTuple_GET_ITEM(args, i))) return false;
            }
            return true;
        }

        static PyObject * py_difference_update_multi(StableSet *self, PyObject *args) {
            return self->difference_update_multi(args) ? Py_NewRef(Py_None) : nullptr;
        }

        static StableSet * py_difference_multi(StableSet *self, PyObject *args) {
            StableSet * copy = self->copy();

            if (!copy || !copy->difference_update_multi(args)) {
                Py_XDECREF(copy);
                return nullptr;
            } 
            return copy;
        }

        bool update_multi(PyObject *args) {
            for (Py_ssize_t i = 0 ; i < PyTuple_GET_SIZE(args); i++) {
                PyObject *other = PyTuple_GET_ITEM(args, i);
                
                if (!add_all(other)) {
                    return false;
                }
            }
            return true;
        }

        static PyObject * py_update_multi(StableSet *self, PyObject *args) {
            return self->update_multi(args) ? Py_NewRef(Py_None) : nullptr;
        }

        static StableSet * py_union_multi(StableSet *self, PyObject *args) {
            StableSet * copy = self->copy();

            if (!copy || !copy->update_multi(args)) {
                Py_XDECREF(copy);
                return nullptr;
            } 
            return copy;
        }

        bool symmetric_difference_update(PyObject *coll) {
            Py_ssize_t size = retracesoftware::coll_size(coll);

            std::vector<PyObject *> extra;
            std::vector<PyObject *> shared;

            if (size) {
                extra.reserve(size);
                shared.reserve(size);
            }

            auto update = [this, &extra, &shared] (PyObject * obj) {
                switch (contains(obj)) {
                case 0:
                    extra.push_back(Py_NewRef(obj));
                    return true;
                case 1:
                    shared.push_back(Py_NewRef(obj));
                    return true;
                default:
                    return false;
                }
            };

            bool success = foreach(update, coll) && remove_all(shared) && add_all(extra);

            for (PyObject * obj : extra) Py_DECREF(obj);
            for (PyObject * obj : shared) Py_DECREF(obj);

            return success;
        }

        static PyObject * py_symmetric_difference_update(StableSet *self, PyObject *other) {
            return self->symmetric_difference_update(other) ? Py_NewRef(Py_None) : nullptr;
        }

        static StableSet * py_symmetric_difference(StableSet *self, PyObject *other) {
            StableSet * copy = self->copy();
            
            if (!copy || !copy->symmetric_difference_update(other)) {
                Py_XDECREF(copy);
                return nullptr;
            } 
            return copy;
        }

        static PyObject * set_sub(PyObject * a, PyObject * b) {
            PyObject * copy = create_set(a);
            if (!copy) return nullptr;
            if (!reinterpret_cast<StableSet *>(copy)->difference_update(b)) {
                Py_DECREF(copy);
                return nullptr;
            }
            return copy;
        }

        static PyObject * frozenset_sub(PyObject * a, PyObject * b) {
            PyObject * set = set_sub(a, b);

            if (!set) return nullptr;
            PyObject * frozen = create_frozenset(set);
            Py_DECREF(set);
            return frozen;
        }

        static PyObject * set_and(PyObject *so, PyObject *other)
        {
            PyObject * copy = create_set(so);
            if (!copy) return nullptr;
            if (!reinterpret_cast<StableSet *>(copy)->intersection_update(other)) {
                Py_DECREF(copy);
                return nullptr;
            }
            return copy;
        }

        static PyObject * frozenset_and(PyObject * so, PyObject * other) {
            PyObject * set = set_and(so, other);

            if (!set) return nullptr;
            PyObject * frozen = create_frozenset(set);
            Py_DECREF(set);
            return frozen;
        }

        static PyObject * set_xor(PyObject *so, PyObject *other)
        {
            PyObject * copy = create_set(so);

            if (!copy) return nullptr;
            if (!reinterpret_cast<StableSet *>(copy)->symmetric_difference_update(other)) {
                Py_DECREF(copy);
                return nullptr;
            }
            return copy;
        }

        static PyObject * frozenset_xor(PyObject * so, PyObject * other) {
            PyObject * set = set_xor(so, other);

            if (!set) return nullptr;
            PyObject * frozen = create_frozenset(set);
            Py_DECREF(set);
            return frozen;
        }
        
        static PyObject * set_or(PyObject *so, PyObject *other)
        {
            PyObject * copy = create_set(so);
            
            if (!copy) return nullptr;
            else if ((PyObject *)so == other) return (PyObject *)copy;
            else if (!reinterpret_cast<StableSet *>(copy)->add_all(other)) {
                Py_DECREF(copy);
                return nullptr;
            }
            return reinterpret_cast<PyObject *>(copy);
        }

        static PyObject * frozenset_or(PyObject * so, PyObject * other) {

            PyObject * set = set_or(so, other);

            if (!set) return nullptr;

            PyObject * frozen = create_frozenset(set);

            Py_DECREF(set);
            return frozen;
        }

        static PyObject * set_isub(StableSet *so, PyObject *other)
        {
            // if (!StableSet_Check(other))
            //     Py_RETURN_NOTIMPLEMENTED;

            if (!so->difference_update(other)) {
                return nullptr;
            }
            return Py_NewRef(reinterpret_cast<PyObject *>(so));
        }

        static PyObject * set_iand(StableSet *so, PyObject *other)
        {
            // if (!StableSet_Check(other))
            //     Py_RETURN_NOTIMPLEMENTED;

            if (!so->intersection_update(other)) {
                return nullptr;
            }
            return Py_NewRef(reinterpret_cast<PyObject *>(so));
        }

        static PyObject * set_ixor(StableSet *so, PyObject *other)
        {
            // if (!StableSet_Check(other))
            //     Py_RETURN_NOTIMPLEMENTED;

            if (!so->symmetric_difference_update(other)) {
                return nullptr;
            }
            return Py_NewRef(reinterpret_cast<PyObject *>(so));
        }
    
        static PyObject * set_ior(StableSet *so, PyObject *other)
        {
            // if (!StableSet_Check(other))
            //     Py_RETURN_NOTIMPLEMENTED;

            if (!so->add_all(other)) {
                return nullptr;
            }
            return Py_NewRef(reinterpret_cast<PyObject *>(so));
        }
    };

    static const char * doc = "This string needs to be replaced";

    static PyMethodDef StableSet_methods[] = {
        {"add", (PyCFunction)StableSet::py_add, METH_O, doc},
        {"remove", (PyCFunction)StableSet::py_remove, METH_O, doc},
        {"pop", (PyCFunction)StableSet::py_pop, METH_NOARGS, doc},
        {"copy", (PyCFunction)StableSet::py_copy, METH_NOARGS, doc},
        {"clear", (PyCFunction)StableSet::clear, METH_NOARGS, doc},

        {"discard", (PyCFunction)StableSet::py_discard, METH_O, doc},

        {"union",           (PyCFunction)StableSet::py_union_multi,         METH_VARARGS, doc},
        {"update",          (PyCFunction)StableSet::py_update_multi,        METH_VARARGS, doc},

        {"difference",      (PyCFunction)StableSet::py_difference_multi, METH_VARARGS, doc},
        {"difference_update", (PyCFunction)StableSet::py_difference_update_multi, METH_VARARGS, doc},

        {"intersection", (PyCFunction)StableSet::py_intersection_multi, METH_VARARGS, doc},
        {"intersection_update", (PyCFunction)StableSet::py_intersection_update_multi, METH_VARARGS, doc},

        {"symmetric_difference", (PyCFunction)StableSet::py_symmetric_difference, METH_O, doc},
        {"symmetric_difference_update", (PyCFunction)StableSet::py_symmetric_difference_update, METH_O, doc},

        {NULL, NULL, 0, NULL}  // Sentinel
    };

    static PyMethodDef StableFrozenSet_methods[] = {
        {"copy", (PyCFunction)StableSet::py_copy, METH_NOARGS, doc},
        {"union",           (PyCFunction)StableSet::py_union_multi,         METH_VARARGS, doc},
        {"difference",      (PyCFunction)StableSet::py_difference_multi, METH_VARARGS, doc},
        {"intersection", (PyCFunction)StableSet::py_intersection_multi, METH_VARARGS, doc},
        {"symmetric_difference", (PyCFunction)StableSet::py_symmetric_difference, METH_O, doc},

        {NULL, NULL, 0, NULL}  // Sentinel
    };

    struct StableSetIterator : public PyObject {
        StableSet * stable_set; /* Set to NULL when iterator is exhausted */
        Py_ssize_t si_used;
        std::vector<uintptr_t>::const_iterator i;
        
        StableSetIterator(StableSet * stable_set) :
            stable_set(stable_set), si_used(stable_set->used), i(stable_set->order.begin())
        {
            Py_INCREF(stable_set);
        }

        ~StableSetIterator() {
            Py_XDECREF(stable_set);
        }

        static int traverse(StableSetIterator * self, visitproc visit, void *arg)
        {
            Py_VISIT(self->stable_set);
            return 0;
        }

        static void dealloc(StableSetIterator *self)
        {
            /* bpo-31095: UnTrack is needed before calling any callbacks */
            PyObject_GC_UnTrack(self);
            self->~StableSetIterator();
            PyObject_GC_Del(self);
        };

        static StableSetIterator * new_instance(StableSet * set);

        static PyObject *iternext(StableSetIterator *self)
        {
            if (self->stable_set == NULL)
                return NULL;

            if (self->si_used != self->stable_set->used) {
                PyErr_SetString(PyExc_RuntimeError,
                                "Set changed size during iteration");
                                self->si_used = -1; /* Make this state sticky */
                return NULL;
            }

            if (self->i == self->stable_set->order.end()) {
                Py_DECREF(self->stable_set);
                self->stable_set = nullptr;
                return nullptr;
        
            } else {
                uintptr_t next = *self->i++;
                return Py_NewRef((PyObject *)next);
            }
        }


    };

    static PyObject * StableSetIterator_New(PyObject * set) {
        return StableSetIterator::new_instance(reinterpret_cast<StableSet *>(set));
    }

    PyTypeObject StableSetIterator_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "StableSetIterator",
        .tp_basicsize = sizeof(StableSetIterator),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)StableSetIterator::dealloc,                /* tp_dealloc */
        .tp_getattro = PyObject_GenericGetAttr,                    /* tp_getattro */
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,    /* tp_flags */
        .tp_traverse = (traverseproc)StableSetIterator::traverse,             /* tp_traverse */
        .tp_iter = PyObject_SelfIter,                          /* tp_iter */
        .tp_iternext = (iternextfunc)StableSetIterator::iternext,             /* tp_iternext */
        // .tp_methods = setiter_methods,                            /* tp_methods */
    };

    StableSetIterator * StableSetIterator::new_instance(StableSet * set) {

        auto self = PyObject_GC_New(StableSetIterator, &StableSetIterator_Type);
        if (self == NULL)
            return NULL;

        new (self) StableSetIterator(set);
        PyObject_GC_Track(self);

        return self;
    }


    // static PyObject *
    // set_sub(PySetObject *so, PyObject *other)
    // {
    //     if (!PyAnySet_Check(so) || !PyAnySet_Check(other))
    //         Py_RETURN_NOTIMPLEMENTED;

    //     return set_difference(so, other);
    // }

    static PyNumberMethods set_as_number = {
        0,                                  /*nb_add*/
        (binaryfunc)StableSet::set_sub,                /*nb_subtract*/
        0,                                  /*nb_multiply*/
        0,                                  /*nb_remainder*/
        0,                                  /*nb_divmod*/
        0,                                  /*nb_power*/
        0,                                  /*nb_negative*/
        0,                                  /*nb_positive*/
        0,                                  /*nb_absolute*/
        0,                                  /*nb_bool*/
        0,                                  /*nb_invert*/
        0,                                  /*nb_lshift*/
        0,                                  /*nb_rshift*/
        (binaryfunc)StableSet::set_and,                /*nb_and*/
        (binaryfunc)StableSet::set_xor,                /*nb_xor*/
        (binaryfunc)StableSet::set_or,                 /*nb_or*/
        0,                                  /*nb_int*/
        0,                                  /*nb_reserved*/
        0,                                  /*nb_float*/
        0,                                  /*nb_inplace_add*/
        (binaryfunc)StableSet::set_isub,               /*nb_inplace_subtract*/
        0,                                  /*nb_inplace_multiply*/
        0,                                  /*nb_inplace_remainder*/
        0,                                  /*nb_inplace_power*/
        0,                                  /*nb_inplace_lshift*/
        0,                                  /*nb_inplace_rshift*/
        (binaryfunc)StableSet::set_iand,               /*nb_inplace_and*/
        (binaryfunc)StableSet::set_ixor,               /*nb_inplace_xor*/
        (binaryfunc)StableSet::set_ior,                /*nb_inplace_or*/
    };

    static PyNumberMethods frozenset_as_number = {
        0,                                  /*nb_add*/
        (binaryfunc)StableSet::frozenset_sub,                /*nb_subtract*/
        0,                                  /*nb_multiply*/
        0,                                  /*nb_remainder*/
        0,                                  /*nb_divmod*/
        0,                                  /*nb_power*/
        0,                                  /*nb_negative*/
        0,                                  /*nb_positive*/
        0,                                  /*nb_absolute*/
        0,                                  /*nb_bool*/
        0,                                  /*nb_invert*/
        0,                                  /*nb_lshift*/
        0,                                  /*nb_rshift*/
        (binaryfunc)StableSet::frozenset_and,                /*nb_and*/
        (binaryfunc)StableSet::frozenset_xor,                /*nb_xor*/
        (binaryfunc)StableSet::frozenset_or,                 /*nb_or*/
    };

    PyTypeObject StableSet_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "set",
        .tp_basicsize = sizeof(StableSet),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)StableSet::dealloc,
        .tp_as_number = &set_as_number,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
        .tp_doc = "TODO",
        .tp_iter = (getiterfunc)StableSet::iter,              /* tp_iter */
        .tp_methods = StableSet_methods,
        .tp_base = &PySet_Type,
        .tp_init = (initproc)StableSet::init,
        .tp_new = (newfunc)StableSet::create,
    };

    PyTypeObject StableFrozenSet_Type = {
        .ob_base = PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = MODULE "frozenset",
        .tp_basicsize = sizeof(StableSet),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor)StableSet::dealloc,
        .tp_as_number = &frozenset_as_number,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
        .tp_doc = "TODO",
        .tp_iter = (getiterfunc)StableSet::iter,              /* tp_iter */
        .tp_methods = StableFrozenSet_methods,
        .tp_base = &PyFrozenSet_Type,
        // .tp_init = (initproc)StableSet::init,
        .tp_new = (newfunc)StableSet::frozenset_create,
    };

    PyTypeObject * StableSet::base_type(PyObject * base) {
        return PyObject_TypeCheck(base, &StableSet_Type) 
            ? &StableSet_Type
            : &StableFrozenSet_Type;
    }

    [[maybe_unused]] static bool StableSet_Check(PyObject * set) {
        return PyObject_TypeCheck(set, &StableSet_Type) || PyObject_TypeCheck(set, &StableFrozenSet_Type);
    }

    PyObject * StableSet_GetItem(PyObject * set, int index) {
        assert(StableSet_Check(set));

        StableSet * ss = reinterpret_cast<StableSet *>(set);
    
        ss->remove_deletes();

        if (index < 0 || index > (int)ss->order.size()) {
            PyErr_Format(PyExc_ValueError, "Passed index %s for stable set access bigger than size: %i", index, ss->order.size());
            return nullptr;
        }
        return (PyObject *)ss->order[index];
    }
}
