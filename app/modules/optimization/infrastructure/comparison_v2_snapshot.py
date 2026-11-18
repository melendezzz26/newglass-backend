"""Versioned JSON codecs for comparison-v2 result and input snapshots."""
from collections.abc import Mapping
from dataclasses import fields
from decimal import Decimal
import json

from ..application.comparison_catalog import BY_METHOD, RESULT_SNAPSHOT_V3, validate_methods
from ..application.comparison_v2_contracts import ComparisonInputV2, ExecutionDiagnostic, ExecutionPayloadV2
from ..domain.problem import OptimizationResult
from .comparison_snapshot import (ComparisonSnapshotError, canonical_json,
    decode_result as decode_historical, encode_problem, decode_problem, problem_fingerprint)
from .run_snapshot import decode as decode_geometry, decode_residual_policy


RESULT_SNAPSHOT_V4 = 4


def encode_result_v3(payload: ExecutionPayloadV2):
    if not isinstance(payload, ExecutionPayloadV2) or payload.residual_policy is not None:
        raise ComparisonSnapshotError("V3 cannot store a residual policy")
    return _encode_result(payload, RESULT_SNAPSHOT_V3)


def encode_result_v4(payload: ExecutionPayloadV2):
    return _encode_result(payload, RESULT_SNAPSHOT_V4)


def _encode_result(payload, version):
    if not isinstance(payload, ExecutionPayloadV2) or payload.diagnostic is None:
        raise ComparisonSnapshotError("Execution diagnostics are required")
    value = dict(snapshot_version=version, profile="comparison-v2", method=payload.method,
                 result=payload.result, diagnostic=payload.diagnostic.public())
    if version == RESULT_SNAPSHOT_V4:
        if (payload.result is None) != (payload.residual_policy is None):
            raise ComparisonSnapshotError("V4 requires an explicit policy for each result")
        value["residual_policy"] = payload.residual_policy
    return json.loads(canonical_json(value))


def decode_result_v3(snapshot: Mapping, *, expected_method=None):
    return _decode_result(snapshot, RESULT_SNAPSHOT_V3, expected_method)


def decode_result_v4(snapshot: Mapping, *, expected_method=None):
    return _decode_result(snapshot, RESULT_SNAPSHOT_V4, expected_method)


def _decode_result(snapshot, version, expected_method):
    try:
        value = json.loads(canonical_json(snapshot))
        keys = {"snapshot_version", "profile", "method", "result", "diagnostic"}
        if version == RESULT_SNAPSHOT_V4:
            keys.add("residual_policy")
        if (not isinstance(value, dict) or set(value) != keys
                or type(value["snapshot_version"]) is not int or value["snapshot_version"] != version
                or value["profile"] != "comparison-v2"):
            raise ValueError("Invalid result envelope")
        method = value["method"]
        validate_methods((method,))
        if expected_method is not None:
            validate_methods((expected_method,))
            if method != expected_method:
                raise ValueError("Snapshot belongs to another method")
        diagnostic = ExecutionDiagnostic(**value["diagnostic"])
        result = None
        if value["result"] is not None:
            base = decode_geometry({"snapshot_version": 1, "result": value["result"]})
            result = BY_METHOD[method].result_type(**{
                field.name: getattr(base, field.name) for field in fields(OptimizationResult)})
        policy = None
        if version == RESULT_SNAPSHOT_V4:
            raw_policy = value["residual_policy"]
            if (result is None) != (raw_policy is None):
                raise ValueError("V4 requires an explicit policy for each result")
            if raw_policy is not None:
                policy = decode_residual_policy(raw_policy)
        return ExecutionPayloadV2(method=method, result=result, diagnostic=diagnostic, residual_policy=policy)
    except (KeyError, TypeError, ValueError, ArithmeticError, OverflowError) as error:
        raise ComparisonSnapshotError("Snapshot comparison-v2 inválido o incoherente") from error


def decode_comparison_result(snapshot: Mapping, *, expected_method=None):
    if isinstance(snapshot, Mapping) and type(snapshot.get("snapshot_version")) is int:
        if snapshot["snapshot_version"] == RESULT_SNAPSHOT_V3:
            return decode_result_v3(snapshot, expected_method=expected_method)
        if snapshot["snapshot_version"] == RESULT_SNAPSHOT_V4:
            return decode_result_v4(snapshot, expected_method=expected_method)
    raise ComparisonSnapshotError("Versión de snapshot no soportada")


def decode_compatible_result(snapshot: Mapping, *, expected_method=None):
    """Dispatch by stored version, preserving the absence of historical metadata."""
    if (isinstance(snapshot, Mapping) and type(snapshot.get("snapshot_version")) is int
            and snapshot["snapshot_version"] in (1, 2)):
        result = decode_historical(snapshot, expected_method=expected_method)
        return ExecutionPayloadV2(method=result.method, result=result, diagnostic=None)
    return decode_comparison_result(snapshot, expected_method=expected_method)


def encode_comparison_input_v2(value: ComparisonInputV2):
    if not isinstance(value, ComparisonInputV2):
        raise ComparisonSnapshotError("Se requiere entrada congelada comparison-v2")
    return json.loads(canonical_json(dict(comparison_snapshot_version=2, profile="comparison-v2",
        methods=value.methods, input=encode_problem(value.problem), input_sha256=problem_fingerprint(value.problem),
        configuration_id=value.configuration_id, configuration_version=value.configuration_version,
        requested_pieces=value.requested_pieces, minimum_width_mm=value.minimum_width_mm,
        minimum_height_mm=value.minimum_height_mm, engine_parameters={"M6-BS": {"width": 2}})))


def decode_comparison_input_v2(snapshot: Mapping):
    """B01 input envelope only; mutable runtime/DB metadata belongs to B02/B05."""
    try:
        value = json.loads(canonical_json(snapshot))
        if not isinstance(value, dict) or set(value) != {
            "comparison_snapshot_version", "profile", "methods", "input", "input_sha256",
            "configuration_id", "configuration_version", "requested_pieces", "minimum_width_mm",
            "minimum_height_mm", "engine_parameters"
        } or type(value["comparison_snapshot_version"]) is not int or value["comparison_snapshot_version"] != 2:
            raise ValueError("Invalid comparison input envelope")
        problem = decode_problem(value["input"])
        parameters = value["engine_parameters"]
        if (value["profile"] != "comparison-v2" or value["input_sha256"] != problem_fingerprint(problem)
                or parameters != {"M6-BS": {"width": 2}}
                or type(parameters["M6-BS"]["width"]) is not int):
            raise ValueError("Input fingerprint/profile/BS-2 variant mismatch")
        if any(type(value[name]) is not str for name in ("minimum_width_mm", "minimum_height_mm")):
            raise ValueError("Persisted residual thresholds require decimal strings")
        return ComparisonInputV2(problem=problem, methods=tuple(value["methods"]),
            configuration_id=value["configuration_id"], configuration_version=value["configuration_version"],
            requested_pieces=value["requested_pieces"], minimum_width_mm=Decimal(value["minimum_width_mm"]),
            minimum_height_mm=Decimal(value["minimum_height_mm"]))
    except (KeyError, TypeError, ValueError, ArithmeticError, OverflowError) as error:
        raise ComparisonSnapshotError("Entrada comparison-v2 inválida o incoherente") from error
