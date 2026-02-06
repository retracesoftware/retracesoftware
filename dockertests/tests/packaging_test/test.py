from packaging import version


def test_packaging_version_comparison():
    v1 = version.parse("2.0.1")
    v2 = version.parse("2.0.2")

    print(f"Version {v1} is less than {v2}: {v1 < v2}", flush=True)
    assert v1 < v2

    pre_release_version = version.parse("2.0.1a1")
    print(
        f"Version {pre_release_version} is a pre-release: {pre_release_version.is_prerelease}",
        flush=True,
    )
    assert pre_release_version.is_prerelease is True


def test_version_specifiers():
    from packaging.specifiers import SpecifierSet

    specifier_set = SpecifierSet(">=2.0.1, <3.0")

    print(
        "Is '2.0.1' compatible with '>=2.0.1, <3.0'? ",
        version.parse("2.0.1") in specifier_set,
        flush=True,
    )
    print(
        "Is '3.1' compatible with '>=2.0.1, <3.0'? ",
        version.parse("3.1") in specifier_set,
        flush=True,
    )

    assert version.parse("2.0.1") in specifier_set
    assert version.parse("3.1") not in specifier_set


if __name__ == "__main__":
    print("=== packaging_test ===", flush=True)
    test_packaging_version_comparison()
    test_version_specifiers()
