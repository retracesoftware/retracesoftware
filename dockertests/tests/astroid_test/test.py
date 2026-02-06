import astroid


def test_astroid_parse():
    code = """
def add(a, b):
    return a + b
"""

    module = astroid.parse(code)

    assert isinstance(module, astroid.Module), "Expected an AST module"
    assert module.body[0].name == "add", "Expected a function named 'add'"

    func_node = module.body[0]
    assert len(func_node.args.args) == 2, "Expected the function to have 2 arguments"

    print("Test passed! astroid parsed the code correctly.", flush=True)


if __name__ == "__main__":
    print("=== astroid_test ===", flush=True)
    test_astroid_parse()
