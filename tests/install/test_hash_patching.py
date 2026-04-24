import types

from retracesoftware.install import patcher


class _FakeSystem:
    def __init__(self):
        self.mode = "disabled"
        self.dispatch_args = None

    def create_dispatch(self, *, disabled, external, internal):
        self.dispatch_args = {
            "disabled": disabled,
            "external": external,
            "internal": internal,
        }

        def dispatch(*args, **kwargs):
            return self.dispatch_args[self.mode](*args, **kwargs)

        return dispatch


def test_install_hash_patching_dispatches_by_phase_and_patches_target_types(monkeypatch):
    system = _FakeSystem()
    patch_calls = []

    def fake_patch_hashes(hashfunc, *classes):
        patch_calls.append((hashfunc, classes))

    monkeypatch.setattr(patcher.utils, "patch_hashes", fake_patch_hashes)

    uninstall = patcher.install_hash_patching(system)

    assert system.dispatch_args is not None
    assert len(patch_calls) == 2
    assert patch_calls[0][1] == (object,)
    assert patch_calls[1][1] == (types.FunctionType,)

    hashfunc = patch_calls[0][0]
    assert patch_calls[1][0] is hashfunc

    probe = object()

    system.mode = "disabled"
    assert hashfunc(probe) is None

    system.mode = "external"
    external_first = hashfunc(probe)
    external_second = hashfunc(probe)
    assert isinstance(external_first, int)
    assert isinstance(external_second, int)
    assert external_first != external_second

    system.mode = "internal"
    internal_first = hashfunc(probe)
    internal_second = hashfunc(probe)
    assert isinstance(internal_first, int)
    assert isinstance(internal_second, int)
    assert internal_first != internal_second

    uninstall()

    assert len(patch_calls) == 3
    uninstall_hashfunc, uninstall_classes = patch_calls[2]
    assert uninstall_classes == (object, types.FunctionType)
    assert uninstall_hashfunc(probe) is None
