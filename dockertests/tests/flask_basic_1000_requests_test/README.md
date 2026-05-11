# flask_basic_1000_requests_test

Stress regression for Flask's threaded development server under Retrace replay.

The test starts a local Flask app under Retrace. `client.py` then drives 1000
pairs of browser-like requests to `/` and `/favicon.ico` from outside the
recorded process. Interrupt the server after the client completes, then replay
the trace.

It exists to reproduce replay divergence that only appears after many threaded
request-handler turns.
