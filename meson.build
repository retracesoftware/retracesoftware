project(
  'retracesoftware',
  ['cpp', 'c'],
  version: run_command('python', '-m', 'setuptools_scm', check: true, capture: true).stdout().strip(),
  default_options: ['cpp_std=c++20', 'strip=false'])

py = import('python').find_installation(pure: false)

# ── Python sources ──────────────────────────────────────────────────────
#
# meson-python installs these into the platlib site-packages directory.
# preserve_path: true keeps the src/retracesoftware/... directory layout,
# but we strip the leading 'src/' via subdir so the installed path is
# just retracesoftware/...

_py_root = 'src/retracesoftware'

# Core package
py.install_sources(
  _py_root / '__init__.py',
  _py_root / '__main__.py',
  _py_root / 'autoenable.py',
  _py_root / 'breakpoint.py',
  _py_root / 'control_runtime.py',
  _py_root / 'cursor.py',
  _py_root / 'exceptions.py',
  _py_root / 'replay_protocol.py',
  _py_root / 'run.py',
  _py_root / 'search.py',
  subdir: 'retracesoftware',
)

# Data files in the core package
py.install_sources(
  _py_root / 'config.json',
  _py_root / 'debug.toml',
  _py_root / 'release.toml',
  _py_root / 'preload.txt',
  _py_root / 'retracesoftware_autoenable.pth',
  _py_root / 'retrace_patterns.txt',
  subdir: 'retracesoftware',
)

# functional
py.install_sources(
  _py_root / 'functional/__init__.py',
  _py_root / 'functional/_pure.py',
  subdir: 'retracesoftware/functional',
)

# utils
py.install_sources(
  _py_root / 'utils/__init__.py',
  _py_root / 'utils/breakpoint.py',
  _py_root / 'utils/trace.py',
  subdir: 'retracesoftware/utils',
)

# stream
py.install_sources(
  _py_root / 'stream/__init__.py',
  _py_root / 'stream/reader.py',
  subdir: 'retracesoftware/stream',
)

# proxy
py.install_sources(
  _py_root / 'proxy/__init__.py',
  _py_root / 'proxy/_binding_checkpoint.py',
  _py_root / 'proxy/_system_patching.py',
  _py_root / 'proxy/_system_threading.py',
  _py_root / 'proxy/globalref.py',
  _py_root / 'proxy/io.py',
  _py_root / 'proxy/protocol.py',
  _py_root / 'proxy/proxyfactory.py',
  _py_root / 'proxy/proxytype.py',
  _py_root / 'proxy/serializer.py',
  _py_root / 'proxy/startthread.py',
  _py_root / 'proxy/stubfactory.py',
  _py_root / 'proxy/system.py',
  _py_root / 'proxy/tape.py',
  _py_root / 'proxy/typeutils.py',
  subdir: 'retracesoftware/proxy',
)

# install
py.install_sources(
  _py_root / 'install/__init__.py',
  _py_root / 'install/config.py',
  _py_root / 'install/edgecases.py',
  _py_root / 'install/globals.py',
  _py_root / 'install/hooks.py',
  _py_root / 'install/importhook.py',
  _py_root / 'install/installation.py',
  _py_root / 'install/monitoring.py',
  _py_root / 'install/patcher.py',
  _py_root / 'install/patchfindspec.py',
  _py_root / 'install/pathpredicate.py',
  _py_root / 'install/replace.py',
  _py_root / 'install/session.py',
  _py_root / 'install/startthread.py',
  subdir: 'retracesoftware/install',
)

# modules (TOML config files for module patching)
install_data(
  _py_root / 'modules/__init__.py',
  _py_root / 'modules/MySQLdb._mysql.toml',
  _py_root / 'modules/PIL._imaging.toml',
  _py_root / 'modules/_io.toml',
  _py_root / 'modules/_sqlite3.toml',
  _py_root / 'modules/confluent_kafka.cimpl.toml',
  _py_root / 'modules/debuggers.toml',
  _py_root / 'modules/grpc._cython.cygrpc.toml',
  _py_root / 'modules/mmap.toml',
  _py_root / 'modules/numpy.random.toml',
  _py_root / 'modules/psycopg2._psycopg.toml',
  _py_root / 'modules/psycopg_c._psycopg.toml',
  _py_root / 'modules/pycurl.toml',
  _py_root / 'modules/pylibmc._pylibmc.toml',
  _py_root / 'modules/pymssql._pymssql.toml',
  _py_root / 'modules/resource.toml',
  _py_root / 'modules/stdlib.toml',
  _py_root / 'modules/syslog.toml',
  install_dir: py.get_install_dir() / 'retracesoftware/modules',
)

# protocol
py.install_sources(
  _py_root / 'protocol/__init__.py',
  _py_root / 'protocol/messages.py',
  _py_root / 'protocol/normalize.py',
  _py_root / 'protocol/record.py',
  _py_root / 'protocol/replay.py',
  subdir: 'retracesoftware/protocol',
)

# testing
py.install_sources(
  _py_root / 'testing/__init__.py',
  _py_root / 'testing/memorytape.py',
  subdir: 'retracesoftware/testing',
)

# threadid
py.install_sources(
  _py_root / 'threadid/__init__.py',
  subdir: 'retracesoftware/threadid',
)

# replay (Python wrapper)
py.install_sources(
  _py_root / 'replay/__init__.py',
  _py_root / 'replay/__main__.py',
  subdir: 'retracesoftware/replay',
)

# dap
py.install_sources(
  _py_root / 'dap/__init__.py',
  _py_root / 'dap/adapter.py',
  subdir: 'retracesoftware/dap',
)

py.install_sources(
  _py_root / 'dap/debug/__init__.py',
  _py_root / 'dap/debug/breakpoints.py',
  _py_root / 'dap/debug/cursor.py',
  _py_root / 'dap/debug/hooks.py',
  _py_root / 'dap/debug/inspection.py',
  _py_root / 'dap/debug/stepping.py',
  subdir: 'retracesoftware/dap/debug',
)

py.install_sources(
  _py_root / 'dap/protocol/__init__.py',
  _py_root / 'dap/protocol/dispatch.py',
  _py_root / 'dap/protocol/framing.py',
  _py_root / 'dap/protocol/types.py',
  subdir: 'retracesoftware/dap/protocol',
)

py.install_sources(
  _py_root / 'dap/replay/__init__.py',
  _py_root / 'dap/replay/fork.py',
  _py_root / 'dap/replay/gate.py',
  _py_root / 'dap/replay/transport.py',
  subdir: 'retracesoftware/dap/replay',
)

# ── C++ extensions ──────────────────────────────────────────────────────

subdir('cpp/functional')
subdir('cpp/utils')
subdir('cpp/cursor')
subdir('cpp/stream')
