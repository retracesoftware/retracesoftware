"""Validate that make_pathpredicate(verbose=True) and make_pathpredicate(verbose=False)
produce predicates with identical accept/reject behaviour."""

import re
from pathlib import Path

import pytest

from retracesoftware.install.pathpredicate import make_pathpredicate


PATTERNS = [re.compile(p) for p in [
    r"\.txt$",
    r"/tmp/",
    r"^/home/user/data",
]]


@pytest.fixture(params=[True, False], ids=["verbose", "quiet"])
def predicate(request):
    return make_pathpredicate(PATTERNS, verbose=request.param)


class TestVerboseQuietParity:
    """Every input must get the same True/False from both verbose modes."""

    def test_matching_string(self, predicate):
        assert predicate("readme.txt") is True

    def test_matching_path_prefix(self, predicate):
        assert predicate("/tmp/scratch") is True

    def test_matching_anchored(self, predicate):
        assert predicate("/home/user/data/file.csv") is True

    def test_no_match(self, predicate):
        assert predicate("/var/log/syslog") is False

    def test_fd_int(self, predicate):
        assert predicate(3) is True

    def test_fd_zero(self, predicate):
        assert predicate(0) is True

    def test_pathlib(self, predicate):
        assert predicate(Path("/tmp/foo")) is True

    def test_pathlib_no_match(self, predicate):
        assert predicate(Path("/var/log/syslog")) is False


class TestEmptyPatterns:
    """With no patterns, only ints (fds) should retrace."""

    @pytest.fixture(params=[True, False], ids=["verbose", "quiet"])
    def empty_pred(self, request):
        return make_pathpredicate([], verbose=request.param)

    def test_string_always_passthrough(self, empty_pred):
        assert empty_pred("/any/path") is False

    def test_fd_still_retraced(self, empty_pred):
        assert empty_pred(5) is True
