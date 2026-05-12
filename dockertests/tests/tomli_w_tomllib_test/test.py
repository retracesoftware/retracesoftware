import tomllib

import tomli_w


def main():
    print("=== tomli_w_tomllib_test ===")
    data = {
        "project": {"name": "retrace-demo", "version": "1.0"},
        "tool": {"retrace": {"enabled": True, "workers": 2}},
    }
    encoded = tomli_w.dumps(data)
    decoded = tomllib.loads(encoded)
    assert decoded == data
    print(decoded["project"]["name"])
    print(f"workers={decoded['tool']['retrace']['workers']}")
    print("tomli-w/tomllib ok")


if __name__ == "__main__":
    main()
