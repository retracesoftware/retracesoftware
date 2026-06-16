from __future__ import annotations

from retracesoftware.install.edgecases import pytest_rerunfailures_configure


class DummySystem:
    def disable_for(self, target, *, unwrap_args=True):
        return target


class DummyPluginManager:
    def __init__(self, plugins: set[str]):
        self.plugins = plugins

    def hasplugin(self, name):
        return name in self.plugins


class DummyOptions:
    numprocesses = None


class DummyConfig:
    def __init__(self, *, plugins: set[str], numprocesses=None):
        self.pluginmanager = DummyPluginManager(plugins)
        self.option = DummyOptions()
        self.option.numprocesses = numprocesses


def test_pytest_rerunfailures_configure_hides_inactive_xdist_plugin():
    seen = []

    def target(config):
        seen.append(config.pluginmanager.hasplugin("xdist"))

    config = DummyConfig(plugins={"xdist"}, numprocesses=None)
    wrapped = pytest_rerunfailures_configure(target, DummySystem())

    wrapped(config)

    assert seen == [False]
    assert config.pluginmanager.hasplugin("xdist") is True


def test_pytest_rerunfailures_configure_preserves_active_xdist_plugin():
    seen = []

    def target(config):
        seen.append(config.pluginmanager.hasplugin("xdist"))

    config = DummyConfig(plugins={"xdist"}, numprocesses=2)
    wrapped = pytest_rerunfailures_configure(target, DummySystem())

    wrapped(config)

    assert seen == [True]
