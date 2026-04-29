import backoff


attempts = 0


def unreliable_function():
    global attempts

    attempts += 1
    print("Trying to perform the task...", flush=True)
    if attempts < 3:
        raise Exception("Task failed, retrying...")
    return "Task succeeded!"


@backoff.on_exception(backoff.expo, Exception, max_tries=5, jitter=None, factor=0)
def retry_task():
    return unreliable_function()


def test_backoff_retry():
    try:
        result = retry_task()
        assert result == "Task succeeded!", "The task should eventually succeed."
        print("Test passed! The task succeeded with retries.", flush=True)
    except Exception as e:
        print(f"Test failed: {e}", flush=True)
        assert False, "The task should have succeeded after retries."


if __name__ == "__main__":
    print("=== backoff_test ===", flush=True)
    test_backoff_retry()
