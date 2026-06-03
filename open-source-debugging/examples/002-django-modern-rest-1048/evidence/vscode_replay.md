# VS Code Replay Evidence

Manual VS Code replay was able to reach the useful `django-modern-rest` frames
for issue #1048.

Useful caller evidence:

```text
dmr/openapi/generators/parameter.py:73
property_schema = Reference(ref="#/components/schemas/TestEnum")
```

Useful failing frame:

```text
dmr/openapi/core/registry.py:112
schema_name = "TestEnum"
resolution_context = None
self.schemas lacks "TestEnum"
```

## Replay GIF

A short replay GIF is included at:

```text
evidence/replay.gif
```

It shows manual VS Code replay reaching the useful schema-generation frames:
the query parameter metadata references `#/components/schemas/TestEnum`, while
the registry lookup later fails because `TestEnum` is not registered.

Conclusion:

The recording is replay-capable and the runtime state is inspectable manually
in VS Code. However, current `retrace-agent inspect` stopped on an earlier
internal generated `TypeError` (`unhashable type: 'Not'`) rather than the final
useful `KeyError`.
