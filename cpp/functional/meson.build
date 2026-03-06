_func_name = '_retracesoftware_functional'
_func_sources = files(
  'advice.cpp',
  'always.cpp',
  'andpredicate.cpp',
  'anyargs.cpp',
  'arity_dispatch.cpp',
  'callall.cpp',
  'casepredicate.cpp',
  'compose.cpp',
  'compose2.cpp',
  'constantly.cpp',
  'deepwrap.cpp',
  'dropargs.cpp',
  'either.cpp',
  'first.cpp',
  'firstof.cpp',
  'functional.cpp',
  'ifthenelse.cpp',
  'indexer.cpp',
  'instancetest.cpp',
  'intercept.cpp',
  'lazy.cpp',
  'manypredicate.cpp',
  'memoize.cpp',
  'methodinvoker.cpp',
  'notpredicate.cpp',
  'orpredicate.cpp',
  'param.cpp',
  'partial.cpp',
  'positional_param.cpp',
  'repeatedly.cpp',
  'repr.cpp',
  'selfapply.cpp',
  'sideeffect.cpp',
  'spread.cpp',
  'ternarypredicate.cpp',
  'transformargs.cpp',
  'typepredicate.cpp',
  'usewith.cpp',
  'vector.cpp',
  'walker.cpp',
  'when.cpp',
  'whennotnone.cpp',
  'whenpredicate.cpp',
)
_func_includes = include_directories('.', '../common/include')
_func_cpp_args = host_machine.system() == 'windows' ? [] : ['-fvisibility=hidden']

_thread_dep = dependency('threads', required: false)
_func_deps = _thread_dep.found() ? [_thread_dep] : []

py.extension_module(
  _func_name + '_release',
  sources: _func_sources,
  install: true,
  cpp_args: _func_cpp_args + ['-DMODULE_NAME=' + _func_name + '_release'],
  include_directories: _func_includes,
  dependencies: _func_deps,
)

_func_debug = py.extension_module(
  _func_name + '_debug',
  sources: _func_sources,
  install: true,
  cpp_args: _func_cpp_args + ['-DMODULE_NAME=' + _func_name + '_debug', '-DDEBUG_BUILD=1', '-g', '-O0'],
  include_directories: _func_includes,
  dependencies: _func_deps,
)

if host_machine.system() == 'darwin'
  _dsymutil = find_program('dsymutil')
  _ext_suffix = py.get_variable('EXT_SUFFIX')
  custom_target(
    _func_name + '_debug_dsym',
    input: _func_debug,
    output: _func_name + '_debug' + _ext_suffix + '.dSYM',
    command: [_dsymutil, '@INPUT@', '-o', '@OUTPUT@'],
    install: true,
    install_dir: py.get_install_dir(),
  )
endif
