import ast

from asttokens import ASTTokens


def test_asttokens():
    code = """
def add(a, b):
    return a + b

result = add(3, 4)
"""

    atok = ASTTokens(code, parse=True)
    tree = atok.tree

    assert isinstance(tree, ast.Module), "The tree should be an instance of ast.Module"

    func_def = tree.body[0]
    assert isinstance(func_def, ast.FunctionDef), "First item in body should be a function definition"
    assert func_def.name == "add", "Function name should be 'add'"

    func_call = tree.body[1].value
    assert isinstance(func_call, ast.Call), "Second item should be a function call"
    assert func_call.func.id == "add", "Function call should be to 'add'"

    tokens = atok.tokens
    print(f"First few tokens: {[tok.string for tok in tokens[:5]]}", flush=True)


if __name__ == "__main__":
    print("=== asttokens_test ===", flush=True)
    test_asttokens()
