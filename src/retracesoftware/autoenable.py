if __name__ == "__main__":
    import sysconfig
    import pathlib
    import shutil

    # Source: retrace.pth bundled with this package
    source = pathlib.Path(__file__).parent / 'retrace.pth'
    
    # Target: site-packages root
    target = pathlib.Path(sysconfig.get_paths()["purelib"]) / 'retrace.pth'
    
    shutil.copy(source, target)

    print(f'Retrace autoinstall enabled: {source} -> {target}')
else:
    import os

    def is_true(name):
        if name in os.environ:
            return os.environ[name].lower() in {'true', '1', 't', 'y', 'yes'}
        else:
            return False
            
    def is_running_retrace():
        return sys.orig_argv[1] == '-m' and sys.orig_argv[2].startswith('retracesoftware')
    
    # only do anything is the RETRACE env variable is set
    if is_true('RETRACE'):
        import sys

        if not is_running_retrace():
            
            new_argv = [sys.orig_argv[0], '-m', 'retracesoftware']

            if is_true('RETRACE_VERBOSE'):
                new_argv.append('--verbose')

            if 'RETRACE_RECORDING_PATH' in os.environ:
                new_argv.append('--recording')
                new_argv.append(os.environ['RETRACE_RECORDING_PATH'])

            if is_true('RETRACE_STACKTRACES'):
                new_argv.append('--stacktraces')

            if is_true('RETRACE_SHUTDOWN'):
                new_argv.append('--trace_shutdown')

            if is_true('RETRACE_TRACE_INPUTS'):
                new_argv.append('--trace_inputs')

            if 'RETRACE_WORKSPACE_PATH' in os.environ:
                new_argv.append('--workspace')
                new_argv.append(os.environ['RETRACE_WORKSPACE_PATH'])

            if 'RETRACE_WRITE_TIMEOUT' in os.environ:
                new_argv.append('--write_timeout')
                new_argv.append(os.environ['RETRACE_WRITE_TIMEOUT'])

            new_argv.append('--')
            new_argv.extend(sys.orig_argv[1:])
            
            os.execv(sys.executable, new_argv)
