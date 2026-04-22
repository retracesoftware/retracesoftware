import sys

import pytest

from retracesoftware.install import Recording, ReplayDivergence, install_retrace
from retracesoftware.install.monitoring import install_monitoring, suppress_monitoring
from retracesoftware.proxy.io import recorder, replayer
from retracesoftware.testing.memorytape import IOMemoryTape

_RUNNER_SETTINGS_ATTR = "__retrace_runner_settings__"


def _core_run_matrix():
    matrix = (
        {"name": "default"},
        {"name": "debug", "debug": True},
        {"name": "stacktraces", "stacktraces": True},
    )
    if sys.version_info >= (3, 12):
        matrix += ({"name": "monitor", "monitor": 1},)
    return matrix


CORE_RUN_MATRIX = _core_run_matrix()
DEFAULT_RUN_MATRIX = CORE_RUN_MATRIX
RUN_MATRIX_PRESETS = {
    "core": CORE_RUN_MATRIX,
}


def _normalize_matrix(matrix):
    normalized = []
    for index, lane in enumerate(matrix):
        lane = dict(lane)
        lane.setdefault("name", f"lane{index}")
        normalized.append(lane)
    return tuple(normalized)


def _resolve_matrix(matrix):
    if matrix is None:
        matrix = DEFAULT_RUN_MATRIX
    elif isinstance(matrix, str):
        try:
            matrix = RUN_MATRIX_PRESETS[matrix]
        except KeyError as exc:
            known = ", ".join(sorted(RUN_MATRIX_PRESETS))
            raise ValueError(f"unknown matrix preset {matrix!r}; expected one of: {known}") from exc
    return _normalize_matrix(matrix)

def _drain_reader(reader):
    items = []
    while True:
        try:
            items.append(reader.read())
        except StopIteration:
            return items

