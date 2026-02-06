from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from apischema import ValidationError, deserialize


@dataclass
class PatientInfo:
    id: str
    name: str
    dob: datetime
    address: str
    phone: Optional[str] = None


@dataclass
class ClinicianInfo:
    id: str
    name: str
    specialization: str


@dataclass
class Booking:
    patient: PatientInfo
    clinician: ClinicianInfo
    appointment_time: datetime
    status: str
    notes: Optional[str] = None


valid_data = {
    "patient": {
        "id": "p123",
        "name": "John Doe",
        "dob": "1980-04-12T00:00:00",
        "address": "123 Health St, Wellness City",
        "phone": "123-456-7890",
    },
    "clinician": {
        "id": "c456",
        "name": "Dr. A Smith",
        "specialization": "General Practice",
    },
    "appointment_time": "2024-11-20T15:30:00",
    "status": "confirmed",
    "notes": "Follow-up appointment",
}

invalid_data = {
    "patient": {
        "id": "p123",
        "name": "John Doe",
        "dob": "invalid-date",  # Invalid date format
        "address": "123 Health St, Wellness City",
    },
    "clinician": {
        "id": "c456",
        "name": "Dr. A Smith",
        "specialization": "General Practice",
    },
    "appointment_time": "2024-11-20T15:30:00",
    "status": "scheduled",
}


def test_booking_schema():
    # Test valid data
    try:
        deserialize(Booking, valid_data)
        print("Valid data test passed.", flush=True)
    except ValidationError as e:
        print("Valid data test failed:", e, flush=True)

    # Test invalid data
    try:
        deserialize(Booking, invalid_data)
        print("Invalid data test unexpectedly passed.", flush=True)
    except ValidationError as e:
        print("Invalid data test passed with expected error:", e, flush=True)


if __name__ == "__main__":
    print("=== apischema_test ===", flush=True)
    test_booking_schema()
