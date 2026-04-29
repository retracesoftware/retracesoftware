import sys
from pathlib import Path

import grpc

sys.path.insert(0, str(Path(__file__).parent))

import patient_pb2  # noqa: E402
import patient_pb2_grpc  # noqa: E402


patients = {
    "p123": {"name": "John Doe", "age": 45, "status": "admitted"},
    "p456": {"name": "Jane Smith", "age": 30, "status": "discharged"},
}


class PatientServiceServicer(patient_pb2_grpc.PatientServiceServicer):
    def GetPatientInfo(self, request, context):
        patient_info = patients.get(
            request.patient_id, {"name": "Unknown", "age": 0, "status": "N/A"}
        )
        return patient_pb2.PatientResponse(
            name=patient_info["name"],
            age=patient_info["age"],
            status=patient_info["status"],
        )


class FakeRpcContext:
    def __init__(self):
        self.code = grpc.StatusCode.OK
        self.details = ""

    def set_code(self, code):
        self.code = code

    def set_details(self, details):
        self.details = details


class InProcessChannel:
    def __init__(self, servicer):
        self.servicer = servicer

    def unary_unary(self, method, request_serializer, response_deserializer, **kwargs):
        assert method == "/patient.PatientService/GetPatientInfo"

        def call(request):
            request_bytes = request_serializer(request)
            round_tripped = patient_pb2.PatientRequest.FromString(request_bytes)
            response = self.servicer.GetPatientInfo(round_tripped, FakeRpcContext())
            return response_deserializer(response.SerializeToString())

        return call


def get_patient_info(stub, patient_id: str):
    response = stub.GetPatientInfo(patient_pb2.PatientRequest(patient_id=patient_id))
    print("Patient Info:", response, flush=True)


def test_grpcio_with_io():
    servicer = PatientServiceServicer()
    channel = InProcessChannel(servicer)
    stub = patient_pb2_grpc.PatientServiceStub(channel)

    get_patient_info(stub, "p123")
    get_patient_info(stub, "p999")


if __name__ == "__main__":
    print("=== grpc_test ===", flush=True)
    test_grpcio_with_io()
