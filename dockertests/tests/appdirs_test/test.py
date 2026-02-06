import appdirs


def test_appdirs_user_data_dir():
    app_name = "TestApp"
    app_author = "TestAuthor"

    # Use appdirs to get the user data directory
    user_data_dir = appdirs.user_data_dir(app_name, app_author)

    # Ensure that the directory returned is not None and is a valid string
    assert isinstance(user_data_dir, str), "User data directory should be a string"
    assert len(user_data_dir) > 0, "User data directory should not be an empty string"
    print(f"User data directory: {user_data_dir}", flush=True)


if __name__ == "__main__":
    print("=== appdirs_test ===", flush=True)
    test_appdirs_user_data_dir()
