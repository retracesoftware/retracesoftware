# in-process moto mock_aws record regression

End-to-end regression for in-process `moto.mock_aws()` under Retrace.

This scenario is distinct from the external Moto server scenario. It uses
Moto's in-process botocore hook and exercises the `MotoRandom(Random)` path
that previously tripped Retrace's `_random.Random` patching during `record`.

Run:

```bash
cd /path/to/retracesoftware/dockertests
python run.py moto_inprocess_mock_aws_test
```

Previous failing signature:

```text
TypeError: descriptor 'getrandbits' for '_random.Random' objects doesn't apply to a 'MotoRandom' object
```

The smaller unit regression is:

```bash
python -m pytest tests/install/external/test_moto_inprocess_replay_regression.py -ra
```
