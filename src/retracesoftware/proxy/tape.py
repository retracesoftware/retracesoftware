from typing import Protocol, runtime_checkable

@runtime_checkable
class TapeWriter(Protocol):
    def write(self, *values: object) -> None:
        ...

    def bind(self, obj: object) -> None:
        ...

@runtime_checkable
class TapeReader(Protocol):
    def read(self) -> object:
        ...

    def bind(self, obj: object) -> None:
        ...

@runtime_checkable
class Tape(Protocol):
    def writer(self) -> TapeWriter:
        ...
        
    def reader(self) -> TapeReader:
        ...

__all__ = ["TapeReader", "TapeWriter", "Tape"]
