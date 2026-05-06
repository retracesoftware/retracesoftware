from retracesoftware.control_runtime import _find_user_frame


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
