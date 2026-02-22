"""Minimal repro: replay crashes with stat_result in sys.path_importer_cache.

Even a trivial script fails on replay because infrastructure code
(Path.exists, runpy.run_path → pkgutil.get_importer) runs inside the
replay context and consumes stream messages, misaligning the stream.
"""
print("ok")
