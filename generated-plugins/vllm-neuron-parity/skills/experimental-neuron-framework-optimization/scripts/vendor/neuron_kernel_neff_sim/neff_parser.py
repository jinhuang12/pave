"""Extract and parse NEFF tar contents into structured data."""

import io
import json
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


NEFF_HEADER_SIZE = 1024

_KNOWN_ENGINE_BASES = {"Pool", "SP", "DVE", "PE", "Activation"}


def _is_known_engine(name: str) -> bool:
    """Check if an engine name matches a known engine (with optional numeric suffix)."""
    for base in _KNOWN_ENGINE_BASES:
        if name == base or (name.startswith(base) and name[len(base):].isdigit()):
            return True
    return False


def _normalize_engine_name(name: str) -> str:
    """Strip numeric suffix from engine name: 'Pool0' -> 'Pool', 'SP0' -> 'SP'."""
    for base in _KNOWN_ENGINE_BASES:
        if name == base or (name.startswith(base) and name[len(base):].isdigit()):
            return base
    return name


@dataclass
class DMADescriptor:
    """A single DMA transfer descriptor from an engine JSON."""
    block_id: int
    queue: str
    semaphore: dict[str, Any]
    descriptors: list[dict[str, Any]]


@dataclass
class EngineData:
    """Parsed data for a single engine from the NEFF."""
    name: str
    bin_data: Optional[bytes] = None
    json_data: Optional[Dict[str, Any]] = None
    dma_descriptors: List[DMADescriptor] = field(default_factory=list)


@dataclass
class NEFFContents:
    """Fully parsed NEFF file contents."""
    neff_json: Dict[str, Any]
    def_json: Dict[str, Any]
    engines: Dict[str, EngineData]
    tensor_map: Optional[Dict[str, Any]] = None
    debug_info: Dict[str, bytes] = field(default_factory=dict)
    target: str = ""
    subgraph_id: str = "sg00"

    @property
    def variables(self) -> dict[str, Any]:
        return self.def_json.get("var", {})

    @property
    def input_bytes(self) -> int:
        """Total bytes of declared input variables (host-injected via DMA)."""
        total = 0
        for var_info in self.def_json.get("var", {}).values():
            if not isinstance(var_info, dict):
                continue
            if var_info.get("type") in ("input", "remote"):
                total += int(var_info.get("size", 0) or 0)
        return total

    @property
    def output_bytes(self) -> int:
        """Total bytes of declared output variables (host-injected via DMA)."""
        total = 0
        for var_info in self.def_json.get("var", {}).values():
            if not isinstance(var_info, dict):
                continue
            if var_info.get("type") == "output":
                total += int(var_info.get("size", 0) or 0)
        return total

    @property
    def output_count(self) -> int:
        """Number of declared output variables."""
        return sum(
            1 for var_info in self.def_json.get("var", {}).values()
            if isinstance(var_info, dict) and var_info.get("type") == "output"
        )

    @property
    def dma_queues(self) -> dict[str, Any]:
        return self.def_json.get("dma_queue", {})

    @property
    def semaphore_count(self) -> int:
        return self.def_json.get("runtime_semaphore_count", 0)

    @property
    def vnc_count(self) -> int:
        """Number of virtual NeuronCores sharing this physical core.

        VNC=2 means two kernels share the core (each gets ~half HBM BW).
        VNC=1 means single kernel owns the whole core (full HBM BW).
        Detected from neff_features: 'neff_feature_vnc' present means VNC>=2.
        """
        features = self.def_json.get("neff_features", [])
        if "neff_feature_vnc" in features:
            return 2
        return 1

    def get_engine_names(self) -> List[str]:
        return [name for name in self.engines if _is_known_engine(name) and self.engines[name].bin_data is not None]


def parse_neff(neff_path: "str | Path") -> NEFFContents:
    """Parse a NEFF file into structured contents.

    Args:
        neff_path: Path to the .neff file

    Returns:
        NEFFContents with all parsed data
    """
    neff_path = Path(neff_path)

    with open(neff_path, "rb") as f:
        header = f.read(NEFF_HEADER_SIZE)
        _validate_header(header)
        tar_data = f.read()

    tar_buffer = io.BytesIO(tar_data)
    tar = tarfile.open(fileobj=tar_buffer, mode="r:gz")

    neff_json = {}
    def_json = {}
    engines: dict[str, EngineData] = {}
    tensor_map = None
    debug_info: dict[str, bytes] = {}
    subgraph_id = "sg00"

    for member in tar.getmembers():
        if not member.isfile():
            continue

        content = tar.extractfile(member)
        if content is None:
            continue

        raw = content.read()
        name = member.name

        if name == "neff.json":
            neff_json = json.loads(raw)
        elif name.endswith("/def.json"):
            def_json = json.loads(raw)
            parts = name.split("/")
            if len(parts) > 1:
                subgraph_id = parts[0]
        elif name.endswith(".bin"):
            engine_name = _extract_engine_name(name)
            if engine_name not in engines:
                engines[engine_name] = EngineData(name=engine_name)
            engines[engine_name].bin_data = raw
        elif name.endswith(".json") and "def.json" not in name and "neff.json" not in name:
            if "tensor_map" in name:
                tensor_map = json.loads(raw)
            else:
                engine_name = _extract_engine_name(name)
                if not _is_known_engine(engine_name):
                    continue
                if engine_name not in engines:
                    engines[engine_name] = EngineData(name=engine_name)
                engine_json = json.loads(raw)
                if not isinstance(engine_json, dict):
                    continue
                engines[engine_name].json_data = engine_json
                engines[engine_name].dma_descriptors = _parse_dma_descriptors(engine_json)
        elif "debug_info" in name:
            debug_info[name] = raw

    tar.close()

    target = _infer_target(neff_json)

    return NEFFContents(
        neff_json=neff_json,
        def_json=def_json,
        engines=engines,
        tensor_map=tensor_map,
        debug_info=debug_info,
        target=target,
        subgraph_id=subgraph_id,
    )


def _validate_header(header: bytes) -> None:
    """Basic validation of the NEFF header."""
    if len(header) < NEFF_HEADER_SIZE:
        raise ValueError(f"NEFF header too short: {len(header)} bytes, expected {NEFF_HEADER_SIZE}")


def _extract_engine_name(path: str) -> str:
    """Extract engine name from a path like 'sg00/Pool.bin' -> 'Pool'."""
    filename = path.split("/")[-1]
    return filename.rsplit(".", 1)[0]


def _infer_target(neff_json: dict[str, Any]) -> str:
    """Infer hardware target from neff.json metadata."""
    if not neff_json:
        return "trn2"
    arch = neff_json.get("arch", "")
    if "sunda" in arch.lower() or "cayman" in arch.lower():
        return "trn2"
    return neff_json.get("target", "trn2")


def _parse_dma_descriptors(engine_json: dict[str, Any]) -> list[DMADescriptor]:
    """Parse DMA descriptor array from an engine JSON."""
    dma_array = engine_json.get("dma", [])
    descriptors = []
    for entry in dma_array:
        desc = DMADescriptor(
            block_id=entry.get("id", 0),
            queue=entry.get("queue", ""),
            semaphore=entry.get("semaphore", {}),
            descriptors=entry.get("desc", []),
        )
        descriptors.append(desc)
    return descriptors
