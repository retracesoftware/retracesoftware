# flight_search_relative_autoenable_test

Manual end-to-end reproducer for the cookbook flight-search replay divergence
seen when recording through the `.pth` auto-enable path with a relative
`RETRACE_RECORDING` value.

Run from the cookbook `flight-search-assistant` checkout, not from this
directory, so the relative recording file is created beside the app just like
the failing manual flow:

```bash
cd /path/to/cookbook/examples/flight-search-assistant
source /path/to/retrace-venv/bin/activate
python -m retracesoftware install

rm -f flight-relative-autoenable.retrace
rm -rf flight-relative-autoenable.d

FLIGHT_SEARCH_ASSISTANT_DIR=$PWD RETRACE_RECORDING=flight-relative-autoenable.retrace \
  python /path/to/retracesoftware/dockertests/tests/flight_search_relative_autoenable_test/test.py

./flight-relative-autoenable.retrace --extract

ROOT_PID=$(python -m retracesoftware --recording flight-relative-autoenable.retrace --list_pids | head -1)
echo "ROOT_PID=$ROOT_PID"

./flight-relative-autoenable.d/${ROOT_PID}.bin
```

Current bad replay behavior can surface as:

- `RuntimeError: bind marker returned when bind was expected`
- or replay stdout diverging from record output after the model/tool path.

The expected result is a clean replay with stdout matching the recorded run.
