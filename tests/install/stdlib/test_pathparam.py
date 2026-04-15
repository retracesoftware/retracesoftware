"""Test the pathparam directive and is_bound query."""
import _io
import os
import tempfile
import threading

import retracesoftware.utils as utils
from retracesoftware.proxy.contexts import record_context
from retracesoftware.proxy.system import System
from retracesoftware.testing.memorytape import MemoryWriter
from retracesoftware.install.installation import Installation
from retracesoftware.install.patcher import patch


def _make_system():
    system = System()
    system.immutable_types.update({
        int, str, bytes, bool, float, type(None),
        _io.BlockingIOError, _io.UnsupportedOperation,
    })
    return system


def test_open_non_whitelisted_path_not_retraced():
    """open() on a path that doesn't match the predicate should pass through
    directly — the result is a plain _io.TextIOWrapper and nothing is written
    to the tape."""
    system = _make_system()

    def pathpredicate(arg):
        return False

    ns = dict(_io.__dict__)
    spec = {
        'proxy': ['open'],
        'pathparam': {'open': 'file'},
    }
    patch(ns, spec, Installation(system), pathpredicate=pathpredicate)
    patched_open = ns['open']

    writer = MemoryWriter(thread=threading.get_ident)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as tmp:
        tmp.write('hello')
        tmp_path = tmp.name

    try:
        with record_context(system, writer):
            f = patched_open(tmp_path, 'r')
            f.close()

        assert not utils.is_wrapped(f), (
            f"expected plain file object, got wrapped: {type(f)}")
        assert len(writer.tape) == 0, (
            f"expected empty tape, got {len(writer.tape)} events")
    finally:
        os.unlink(tmp_path)


def test_open_whitelisted_path_is_retraced():
    """open() on a path that matches the predicate should go through the
    proxy — the tape should contain recorded events."""
    system = _make_system()

    def pathpredicate(arg):
        return True

    ns = dict(_io.__dict__)
    spec = {
        'proxy': ['open'],
        'pathparam': {'open': 'file'},
    }
    patch(ns, spec, Installation(system), pathpredicate=pathpredicate)
    patched_open = ns['open']

    writer = MemoryWriter(thread=threading.get_ident)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as tmp:
        tmp.write('hello')
        tmp_path = tmp.name

    try:
        with record_context(system, writer):
            f = patched_open(tmp_path, 'r')
            f.close()

        assert len(writer.tape) > 0, "expected events on the tape"
    finally:
        os.unlink(tmp_path)


def test_is_bound_true_for_whitelisted_open():
    """Objects created via a whitelisted open should be bound in the writer."""
    system = _make_system()

    def pathpredicate(arg):
        return True

    ns = dict(_io.__dict__)
    spec = {
        'proxy': ['open'],
        'pathparam': {'open': 'file'},
    }
    patch(ns, spec, Installation(system), pathpredicate=pathpredicate)
    patched_open = ns['open']

    writer = MemoryWriter(thread=threading.get_ident)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as tmp:
        tmp.write('hello')
        tmp_path = tmp.name

    try:
        with record_context(system, writer):
            f = patched_open(tmp_path, 'r')
            assert system.is_bound(f), "file from whitelisted open should be bound"
            f.close()
    finally:
        os.unlink(tmp_path)


def test_is_bound_false_for_non_whitelisted_open():
    """Objects created via a non-whitelisted open should NOT be bound."""
    system = _make_system()

    def pathpredicate(arg):
        return False

    ns = dict(_io.__dict__)
    spec = {
        'proxy': ['open'],
        'pathparam': {'open': 'file'},
    }
    patch(ns, spec, Installation(system), pathpredicate=pathpredicate)
    patched_open = ns['open']

    writer = MemoryWriter(thread=threading.get_ident)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as tmp:
        tmp.write('hello')
        tmp_path = tmp.name

    try:
        with record_context(system, writer):
            f = patched_open(tmp_path, 'r')
            assert not system.is_bound(f), "file from non-whitelisted open should not be bound"
            f.close()
    finally:
        os.unlink(tmp_path)


def test_is_bound_false_outside_context():
    """is_bound should return False when no record context is active."""
    system = _make_system()
    assert not system.is_bound(object()), "is_bound should be False outside context"
