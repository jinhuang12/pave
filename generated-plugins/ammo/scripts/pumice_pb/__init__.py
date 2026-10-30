"""AMMO-owned gRPC stubs for the Pumice ``PumiceEngine`` service.

The stubs come from ``pumice-rust/proto/pumice_engine.proto``, the proto that
the Rust server compiles. The Python proto shipped in ``pumice.grpc`` comments
out ``prompt_tokens_cached``, so the bench runner does not use it. See
README.md in this directory for the source commit and the generate command.

Do not import ``pumice.grpc.pumice_engine_pb2`` in the same process. Both
files define the ``pumice.grpc.engine`` package with different fields, and
the default descriptor pool rejects the second one.
"""
