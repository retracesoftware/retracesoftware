_stream_name = '_retracesoftware_stream'
_stream_sources = files(
  'bindingref.cpp',
  'framed_writer.cpp',
  'module.cpp',
  'objectstream.cpp',
  'objectwriter.cpp',
  'persister.cpp',
  'queue.cpp',
  'search.cpp',
  'tapereader.cpp',
)
_stream_includes = include_directories('.', '../common/include', 'vendor')
_stream_cpp_args = host_machine.system() == 'windows' ? [] : ['-fvisibility=hidden']

_thread_dep = dependency('threads', required: false)
_stream_deps = _thread_dep.found() ? [_thread_dep] : []

py.extension_module(
  _stream_name + '_release',
  sources: _stream_sources,
  install: true,
  cpp_args: _stream_cpp_args + ['-DMODULE_NAME=' + _stream_name + '_release'],
  include_directories: _stream_includes,
  dependencies: _stream_deps,
)

_stream_debug = py.extension_module(
  _stream_name + '_debug',
  sources: _stream_sources,
  install: true,
  cpp_args: _stream_cpp_args + ['-DMODULE_NAME=' + _stream_name + '_debug', '-DDEBUG_BUILD=1', '-g', '-O0'],
  include_directories: _stream_includes,
  dependencies: _stream_deps,
)

if host_machine.system() == 'darwin'
  _dsymutil = find_program('dsymutil')
  _ext_suffix = py.get_variable('EXT_SUFFIX')
  custom_target(
    _stream_name + '_debug_dsym',
    input: _stream_debug,
    output: _stream_name + '_debug' + _ext_suffix + '.dSYM',
    command: [_dsymutil, '@INPUT@', '-o', '@OUTPUT@'],
    install: true,
    install_dir: py.get_install_dir(),
  )
endif
