"""hundekraft 3.3 (kennel) firmware builder.

Vendored from https://github.com/Hundekraft/op1-workshop (GPL-3.0-or-later with
the OP-1 combination permission; see firmware_data/hundekraft/LICENSE, NOTICE
and UPSTREAM.md). runtime.py is their unchanged builder and their site's
browser worker is mirrored here: normalize a selection, map it to the prebuilt
variant bundle, patch the stock R246 image server-side, and filter the preset
pack to the same selection. Only the stock original R246 bytes can be a build
source; the sha256 check is theirs and is enforced before any work is done.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import threading
import time
from pathlib import Path

from .config import StudioConfig
from .firmware import (
    FirmwareError,
    _cached_installer_sha,
    build_output_name,
    installer_path,
    list_installers,
    sha256_bytes,
)

DATA_DIR = Path(__file__).resolve().parent / "firmware_data" / "hundekraft"
CONFIG_ID_RE = re.compile(r"[0-9a-f]{2,16}")

_lock = threading.Lock()
_release_cache: tuple[int, dict] | None = None
_runtime_module = None


def release() -> dict:
    global _release_cache
    path = DATA_DIR / "release.json"
    stamp = path.stat().st_mtime_ns
    if _release_cache is None or _release_cache[0] != stamp:
        payload = json.loads(path.read_text("utf-8"))
        runtime_sha = payload.get("runtime_sha256")
        actual = _cached_installer_sha(DATA_DIR / "runtime.py")
        if runtime_sha and actual != runtime_sha:
            raise FirmwareError(
                f"vendored kennel runtime does not match release.json ({actual} != {runtime_sha})"
            )
        _release_cache = (stamp, payload)
    return _release_cache[1]


def runtime():
    global _runtime_module
    with _lock:
        if _runtime_module is None:
            spec = importlib.util.spec_from_file_location(
                "kennel_runtime", DATA_DIR / "runtime.py"
            )
            if spec is None or spec.loader is None:
                raise FirmwareError(f"cannot load vendored runtime: {DATA_DIR / 'runtime.py'}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _runtime_module = module
    return _runtime_module


def catalog() -> dict:
    return release()["catalog"]


def original_sha256() -> str:
    base = json.loads((DATA_DIR / "base" / "manifest.json").read_text("utf-8"))
    return base["original"]["sha256"]


def config_ids(selection: dict) -> tuple[str, str]:
    module = runtime()
    cat = catalog()
    try:
        config = module.normalize(selection, cat)
        return (
            module.config_id(config, cat),
            module.config_id(module.variant_config(config, cat), cat),
        )
    except ValueError as exc:
        raise FirmwareError(f"invalid hundekraft selection: {exc}") from exc


def buildable(config: StudioConfig, name: str) -> bool:
    try:
        return _cached_installer_sha(installer_path(config, name)) == original_sha256()
    except FirmwareError:
        return False


def find_stock_source(config: StudioConfig) -> str:
    original = original_sha256()
    for row in list_installers(config):
        if row["sha256"] == original:
            return row["name"]
    raise FirmwareError(
        f"no stock R246 firmware (op1_246.op1) in {config.firmware_dir}; "
        "hundekraft builds from the original image only"
    )


def default_config() -> dict:
    cat = catalog()
    return {
        "schema": 1,
        "base": cat["base"],
        "synths": [f["id"] for f in cat["synths"]],
        "effects": [f["id"] for f in cat["effects"]],
        "utilities": [f["id"] for f in cat.get("utilities", [])],
        "preset_cap": cat["preset_caps"][-1],
    }


def _slim_plugin(entry: dict) -> dict:
    return {
        key: entry[key]
        for key in ("id", "name", "description", "color", "type", "controls", "needs", "marker")
        if key in entry
    }


def summary(config: StudioConfig) -> dict:
    payload = release()
    cat = payload["catalog"]
    rows = list_installers(config)
    original = original_sha256()
    originals = {row["name"] for row in rows if row["sha256"] == original}
    return {
        "product": payload["release"]["product"],
        "release_id": payload["release"]["id"],
        "experimental": payload["release"].get("experimental", False),
        "op1r_commit": payload["release"].get("op1r_commit"),
        "base": cat["base"],
        "preset_caps": cat["preset_caps"],
        "screen_budget": cat.get("screen_budget"),
        "synths": [_slim_plugin(f) for f in cat["synths"]],
        "effects": [_slim_plugin(f) for f in cat["effects"]],
        "utilities": [_slim_plugin(f) for f in cat.get("utilities", [])],
        "op1r_plugins": [
            {"id": f["id"], "name": f.get("name"), "always": bool(f.get("always"))}
            for f in cat.get("op1r_plugins", [])
        ],
        "variants": sorted(payload["variants"]),
        "default_config": default_config(),
        "images": [
            {"name": row["name"], "bytes": row["bytes"], "buildable": row["name"] in originals}
            for row in rows
        ],
    }


def _validated_variant(selection_id: str) -> str:
    payload = release()
    variant_ids = sorted(payload["variants"])
    if len(variant_ids) != 1:
        raise FirmwareError(
            f"selection {selection_id} has no prebuilt bundle in this vendored release "
            f"(bundles: {', '.join(variant_ids)})"
        )
    return variant_ids[0]


def _safe_config_id(raw_id: str) -> str:
    candidate = str(raw_id).strip().lower()
    if not CONFIG_ID_RE.fullmatch(candidate):
        raise FirmwareError(f"not a hundekraft config id: {raw_id!r}")
    return candidate


def _blob_reader(directory: Path, module):
    def read(name: str) -> bytes:
        if (
            not name
            or name.startswith("/")
            or ".." in name.split("/")
            or not re.fullmatch(r"[A-Za-z0-9_./-]+", name)
        ):
            raise FirmwareError(f"patch asset name rejected: {name!r}")
        path = directory / name
        if not path.is_file() or not path.resolve().is_relative_to(directory.resolve()):
            raise FirmwareError(f"vendored patch asset missing: {path}")
        return path.read_bytes()

    return read


def build(config: StudioConfig, selection: dict, source: str | None = None,
          overwrite: bool = False) -> dict:
    if source is None:
        source = find_stock_source(config)
    return build_image(config, source, selection, overwrite=overwrite)


def build_image(config: StudioConfig, source_name: str, selection: dict,
                overwrite: bool = False) -> dict:
    source_path = installer_path(config, source_name)
    input_sha = _cached_installer_sha(source_path)
    if input_sha != original_sha256():
        raise FirmwareError(
            f"{source_path.name} is not the original R246 firmware "
            f"(sha256 {input_sha[:12]}…); hundekraft builds from stock op1_246.op1 only"
        )
    module = runtime()
    cat = catalog()
    payload = release()
    try:
        normalized = module.normalize(selection, cat)
        selection_id = module.config_id(normalized, cat)
        variant_id = module.config_id(module.variant_config(normalized, cat), cat)
    except ValueError as exc:
        raise FirmwareError(f"invalid hundekraft selection: {exc}") from exc
    if variant_id not in payload["variants"]:
        raise FirmwareError(
            f"this configuration is not available in hundekraft {payload['release']['product']['version']}"
        )

    stem = build_output_name(f"{payload['release']['product']['slug']} {selection_id}")
    output_path = config.firmware_dir / stem
    presets_path = config.firmware_dir / f"{stem[: -len('.op1')]}-presets.zip"
    if output_path.exists() and not overwrite:
        return {
            "name": output_path.name,
            "bytes": output_path.stat().st_size,
            "sha256": _cached_installer_sha(output_path),
            "config_id": selection_id,
            "variant_id": variant_id,
            "presets_name": presets_path.name if presets_path.is_file() else None,
            "reused": True,
            "seconds": 0.0,
            "report": {},
        }

    base = json.loads((DATA_DIR / "base" / "manifest.json").read_text("utf-8"))
    variant_dir = DATA_DIR / "variants" / variant_id
    variant = json.loads((variant_dir / "manifest.json").read_text("utf-8"))
    if variant["release"]["id"] != payload["release"]["id"]:
        raise FirmwareError("vendored variant belongs to a different patch release")

    log_lines: list[str] = []
    started = time.monotonic()
    input_bytes = source_path.read_bytes()
    output_bytes, report = module.patch(
        input_bytes,
        base,
        variant,
        _blob_reader(DATA_DIR / "base", module),
        _blob_reader(variant_dir, module),
        log_lines.append,
        catalog=cat,
        config=normalized,
    )
    presets_bytes = module.filter_preset_zip(
        (variant_dir / "presets.zip").read_bytes(), normalized, cat
    )
    finished = time.monotonic()

    _atomic_write(output_path, output_bytes)
    _atomic_write(presets_path, presets_bytes)

    return {
        "name": output_path.name,
        "bytes": len(output_bytes),
        "sha256": sha256_bytes(output_bytes),
        "config_id": selection_id,
        "variant_id": variant_id,
        "presets_name": presets_path.name,
        "presets_bytes": len(presets_bytes),
        "reused": False,
        "seconds": round(finished - started, 1),
        "report": report,
        "log": [str(line) for line in log_lines][-40:],
    }


def _atomic_write(destination: Path, data: bytes) -> None:
    partial = destination.with_name(f".{destination.name}.{os.getpid()}.part")
    partial.write_bytes(data)
    partial.replace(destination)


def presets_file(config: StudioConfig, raw_id: str) -> Path:
    selection_id = _safe_config_id(raw_id)
    stem = build_output_name(f"{release()['release']['product']['slug']} {selection_id}")
    path = config.firmware_dir / f"{stem[: -len('.op1')]}-presets.zip"
    if not path.is_file():
        raise FileNotFoundError(f"no hundekraft preset pack for config {selection_id}")
    return path
