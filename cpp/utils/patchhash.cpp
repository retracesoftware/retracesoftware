/**
 * patchhash.cpp - Runtime hash function patching for Python types
 * 
 * This module provides the ability to replace a Python type's __hash__ method
 * at the C level (tp_hash slot) with a custom implementation. This is useful for:
 * 
 *   1. Making mutable objects hashable in a controlled way
 *   2. Implementing deterministic/sequential hashing (e.g., for reproducible tests)
 *   3. Customizing hash behavior without subclassing
 * 
 * Usage from Python:
 *   patch_hash(MyClass, my_hash_func)
 * 
 * The hash function can return:
 *   - int: Used directly as the hash value
 *   - None: Falls back to identity hash (memory address)
 *   - Counter object: Returns spread64(counter.next()) for sequential hashing
 * 
 * Implementation details:
 *   - Hashes are cached per-object in a global map to ensure consistency
 *   - The type's tp_dealloc is hooked to clean up cached hashes on object deletion
 *   - Subclasses inherit the patched hash behavior via tp_base traversal
 */

#include "utils.h"
#include "unordered_dense.h"

using namespace ankerl::unordered_dense;

#include <stdint.h>

// unspread64 (inverse of spread64) removed - not currently used

/**
 * Hash mixing function (splitmix64 finalizer).
 * 
 * Spreads sequential input values across the full 64-bit hash space to avoid
 * clustering in hash tables. This is essential when using Counter-based hashing
 * since consecutive integers would otherwise map to adjacent hash table buckets.
 * 
 * Based on the finalizer from SplitMix64 PRNG by Sebastiano Vigna.
 */
static inline uint64_t spread64(uint64_t x) {
    x ^= x >> 30;
    x *= 0xbf58476d1ce4e5b9ULL;
    x ^= x >> 27;
    x *= 0x94d049bb133111ebULL;
    x ^= x >> 31;
    return x;
}

namespace retracesoftware {

    /**
     * Hasher - Stores the custom hash function and original deallocator for a patched type.
     * 
     * Each patched type gets a Hasher instance that:
     *   - Wraps the user-provided Python hash function via FastCall for efficient invocation
     *   - Preserves the original tp_dealloc so we can chain to it after cleanup
     */
    struct Hasher {
        FastCall hashfunc;   // User's hash function, wrapped for fast calling
        destructor dealloc;  // Original tp_dealloc to chain to
    
        Hasher(const Hasher& other) : hashfunc(other.hashfunc), dealloc(other.dealloc) {}

        Hasher& operator=(const Hasher& other) {
            if (this != &other) {
                hashfunc = other.hashfunc;
                dealloc = other.dealloc;
            }
            return *this;
        }

        Hasher(PyTypeObject * cls, PyObject * hasher) :
            hashfunc(FastCall(hasher)), dealloc(cls->tp_dealloc) {}

        /**
         * Compute hash for an object using the user's hash function.
         * 
         * The hash function can return:
         *   - None: Use identity hash (_Py_HashPointer) - object's memory address
         *   - int: Use the integer value directly as the hash
         *   - Counter: Get next counter value and spread it across hash space
         * 
         * Note: Python requires hash() to never return -1 (reserved for errors),
         * so we map -1 to 1.
         */
        Py_hash_t hash(PyObject * self) {
            PyObject * result = hashfunc(self);

            if (!result) return -1;  // Exception occurred

            if (result == Py_None) {
                // None means "use identity hash"
                Py_DECREF(result);
                return _Py_HashPointer(self);
            }

            if (PyLong_CheckExact(result)) {
                // Integer returned - use directly
                Py_hash_t hash = PyLong_AsLongLong(result);
                Py_DECREF(result);
                if (hash == -1) hash = 1;  // -1 reserved for errors
                return hash;
            }

            if (Py_TYPE(result) == &Counter_Type) {
                // Counter returned - get next value and spread for uniform distribution
                Py_hash_t hash = (Py_hash_t)spread64(Counter_Next(result));
                Py_DECREF(result);
                if (hash == -1) hash = 1;  // -1 reserved for errors
                return hash;
            }

            PyErr_Format(PyExc_TypeError, "hash function: %S returned object: %S of unexpected type: %S", 
                            hashfunc.callable,
                            result, 
                            Py_TYPE(result));

            return -1;
        }  
    };

    // Global hash cache: object pointer -> cached hash value
    // Ensures hash(obj) always returns the same value for the lifetime of obj
    static map<PyObject *, Py_hash_t> hashes;
    
    // Registry of patched types: type -> Hasher containing hash func and original dealloc
    static map<PyTypeObject *, Hasher> hashers;
           
        /**
         * Find the Hasher for a type, traversing up the inheritance chain if needed.
         * This allows subclasses to inherit patched hash behavior from their base class.
         */
        static Hasher& find_hasher(PyTypeObject * cls) {
            auto it = hashers.find(cls);
            if (it != hashers.end()) return it->second;
            else return find_hasher(cls->tp_base);  // Recurse to base class
        }

    /**
     * The tp_hash implementation installed on patched types.
     * 
     * First checks the cache for a previously computed hash. If not found,
     * computes a new hash using the user's hash function and caches it.
     * Caching is essential for consistency - hash(obj) must always return
     * the same value while obj is alive.
     */
    static Py_hash_t hashfunc(PyObject * self) {
        auto it = hashes.find(self);
        if (it != hashes.end()) return it->second;  // Return cached hash
        else {
            // Compute new hash and cache it
            Py_hash_t hash = find_hasher(Py_TYPE(self)).hash(self);

            if (hash != -1) {
                hashes[self] = hash;
            }
            return hash;
        }
    };

