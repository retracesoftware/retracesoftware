import pkgutil
import json
import os
import datetime
import tomllib

from pathlib import Path

def debug_level(config):
    if 'RETRACE_DEBUG' in os.environ:
        debug = env_int('RETRACE_DEBUG')
    else:
        debug = config.get('record', {}).get('debug', 1)

    return debug

def env_truthy(key, default=False):
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")

def env_int(key, default = 0):
    value = os.getenv(key)
    if value is None:
        return default
    return int(value)

def get_recording_path(config):
    return Path(datetime.datetime.now().strftime(config.format(pid = os.getpid())))

def recording_path(config):
    if 'RETRACE_RECORDING_PATH' in os.environ:
        return Path(os.environ['RETRACE_RECORDING_PATH'])
    else:
        recording_path = get_recording_path(config.get('record_path', 'recordings'))
        os.environ['RETRACE_RECORDING_PATH'] = str(recording_path)
        return recording_path

def load_module_config(filename):
    data = pkgutil.get_data("retracesoftware", filename)
    assert data is not None
    return tomllib.loads(data.decode("utf-8"))

def load_config(filename):
        
    data = pkgutil.get_data("retracesoftware", filename)
    assert data is not None

    config = json.loads(data.decode("utf-8"))

    config['debug_level'] = debug_level(config)

    # config['recording_path'] = recording_path(config)

    config['verbose'] = env_truthy('RETRACE_VERBOSE')
    
    return config

