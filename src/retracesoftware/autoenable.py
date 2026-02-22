if __name__ == "__main__":
    import sysconfig
    import pathlib
    import shutil

    source = pathlib.Path(__file__).parent / 'retrace.pth'
    target = pathlib.Path(sysconfig.get_paths()["purelib"]) / 'retrace.pth'
    shutil.copy(source, target)
    print(f'Retrace autoinstall enabled: {source} -> {target}')
else:
    import os

    def _is_running_retrace():
        import sys
        return (len(sys.orig_argv) >= 3
                and sys.orig_argv[1] == '-m'
                and sys.orig_argv[2].startswith('retracesoftware'))

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
                new_argv = [sys.orig_argv[0], '-m', 'retracesoftware']
                new_argv.extend(config_to_argv(config))
                new_argv.append('--')
                new_argv.extend(sys.orig_argv[1:])
                os.execv(sys.executable, new_argv)