    /**
     * The tp_dealloc implementation installed on patched types.
     *
     * Removes the object's cached hash (if any) before chaining to the
     * original deallocator.
     *
     * Subtype resolution
     * ------------------
     * Py_TYPE(self) is often a *subclass* of the type we patched.  For
     * example, when we patch `object`, a `threading.Thread` instance
     * reaches us because CPython's subtype_dealloc walks tp_base until
     * it finds a dealloc that is not subtype_dealloc — which is our
     * patched_dealloc sitting on `object`.
     *
     * We must therefore walk tp_base ourselves to locate the actual
     * patched type (the one registered in `hashers`) and only swap
     * *that* type's tp_dealloc.  Modifying Py_TYPE(self)->tp_dealloc
     * instead would corrupt the subclass's type struct — setting e.g.
     * Thread->tp_dealloc to object_dealloc and then to patched_dealloc,
     * permanently bypassing subtype_dealloc for all future Thread
     * instances and causing a segfault at interpreter shutdown.
     *
     * Temporary restore
     * -----------------
     * We temporarily restore the original tp_dealloc on the patched
     * type before chaining, because subtype_dealloc reads
     * tp_base->tp_dealloc to decide how to chain.  If it still sees
     * patched_dealloc it re-enters us and infinite-loops.
     */
    static void patched_dealloc(PyObject * self) {
        hashes.erase(self);

        // Find the patched base type — may differ from Py_TYPE(self).
        PyTypeObject* patched_type = Py_TYPE(self);
        while (hashers.find(patched_type) == hashers.end()) {
            patched_type = patched_type->tp_base;
        }
        Hasher& hasher = hashers.find(patched_type)->second;

        // Swap, chain, restore — only on the patched type.
        patched_type->tp_dealloc = hasher.dealloc;
        hasher.dealloc(self);
        patched_type->tp_dealloc = patched_dealloc;
    }

    /**
     * Pre-cache hashes for all existing instances of a type before patching.
     *
     * Uses gc.get_objects() via Python C API calls to iterate all GC-tracked objects.
     * This is critical for correctness: objects already stored in dicts/sets
     * were placed there based on their original hash. If we change the hash
     * function without caching old values, those objects become "lost" -
     * lookups will search the wrong bucket and never find them.
     *
     * Note: PyUnstable_GC_VisitObjects (Python 3.12+) was tried but it doesn't
     * seem to visit all GC objects reliably, so we use gc.get_objects() instead.
     */
    static void cache_existing_instance_hashes(PyTypeObject * cls) {
        PyObject* gc_module = PyImport_ImportModule("gc");
        if (!gc_module) {
            PyErr_Clear();
            return;
        }

        PyObject* get_objects = PyObject_GetAttrString(gc_module, "get_objects");
        Py_DECREF(gc_module);
        if (!get_objects) {
            PyErr_Clear();
            return;
        }

        PyObject* all_objects = PyObject_CallNoArgs(get_objects);
        Py_DECREF(get_objects);
        if (!all_objects) {
            PyErr_Clear();
            return;
        }

        if (PyList_Check(all_objects)) {
            Py_ssize_t size = PyList_GET_SIZE(all_objects);
            for (Py_ssize_t i = 0; i < size; i++) {
                PyObject* obj = PyList_GET_ITEM(all_objects, i);
                
                // Skip null objects or objects with null type (shouldn't happen but be safe)
                if (!obj || !Py_TYPE(obj)) {
                    continue;
                }
                
                // Only process objects that are direct instances of cls
                // Using Py_TYPE(obj) == cls instead of PyObject_TypeCheck to be more precise
                // and avoid issues with objects whose type chain might be inconsistent
                if (Py_TYPE(obj) == cls) {
                    Py_hash_t old_hash = PyObject_Hash(obj);
                    if (old_hash != -1) {
                        hashes[obj] = old_hash;
                    } else {
                        PyErr_Clear();
                    }
                }
            }
        }

        Py_DECREF(all_objects);
    }

    /**
     * Patch a Python type's hash function at the C level.
     * 
     * @param cls      The type to patch (e.g., a class object)
     * @param hashfunc The new hash function to use. Should accept one argument
     *                 (the object) and return int, None, or a Counter object.
     * 
     * After patching:
     *   - cls.tp_hash points to our hashfunc() which calls the user's function
     *   - cls.tp_dealloc points to patched_dealloc() which cleans up before
     *     chaining to the original deallocator
     *   - Subclasses of cls inherit the patched behavior
     * 
     * Safety: Before patching, we walk all existing instances via gc.get_objects()
     * and cache their current hash values. This ensures objects already in
     * dicts/sets remain findable - they keep their original hash.
     * 
     * Note: A type can only be patched once. Attempting to patch an already
     * patched type will trigger an assertion failure.
     */
    void patch_hash(PyTypeObject * cls, PyObject * hashfunc) {

        assert (!hashers.contains(cls));  // Can only patch once

        // CRITICAL: Cache existing instance hashes BEFORE patching
        // Otherwise objects in sets/dicts become unfindable
        cache_existing_instance_hashes(cls);

        Py_INCREF(cls);       // Keep type alive
        Py_INCREF(hashfunc);  // Keep hash function alive

        hashers.emplace(cls, Hasher(cls, hashfunc));

        cls->tp_dealloc = patched_dealloc;
        cls->tp_hash = retracesoftware::hashfunc;
    }
}
