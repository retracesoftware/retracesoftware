if __name__ == "__main__":
    import sysconfig
    import pathlib
    import shutil

    source = pathlib.Path(__file__).parent / 'retracesoftware_autoenable.pth'
    target = pathlib.Path(sysconfig.get_paths()["purelib"]) / 'retracesoftware_autoenable.pth'
    shutil.copy(source, target)
    print(f'Retrace autoinstall enabled: {source} -> {target}')
else:
    import os

    def _is_running_retrace():
        import sys
        return (len(sys.orig_argv) >= 3
                and sys.orig_argv[1] == '-m'
                and sys.orig_argv[2].startswith('retracesoftware'))

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
                os.execv(sys.executable, new_argv)
