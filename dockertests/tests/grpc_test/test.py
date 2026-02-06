import subprocess
import sys
import time
from concurrent import futures
from multiprocessing import Process

import grpc


def generate_protobuf() -> None:
    """
    Generate patient_pb2.py / patient_pb2_grpc.py into the current working directory.
    """
    print("Generating protobuf files...", flush=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            "--python_out=.",
            "--grpc_python_out=.",
            "patient.proto",
        ],
        check=True,
    )
    print("Protobuf files generated successfully!", flush=True)


# Generate protobufs before importing generated modules.
generate_protobuf()

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


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    patient_pb2_grpc.add_PatientServiceServicer_to_server(PatientServiceServicer(), server)
    server.add_insecure_port("[::]:50051")
    server.start()
    print("Server started on port 50051", flush=True)
    server.wait_for_termination()


def get_patient_info(patient_id: str):
    with grpc.insecure_channel("localhost:50051") as channel:
        stub = patient_pb2_grpc.PatientServiceStub(channel)
        response = stub.GetPatientInfo(patient_pb2.PatientRequest(patient_id=patient_id))
        print("Patient Info:", response, flush=True)


def test_grpcio_with_io():
    server_process = Process(target=serve)
    server_process.start()

    time.sleep(2)

    get_patient_info("p123")
    get_patient_info("p999")

    server_process.terminate()
    server_process.join(timeout=5)


if __name__ == "__main__":
    print("=== grpc_test ===", flush=True)
    test_grpcio_with_io()
