if __name__ == "__main__":
    import os
    import stat
    import sysconfig
    import pathlib
    import shutil

    source = pathlib.Path(__file__).parent / 'retracesoftware_autoenable.pth'
    target = pathlib.Path(sysconfig.get_paths()["purelib"]) / 'retracesoftware_autoenable.pth'
    shutil.copy(source, target)

    # On macOS/BSD, overwriting a previously hidden .pth can preserve UF_HIDDEN.
    if hasattr(os, "chflags") and hasattr(stat, "UF_HIDDEN"):
        try:
            flags = os.stat(target).st_flags
            if flags & stat.UF_HIDDEN:
                os.chflags(target, flags & ~stat.UF_HIDDEN)
        except OSError:
            pass

    print(f'Retrace autoinstall enabled: {source} -> {target}')
else:
    import os

    _RETRACE_PYTHON_REEXECED = "RETRACE_PYTHON_REEXECED"

    def _is_running_retrace():
        import sys
        return (len(sys.orig_argv) >= 3
                and sys.orig_argv[1] == '-m'
                and sys.orig_argv[2].startswith('retracesoftware'))

    def _has_retrace():
        try:
            import retrace  # noqa: F401
        except ImportError:
            return False
        return True

    def _python_version():
        import sys
        return ".".join(str(part) for part in sys.version_info[:3])

    def _python_home():
        import sys
        prefix = getattr(sys, "base_prefix", sys.prefix)
        exec_prefix = getattr(sys, "base_exec_prefix", sys.exec_prefix)
        if exec_prefix != prefix:
            return os.pathsep.join((prefix, exec_prefix))
        return prefix

    def _python_path():
        import sys
        paths = []
        for path in sys.path:
            if path not in paths:
                paths.append(path)
        return os.pathsep.join(paths)

    def _patched_python():
        import sys
        try:
            import retracesoftware_cpython
        except ImportError as exc:
            print(
                "retracesoftware requires retracesoftware-cpython to auto-enable retrace-python",
                file=sys.stderr,
            )
            raise SystemExit(1) from exc

        expected = _python_version()
        actual = getattr(retracesoftware_cpython, "__cpython_version__", None)
        if actual != expected:
            print(
                "retracesoftware-cpython does not match this Python: "
                f"expected {expected}, got {actual or 'unknown'}",
                file=sys.stderr,
            )
            raise SystemExit(1)

        executable = retracesoftware_cpython.executable()
        if not os.path.isfile(executable):
            print(f"retracesoftware-cpython executable not found: {executable}", file=sys.stderr)
            raise SystemExit(1)
        return executable

    def _retrace_env():
        import sys
        env = os.environ.copy()
        env[_RETRACE_PYTHON_REEXECED] = "1"
        env["RETRACE_ORIGINAL_PYTHON"] = sys.executable
        env["PYTHONHOME"] = _python_home()
        env["PYTHONPATH"] = _python_path()
        return env

    def _exec_retrace_python(argv):
        import sys
        if _has_retrace():
            executable = sys.executable
            env = os.environ.copy()
        else:
            if os.environ.get(_RETRACE_PYTHON_REEXECED):
                print("patched retrace-python did not load retrace", file=sys.stderr)
                raise SystemExit(1)
            executable = _patched_python()
            env = _retrace_env()
        os.execvpe(executable, [executable, *argv[1:]], env)

    def _script_stem():
        """Derive the base script name (no extension) from sys.orig_argv."""
        import sys
        args = sys.orig_argv[1:]
        if '-m' in args:
            idx = args.index('-m')
            if idx + 1 < len(args):
                return args[idx + 1]
        for arg in args:
            if not arg.startswith('-'):
                return os.path.splitext(os.path.basename(arg))[0]
        return 'recording'

    def _prepare_trace_file(path):
        """If root retrace process, truncate trace file and add shebang."""
        try:
            existing_inode = str(os.stat(path).st_ino)
        except FileNotFoundError:
            existing_inode = None

        if os.environ.get('RETRACE_INODE') == existing_inode and existing_inode is not None:
            return  # child process, file already prepared by root

        from retracesoftware.replay import extract_binary_path
        extract_bin = extract_binary_path()
        shebang = f'#!{extract_bin}\n'

        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(shebang.encode())
        os.chmod(path, 0o755)
        os.environ['RETRACE_INODE'] = str(os.stat(path).st_ino)

    if 'RETRACE_RECORDING' in os.environ or 'RETRACE_CONFIG' in os.environ:
        import sys

        if not _is_running_retrace():
            from retracesoftware.install.config import load_retrace_config, config_to_argv

            config = load_retrace_config()

            if 'RETRACE_RECORDING' in os.environ:
                config.setdefault("record", {})["recording"] = os.environ['RETRACE_RECORDING']

            if "recording" not in config.get("record", {}):
                pass  # no recording path, nothing to do
            else:
                recording = config["record"]["recording"]
                if recording != "disable":
                    if '{script}' in recording:
                        recording = recording.format(script=_script_stem())
                        config["record"]["recording"] = recording
                    _prepare_trace_file(recording)

                if os.environ.get('RETRACE_GILWATCH', '0').strip().lower() in ('1', 'true', 'yes', 'on'):
                    try:
                        from retracesoftware.utils import gilwatch_library_path
                        gilwatch = gilwatch_library_path()
                        if gilwatch:
                            if sys.platform == 'darwin':
                                os.environ['DYLD_INSERT_LIBRARIES'] = gilwatch
                            else:
                                os.environ['LD_PRELOAD'] = gilwatch
                    except Exception:
                        pass

                new_argv = [sys.orig_argv[0], '-m', 'retracesoftware']
                new_argv.extend(config_to_argv(config))
                new_argv.append('--')
                new_argv.extend(sys.orig_argv[1:])
                _exec_retrace_python(new_argv)
