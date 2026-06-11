# in-process moto mock_aws record regression

Manual end-to-end reproducer for in-process `moto.mock_aws()` under Retrace.

This scenario is distinct from the external Moto server scenario. It uses
Moto's in-process botocore hook and currently fails during `record`, before
Replay starts, because Moto's `MotoRandom(Random)` path trips Retrace's
`_random.Random` patching.

Run:

```bash
cd /path/to/retracesoftware/dockertests
python run.py moto_inprocess_mock_aws_test
```

Current failing signature:

```text
TypeError: descriptor 'getrandbits' for '_random.Random' objects doesn't apply to a 'MotoRandom' object
```

The smaller unit regression is:

```bash
python -m pytest tests/install/external/test_moto_inprocess_replay_regression.py -ra
```
