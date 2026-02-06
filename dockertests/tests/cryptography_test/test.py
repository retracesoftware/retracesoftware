from cryptography.fernet import Fernet


def main():
    # generate a key for encryption and decryption
    key = Fernet.generate_key()
    cipher_suite = Fernet(key)

    # define a message to encrypt
    message = b"this is rather interesting"

    encrypted_text = cipher_suite.encrypt(message)
    print("Encrypted:", encrypted_text, flush=True)

    decrypted_text = cipher_suite.decrypt(encrypted_text)
    print("Decrypted:", decrypted_text, flush=True)

    assert decrypted_text == message


if __name__ == "__main__":
    print("=== cryptography_test ===", flush=True)
    main()
