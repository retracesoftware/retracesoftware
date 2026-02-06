import click


@click.command()
@click.argument("name")
def greet(name):
    click.echo(f"Hello, {name}!")


def test_click_greet():
    from click.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(greet, ["Daniel"])

    assert result.exit_code == 0, "Command should exit successfully"
    assert result.output == "Hello, Daniel!\n", f"Expected 'Hello, Daniel!', but got '{result.output}'"

    print(f"Command output: {result.output}", flush=True)


if __name__ == "__main__":
    print("=== click_test ===", flush=True)
    test_click_greet()
