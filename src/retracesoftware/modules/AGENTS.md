# Module Interception Config

This directory is not miscellaneous config. These TOML files define which
library and stdlib behavior crosses the Retrace boundary, how it is proxied,
and which values are treated as immutable, bound, disabled, or replay-side
materialized. Missing or wrong config here can cause replay divergence without
touching core runtime code.

## Current Core Files

- `__init__.py`
  `ModuleConfigResolver` loads user overrides plus built-in TOML configs and
  resolves grouped vs single-module files and version-specific sections.
- `stdlib.toml`
  Broad stdlib interception coverage and directive examples.
- `debuggers.toml`
  Debugger-specific disable rules that prevent tracing tools from perturbing
  record/replay.
- Single-module TOMLs such as `numpy.random.toml`, `pycurl.toml`,
  `psycopg2._psycopg.toml`
  Version-aware third-party interception coverage.

## Mental Model

- User configs from `RETRACE_MODULES_PATH` or `.retrace/modules/` override the
  built-in package configs.
- Grouped TOMLs use top-level tables per module; single-module TOMLs use the
  filename as the module name and may include additive version sections.
- Directives such as `proxy`, `immutable`, `bind`, `disable`, `patch_hash`,
  `wrap`, `patch_class`, `patch_types`, `type_attributes`, `default`, `ignore`,
  `pathparam`, and `replay_materialize` affect how install/proxy/runtime layers
  behave.
- These files are coverage and semantics, not just allowlists. A one-line TOML
  change can change replay identity, interception breadth, or debugger behavior.

## High-Risk Areas

- Missing interception for new nondeterministic calls.
- Over-proxying deterministic or internal behavior that should stay passthrough.
- Wrong `replay_materialize`, `bind`, or `wrap` directives that change replay
  object identity or binding/materialization order.
- Wrong `patch_class`, `patch_types`, `default`, or `ignore` directives that
  change which methods/types are wrapped or skipped at install time.
- Incorrect `pathparam` rules that make filesystem calls bypass or enter the
  proxy unexpectedly.
- Version-section drift for third-party packages where the package is missing or
  the installed version resolves a different additive config than expected.
- Debugger-related config drift that lets tracing hooks observe retrace internals.

## Working Rules

- Prefer the narrowest config change that fixes interception coverage before
  rewriting deeper runtime code.
- When editing a TOML file, explain which concrete call path or type behavior is
  being intercepted and why the chosen directive is correct.
- If you add or change `replay_materialize`, `bind`, or `wrap`, re-check the
  relevant stream/protocol/proxy tests because those directives affect replay
  identity and materialization contract.
- If you add or change `patch_class`, `patch_types`, `default`, or `ignore`,
  re-check install/proxy tests because those directives change interception
  coverage and wrapper application.
- Keep version-specific additions additive and easy to reason about.
- If a package-specific config only applies when a dependency is installed,
  say so explicitly in tests or review notes.

## References

- `src/retracesoftware/modules/__init__.py`
- `src/retracesoftware/modules/stdlib.toml`
- `src/retracesoftware/modules/debuggers.toml`
- `src/retracesoftware/install/patcher.py`
- `src/retracesoftware/install/__init__.py`
