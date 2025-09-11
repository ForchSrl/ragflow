from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ruamel.yaml import YAML


@dataclass
class Metric:
    name: str
    expression: str  # e.g., SUM(orders.amount)
    description: str = ""
    defaults: Dict[str, str] = field(default_factory=dict)  # e.g., time grain


@dataclass
class Dimension:
    name: str
    column: str
    description: str = ""


@dataclass
class Entity:
    name: str
    table: str
    description: str = ""
    dimensions: List[Dimension] = field(default_factory=list)


@dataclass
class SemanticModel:
    db: str
    entities: Dict[str, Entity] = field(default_factory=dict)
    metrics: Dict[str, Metric] = field(default_factory=dict)
    synonyms: Dict[str, str] = field(default_factory=dict)  # business term -> column/table


def load_yaml(path: str) -> dict:
    yaml = YAML(typ="safe")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.load(f) or {}


def load_semantic_model(path: str) -> SemanticModel:
    data = load_yaml(path)
    db = data.get("db") or ""
    model = SemanticModel(db=db)

    # Entities
    for e in data.get("entities", []) or []:
        ent = Entity(
            name=e.get("name"),
            table=e.get("table"),
            description=e.get("description", ""),
        )
        for d in e.get("dimensions", []) or []:
            ent.dimensions.append(
                Dimension(name=d.get("name"), column=d.get("column"), description=d.get("description", ""))
            )
        model.entities[ent.name] = ent

    # Metrics
    for m in data.get("metrics", []) or []:
        metric = Metric(
            name=m.get("name"),
            expression=m.get("expression"),
            description=m.get("description", ""),
            defaults=m.get("defaults", {}) or {},
        )
        model.metrics[metric.name] = metric

    # Synonyms
    model.synonyms = data.get("synonyms", {}) or {}
    return model