class Runner:
    @staticmethod
    def settings(
        *,
        configure_system=None,
        debug=None,
        stacktraces=None,
        monitor=None,
        matrix=None,
        patch=None,
    ):
        settings = {}
        if configure_system is not None:
            settings["configure_system"] = configure_system
        if debug is not None:
            settings["debug"] = debug
        if stacktraces is not None:
            settings["stacktraces"] = stacktraces
        if monitor is not None:
            settings["monitor"] = monitor
        if matrix is not None:
            settings["matrix"] = _resolve_matrix(matrix)
        if patch is not None:
            settings["patch"] = tuple(patch)

        def decorate(fn):
            merged = dict(getattr(fn, _RUNNER_SETTINGS_ATTR, {}))
            if "patch" in settings:
                merged["patch"] = tuple(merged.get("patch", ())) + tuple(settings["patch"])
            merged.update({k: v for k, v in settings.items() if k != "patch"})
            setattr(fn, _RUNNER_SETTINGS_ATTR, merged)
            return fn

        return decorate

    def __init__(
        self,
        *,
        configure_system=None,
        debug=False,
        stacktraces=False,
        monitor=0,
        matrix=None,
    ):
        self._configure_system = configure_system
        self._debug = debug
        self._stacktraces = stacktraces
        self._monitor = monitor
        self._matrix = _resolve_matrix(matrix)

    def _settings_for(self, fn, overrides=None):
        settings = {
            "configure_system": self._configure_system,
            "debug": self._debug,
            "stacktraces": self._stacktraces,
            "monitor": self._monitor,
            "matrix": self._matrix,
            "patch": (),
        }
        fn_settings = getattr(fn, _RUNNER_SETTINGS_ATTR, {})
        settings.update({k: v for k, v in fn_settings.items() if k != "patch"})
        if "patch" in fn_settings:
            settings["patch"] = tuple(settings["patch"]) + tuple(fn_settings["patch"])
        if overrides is not None:
            settings.update(overrides)
        return settings

    @staticmethod
    def _apply_settings(system, settings):
        if settings["configure_system"] is not None:
            settings["configure_system"](system)
        for obj in settings["patch"]:
            if isinstance(obj, type):
                system.patch_type(obj)
            else:
                system.patch(obj)

    def _record_with_settings(self, settings, fn, *args, **kwargs):
        tape = IOMemoryTape()
        writer = tape.writer()
        system = recorder(
            writer=writer.write,
            debug=settings["debug"],
            stacktraces=settings["stacktraces"],
        )
        self._apply_settings(system, settings)

        result = None
        error = None

        def write_monitor(value):
            with suppress_monitoring():
                writer.monitor_event(value)

        write_monitor_disabled = system.disable_for(write_monitor)

        def checkpoint_monitor(value):
            if system._in_sandbox():
                write_monitor_disabled(value)

        uninstall = install_retrace(
            system=system,
            retrace_file_patterns=None,
            monitor_level=0,
            verbose=False,
            retrace_shutdown=False,
        )
        uninstall_monitor = (
            install_monitoring(
                checkpoint_monitor,
                settings["monitor"],
            )
            if settings["monitor"] > 0
            else None
        )

        try:
            try:
                result = system.run(fn, *args, **kwargs)
            except Exception as exc:
                error = exc
        finally:
            if uninstall_monitor is not None:
                uninstall_monitor()
            uninstall()
            system.unpatch_types()

        return Recording(list(tape.tape), result, error)

    def record(self, fn, *args, **kwargs):
        settings = self._settings_for(fn)
        return self._record_with_settings(settings, fn, *args, **kwargs)

    def _replay_with_settings(self, settings, recording, fn, *args, **kwargs):
        tape = IOMemoryTape(recording.tape)
        reader = tape.reader()

        def on_unexpected(key):
            raise ReplayDivergence(
                f"unexpected message during replay: {key!r}",
                tape=recording.tape,
            )

        def on_desync(record, replay):
            raise ReplayDivergence(
                f"Checkpoint difference: {record!r} was expecting {replay!r}",
                tape=recording.tape,
            )

        system = replayer(
            next_object=reader.read,
            close=getattr(reader, "close", None),
            on_unexpected=on_unexpected,
            on_desync=on_desync,
            debug=settings["debug"],
            stacktraces=settings["stacktraces"],
        )
        self._apply_settings(system, settings)

        def verify_monitor(value):
            with suppress_monitoring():
                reader.monitor_checkpoint(value)

        verify_monitor_disabled = system.disable_for(verify_monitor)

        def checkpoint_monitor(value):
            if system._in_sandbox():
                verify_monitor_disabled(value)

        uninstall = install_retrace(
            system=system,
            retrace_file_patterns=None,
            monitor_level=0,
            verbose=False,
            retrace_shutdown=False,
        )
        uninstall_monitor = (
            install_monitoring(
                checkpoint_monitor,
                settings["monitor"],
            )
            if settings["monitor"] > 0
            else None
        )

        try:
            try:
                replay_result = system.run(fn, *args, **kwargs)
            except ReplayDivergence:
                raise
            except Exception as exc:
                if recording.error is None:
                    raise ReplayDivergence(
                        f"replay raised {type(exc).__name__} but record succeeded",
                        tape=recording.tape,
                        ) from exc
                raise recording.error
        finally:
            if uninstall_monitor is not None:
                uninstall_monitor()
            uninstall()
            system.unpatch_types()

        if recording.error is not None:
            raise ReplayDivergence(
                f"record raised {type(recording.error).__name__} but replay succeeded",
                tape=recording.tape,
            )

        if replay_result != recording.result:
            raise ReplayDivergence(
                f"return value divergence: record returned {recording.result!r}, "
                f"replay returned {replay_result!r}",
                tape=recording.tape,
            )

        try:
            remaining = _drain_reader(reader)
        except Exception as exc:
            raise ReplayDivergence(
                f"tape drain divergence: {exc}",
                tape=recording.tape,
            ) from exc
        if remaining:
            raise ReplayDivergence(
                f"tape has {len(remaining)} unconsumed entries "
                f"(replay consumed fewer events than record produced)",
                tape=recording.tape,
            )

        return recording.result

    def replay(self, recording, fn, *args, **kwargs):
        settings = self._settings_for(fn)
        return self._replay_with_settings(settings, recording, fn, *args, **kwargs)

    def _run_once_with_settings(self, settings, fn, *args, **kwargs):
        recording = self._record_with_settings(settings, fn, *args, **kwargs)

        try:
            return self._replay_with_settings(settings, recording, fn, *args, **kwargs)
        except ReplayDivergence as exc:
            diagnostic_settings = self._settings_for(
                fn,
                overrides={"debug": True, "stacktraces": True},
            )
            if (
                diagnostic_settings["debug"] == settings["debug"]
                and diagnostic_settings["stacktraces"] == settings["stacktraces"]
            ):
                raise

            try:
                diagnostic_recording = self._record_with_settings(
                    diagnostic_settings, fn, *args, **kwargs
                )
                self._replay_with_settings(
                    diagnostic_settings, diagnostic_recording, fn, *args, **kwargs
                )
            except ReplayDivergence as diagnostic_exc:
                diagnostic_exc.add_note(
                    "Automatic diagnostic rerun enabled debug=True and stacktraces=True."
                )
                diagnostic_exc.add_note(f"Initial divergence: {exc}")
                raise diagnostic_exc from exc
            except Exception as diagnostic_exc:
                exc.add_note(
                    "Automatic diagnostic rerun failed before producing a richer divergence."
                )
                exc.add_note(
                    f"Diagnostic rerun error: {type(diagnostic_exc).__name__}: {diagnostic_exc}"
                )
                raise

            exc.add_note(
                "Automatic diagnostic rerun with debug=True and stacktraces=True completed "
                "without reproducing the divergence."
            )
            raise

    def run(self, fn, *args, **kwargs):
        settings = self._settings_for(fn)
        result = None

        for lane in settings["matrix"]:
            lane_name = lane["name"]
            lane_overrides = {k: v for k, v in lane.items() if k != "name"}
            lane_settings = self._settings_for(fn, overrides=lane_overrides)
            try:
                lane_result = self._run_once_with_settings(
                    lane_settings,
                    fn,
                    *args,
                    **kwargs,
                )
            except Exception as exc:
                exc.add_note(f"Matrix lane: {lane_name}")
                raise

            if result is None:
                result = lane_result

        return result


def retrace_test(_fn=None, **settings):
    def decorate(fn):
        decorated = Runner.settings(**settings)(fn)
        return pytest.mark.retrace_test(decorated)

    if _fn is None:
        return decorate
    return decorate(_fn)
