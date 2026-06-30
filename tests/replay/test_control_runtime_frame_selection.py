from retracesoftware.control_runtime import Controller, _find_user_frame


def test_find_user_frame_keeps_user_path_containing_retracesoftware():
    filename = "/tmp/user-retracesoftware-project/app.py"
    namespace = {"_find_user_frame": _find_user_frame}
    exec(
        compile(
            "def target():\n"
            "    return _find_user_frame()\n",
            filename,
            "exec",
        ),
        namespace,
    )

    frame = namespace["target"]()

    assert frame is not None
    assert frame.f_code.co_filename == filename


def test_on_breakpoint_hit_preserves_actual_hit_frame_when_no_application_frame():
    captured: list[object] = []

    class Loop:
        def send(self, value):
            return None

    class FakeController(Controller):
        pass

    controller = FakeController.__new__(FakeController)
    controller._done = False
    controller._stopped_frame = None
    controller._stopped_cursor_snapshot = lambda: {"thread_id": 1, "function_counts": [1]}
    controller._event_loop_lock = __import__("threading").Lock()
    controller.event_loop = Loop()
    controller._handle_intent = lambda intent: None

    namespace = {"controller": controller, "captured": captured}
    site_packages = "/tmp/project/.venv/lib/python3.11/site-packages/_pytest/config/__init__.py"
    exec(
        compile(
            "def pytest_internal():\n"
            "    frame = __import__('sys')._getframe()\n"
            "    controller._on_breakpoint_hit(frame)\n"
            "    captured.append(controller._stopped_frame)\n",
            site_packages,
            "exec",
        ),
        namespace,
    )
    namespace["pytest_internal"]()

    assert namespace["captured"][0] is not None
    assert namespace["captured"][0].f_code.co_filename == site_packages
