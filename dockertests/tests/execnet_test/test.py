import execnet


def test_execnet_spec_parsing():
    spec = execnet.XSpec("popen//python=python")
    assert spec.popen is True
    assert spec.python == "python"

    group = execnet.Group()
    assert len(group) == 0
    print("Execnet spec parsing and group construction work.", flush=True)


if __name__ == "__main__":
    print("=== execnet_test ===", flush=True)
    test_execnet_spec_parsing()
