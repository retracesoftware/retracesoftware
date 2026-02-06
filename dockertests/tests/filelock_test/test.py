import os

from filelock import FileLock, Timeout


LOG_FILE_PATH = "shared_log.txt"
LOCK_FILE_PATH = "shared_log.txt.lock"


def write_patient_log(patient_id, message):
    lock = FileLock(LOCK_FILE_PATH, timeout=2)

    try:
        with lock:
            with open(LOG_FILE_PATH, "a") as log_file:
                log_file.write(f"Patient ID: {patient_id} - {message}\n")
            print(f"Log entry for patient {patient_id} written successfully.", flush=True)
    except Timeout:
        print(f"Timeout: Unable to acquire lock for patient {patient_id}.", flush=True)


def test_filelock_with_io():
    write_patient_log("p123", "Checked in at 10:00 AM")
    write_patient_log("p456", "Appointment completed at 10:30 AM")

    with open(LOG_FILE_PATH, "r") as f:
        content = f.read()
        print("Log File Content:\n", content, flush=True)

    # Clean up files
    if os.path.exists(LOG_FILE_PATH):
        os.remove(LOG_FILE_PATH)
    if os.path.exists(LOCK_FILE_PATH):
        os.remove(LOCK_FILE_PATH)
    print("Deleted log and lock files.", flush=True)


if __name__ == "__main__":
    print("=== filelock_test ===", flush=True)
    test_filelock_with_io()
