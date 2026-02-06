from types import FunctionType

import bytecode


def test_bytecode_manipulation():
    code = bytecode.Bytecode()

    # Load constant (1) onto the stack and return it
    code.extend([bytecode.Instr("LOAD_CONST", 1), bytecode.Instr("RETURN_VALUE")])

    code_obj = code.to_code()
    generated_function = FunctionType(code_obj, globals())

    result = generated_function()

    assert result == 1, "The function should return 1"
    print("Test passed! Bytecode manipulation works correctly.", flush=True)


if __name__ == "__main__":
    print("=== bytecode_test ===", flush=True)
    test_bytecode_manipulation()
