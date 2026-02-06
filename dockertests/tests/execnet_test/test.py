import execnet


def remote_calculation(channel):
    number = channel.receive()
    result = number * 2
    channel.send(result)


def test_execnet_remote_execution():
    # In the dockertests container we have `python` (not necessarily `python3`),
    # so prefer `python` for the popen gateway.
    gateway = execnet.makegateway("popen//python=python")

    channel = gateway.remote_exec(remote_calculation)

    test_number = 10
    print(f"Sending number: {test_number}", flush=True)
    channel.send(test_number)

    result = channel.receive()
    print("Received result from remote interpreter:", result, flush=True)

    assert result == test_number * 2, "Remote calculation failed."

    gateway.exit()


if __name__ == "__main__":
    print("=== execnet_test ===", flush=True)
    test_execnet_remote_execution()
