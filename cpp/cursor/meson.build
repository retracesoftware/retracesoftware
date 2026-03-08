_cursor_name = '_retracesoftware_cursor'
_cursor_sources = files('watch_state.cpp', 'thread_call_counts.cpp', 'cursor.cpp', 'module.cpp')
_cursor_includes = include_directories('.', '../common/include')
_cursor_cpp_args = host_machine.system() == 'windows' ? [] : ['-fvisibility=hidden']

py.extension_module(
  _cursor_name + '_release',
  sources: _cursor_sources,
  install: true,
  cpp_args: _cursor_cpp_args + ['-DMODULE_NAME=' + _cursor_name + '_release'],
  include_directories: _cursor_includes,
)

_cursor_debug = py.extension_module(
  _cursor_name + '_debug',
  sources: _cursor_sources,
  install: true,
  cpp_args: _cursor_cpp_args + ['-DMODULE_NAME=' + _cursor_name + '_debug', '-DDEBUG_BUILD=1', '-g', '-O0'],
  include_directories: _cursor_includes,
)

if host_machine.system() == 'darwin'
  _dsymutil = find_program('dsymutil')
  _ext_suffix = py.get_variable('EXT_SUFFIX')
  custom_target(
    _cursor_name + '_debug_dsym',
    input: _cursor_debug,
    output: _cursor_name + '_debug' + _ext_suffix + '.dSYM',
    command: [_dsymutil, '@INPUT@', '-o', '@OUTPUT@'],
    install: true,
    install_dir: py.get_install_dir(),
  )
endif
