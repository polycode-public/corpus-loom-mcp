"""TOML configuration loading.

Config resolution order: explicit path argument, then $CORPUS_INDEX_CONFIG,
then ./corpus.toml. Relative paths inside the file (db, roots, seeds, aliases)
resolve against the config file's directory.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ENV_VAR = "CORPUS_INDEX_CONFIG"
DEFAULT_FILENAME = "corpus.toml"

SOURCE_TYPES = ("filetree", "eml_tree", "gitrepo")

DEFAULT_PROSE_MODEL = "voyage-3.5"
DEFAULT_CODE_MODEL = "voyage-code-3"
EMBEDDING_DIMS = 1024


class ConfigError(ValueError):
    """Invalid or missing configuration."""


@dataclass(frozen=True)
class SourceConfig:
    name: str
    type: str
    root: Path
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    branch: str | None = None
    convert: tuple[str, ...] = ()
    embed: bool = True
    embed_exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    db: Path
    sources: tuple[SourceConfig, ...]
    prose_model: str = DEFAULT_PROSE_MODEL
    code_model: str = DEFAULT_CODE_MODEL
    seeds: Path | None = None
    aliases: Path | None = None
    config_dir: Path = field(default_factory=Path.cwd)

    def source(self, name: str) -> SourceConfig:
        for s in self.sources:
            if s.name == name:
                return s
        raise KeyError(name)


def _expect(value: Any, typ: type, what: str) -> Any:
    if not isinstance(value, typ):
        raise ConfigError(
            f"{what}: expected {typ.__name__}, got {type(value).__name__} ({value!r})"
        )
    return value


def _str_list(value: Any, what: str) -> tuple[str, ...]:
    _expect(value, list, what)
    for i, item in enumerate(value):
        _expect(item, str, f"{what}[{i}]")
    return tuple(value)


def _resolve(raw: str, base: Path) -> Path:
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (base / p).resolve()


def _parse_source(raw: Any, index: int, base: Path) -> SourceConfig:
    where = f"sources[{index}]"
    _expect(raw, dict, where)
    known = {
        "name", "type", "root", "include", "exclude",
        "branch", "convert", "embed", "embed_exclude",
    }
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"{where}: unknown keys {sorted(unknown)}")

    for key in ("name", "type", "root"):
        if key not in raw:
            raise ConfigError(f"{where}: missing required key '{key}'")

    name = _expect(raw["name"], str, f"{where}.name")
    if not name:
        raise ConfigError(f"{where}.name: must be non-empty")
    where = f"source '{name}'"

    stype = _expect(raw["type"], str, f"{where}.type")
    if stype not in SOURCE_TYPES:
        raise ConfigError(
            f"{where}.type: {stype!r} is not one of {', '.join(SOURCE_TYPES)}"
        )

    branch = None
    if "branch" in raw:
        branch = _expect(raw["branch"], str, f"{where}.branch")
        if stype != "gitrepo":
            raise ConfigError(f"{where}.branch: only valid for type 'gitrepo'")

    convert = ()
    if "convert" in raw:
        convert = tuple(
            ext.lower().lstrip(".")
            for ext in _str_list(raw["convert"], f"{where}.convert")
        )
        if any(not ext for ext in convert):
            raise ConfigError(f"{where}.convert: empty extension")

    embed = True
    if "embed" in raw:
        embed = _expect(raw["embed"], bool, f"{where}.embed")

    return SourceConfig(
        name=name,
        type=stype,
        root=_resolve(_expect(raw["root"], str, f"{where}.root"), base),
        include=_str_list(raw.get("include", []), f"{where}.include"),
        exclude=_str_list(raw.get("exclude", []), f"{where}.exclude"),
        branch=branch,
        convert=convert,
        embed=embed,
        embed_exclude=_str_list(raw.get("embed_exclude", []), f"{where}.embed_exclude"),
    )


def parse_config(data: dict[str, Any], config_dir: Path) -> Config:
    known = {"db", "sources", "models", "entities"}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown top-level keys {sorted(unknown)}")

    if "db" not in data:
        raise ConfigError("missing required key 'db'")
    db = _resolve(_expect(data["db"], str, "db"), config_dir)

    raw_sources = data.get("sources", [])
    _expect(raw_sources, list, "sources")
    if not raw_sources:
        raise ConfigError("at least one [[sources]] entry is required")
    sources = tuple(
        _parse_source(raw, i, config_dir) for i, raw in enumerate(raw_sources)
    )
    names = [s.name for s in sources]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ConfigError(f"duplicate source names: {sorted(dupes)}")

    models = data.get("models", {})
    _expect(models, dict, "models")
    unknown = set(models) - {"prose", "code"}
    if unknown:
        raise ConfigError(f"models: unknown keys {sorted(unknown)}")
    prose_model = _expect(models.get("prose", DEFAULT_PROSE_MODEL), str, "models.prose")
    code_model = _expect(models.get("code", DEFAULT_CODE_MODEL), str, "models.code")

    entities = data.get("entities", {})
    _expect(entities, dict, "entities")
    unknown = set(entities) - {"seeds", "aliases"}
    if unknown:
        raise ConfigError(f"entities: unknown keys {sorted(unknown)}")
    seeds = aliases = None
    if "seeds" in entities:
        seeds = _resolve(_expect(entities["seeds"], str, "entities.seeds"), config_dir)
    if "aliases" in entities:
        aliases = _resolve(
            _expect(entities["aliases"], str, "entities.aliases"), config_dir
        )

    return Config(
        db=db,
        sources=sources,
        prose_model=prose_model,
        code_model=code_model,
        seeds=seeds,
        aliases=aliases,
        config_dir=config_dir,
    )


def find_config_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env).expanduser()
    return Path(DEFAULT_FILENAME)


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    resolved = find_config_path(path)
    if not resolved.is_file():
        raise ConfigError(f"config file not found: {resolved}")
    try:
        with open(resolved, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{resolved}: invalid TOML: {e}") from e
    return parse_config(data, resolved.resolve().parent)
