import pytest

import retracesoftware.functional as fn


def test_transform_call_transforms_args_kwargs_and_result():
    seen = []

    def target(*args, **kwargs):
        seen.append((args, kwargs))
        return 5

    wrapped = fn.transform_call(
        target,
        lambda value: f"first:{value}",
        lambda value: f"second:{value}",
        rest_transform=lambda value: f"rest:{value}",
        result_transform=lambda result: result + 1,
    )

    assert wrapped("a", "b", "c", value="d") == 6
    assert seen == [(("first:a", "second:b", "rest:c"), {"value": "rest:d"})]


def test_transform_call_replaces_exception():
    def target(value):
        raise ValueError(value)

    def transform_error(exc_type, exc_value, exc_tb):
        assert exc_type is ValueError
        assert isinstance(exc_value, ValueError)
        assert exc_tb is not None
        return RuntimeError(f"wrapped {exc_value}")

    wrapped = fn.transform_call(
        target,
        rest_transform=lambda value: value,
        result_transform=lambda result: result,
        error_transform=transform_error,
    )

    with pytest.raises(RuntimeError, match="wrapped boom"):
        wrapped("boom")


def test_transform_call_on_error_preserves_original_exception():
    seen = []

    def target():
        raise ValueError("boom")

    def on_error(exc_type, exc_value, exc_tb):
        seen.append((exc_type, exc_value, exc_tb))

    wrapped = fn.transform_call(
        target,
        rest_transform=lambda value: value,
        result_transform=lambda result: result,
        on_error=on_error,
    )

    with pytest.raises(ValueError, match="boom") as raised:
        wrapped()

    assert len(seen) == 1
    assert seen[0][0] is ValueError
    assert seen[0][1] is raised.value
    assert seen[0][2] is not None


def test_transform_call_propagates_original_exception_without_error_transform():
    def target():
        raise ValueError("boom")

    wrapped = fn.transform_call(
        target,
        rest_transform=lambda value: value,
        result_transform=lambda result: result,
    )

    with pytest.raises(ValueError, match="boom"):
        wrapped()


def test_transform_call_requires_error_transform_to_return_exception():
    def target():
        raise ValueError("boom")

    wrapped = fn.transform_call(
        target,
        rest_transform=lambda value: value,
        result_transform=lambda result: result,
        error_transform=lambda exc_type, exc_value, exc_tb: "not an exception",
    )

    with pytest.raises(TypeError, match="BaseException"):
        wrapped()
