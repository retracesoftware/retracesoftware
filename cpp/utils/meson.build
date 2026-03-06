_utils_name = '_retracesoftware_utils'
_utils_sources = files(
  'counter.cpp',
  'cursor.cpp',
  'demux.cpp',
  'dictintercept.cpp',
  'dispatcher.cpp',
  'fastset.cpp',
  'frameeval.cpp',
  'gate.cpp',
  'gccollectpred.cpp',
  'iddict.cpp',
  'idset.cpp',
  'instancecheck.cpp',
  'marker.cpp',
  'memoryaddresses.cpp',
  'module.cpp',
  'newwrapper.cpp',
  'observer.cpp',
  'onalloc.cpp',
  'patchhash.cpp',
  'perthread.cpp',
  'proxy.cpp',
  'reference.cpp',
  'runall.cpp',
  'stableset.cpp',
  'stack.cpp',
  'striptraceback.cpp',
  'threadcontext.cpp',
  'threadlocal.cpp',
  'threadstate.cpp',
  'threadswitchmonitor.cpp',
  'visitor.cpp',
  'wrapped.cpp',
  'wrappedfunction.cpp',
  'wrappedmember.cpp',
)

_utils_extra_c = files('generation.c', 'gilwatch_activate.c')
_utils_includes = include_directories('.', '../common/include')
_utils_cpp_args = host_machine.system() == 'windows' ? [] : ['-fvisibility=hidden']

_thread_dep = dependency('threads', required: false)
_utils_deps = _thread_dep.found() ? [_thread_dep] : []

py.extension_module(
  _utils_name + '_release',
  sources: _utils_sources + _utils_extra_c,
  install: true,
  cpp_args: _utils_cpp_args + ['-DMODULE_NAME=' + _utils_name + '_release'],
  include_directories: _utils_includes,
  dependencies: _utils_deps,
)

_utils_debug = py.extension_module(
  _utils_name + '_debug',
  sources: _utils_sources + _utils_extra_c,
  install: true,
  cpp_args: _utils_cpp_args + ['-DMODULE_NAME=' + _utils_name + '_debug', '-DDEBUG_BUILD=1', '-g', '-O0'],
  include_directories: _utils_includes,
  dependencies: _utils_deps,
)

if host_machine.system() == 'darwin'
  _dsymutil = find_program('dsymutil')
  _ext_suffix = py.get_variable('EXT_SUFFIX')
  custom_target(
    _utils_name + '_debug_dsym',
    input: _utils_debug,
    output: _utils_name + '_debug' + _ext_suffix + '.dSYM',
    command: [_dsymutil, '@INPUT@', '-o', '@OUTPUT@'],
    install: true,
    install_dir: py.get_install_dir(),
  )
endif

# gilwatch — preload library for detecting GIL thread switches.
# Plain C, no Python dependency. Loaded via LD_PRELOAD / DYLD_INSERT_LIBRARIES.
_gilwatch_deps = [dependency('threads')]
if host_machine.system() != 'darwin'
  _gilwatch_deps += [meson.get_compiler('c').find_library('dl')]
endif

shared_library(
  'gilwatch',
  files('gilwatch.c'),
  dependencies: _gilwatch_deps,
  install: true,
  install_dir: py.get_install_dir(),
  c_args: ['-fvisibility=default'],
)
