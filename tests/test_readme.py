"""Checks that the data model diagram, the example records and the variable overview of the README match the schema.

The entity relationship diagram lists every field of every dataset and of the
nested objects, the README shows one example record per dataset, and the
variable overview names fields of the datasets and every chart. These tests
fail when a field is added, renamed or removed in ``schema.py``, or a chart
in ``config/charts.toml``, without updating the README.
"""
import json
import re
from pathlib import Path

import pytest

from chart_catalog import load_chart_config
from schema import DATASET_MODELS, NESTED_MODELS, RecordBase

README = Path(__file__).resolve().parents[1] / "README.md"
COMMON_ENTITY = "common_fields"


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def snake_case(name: str) -> str:
    """Returns the snake case form of a model name, e.g. ``chart_entry`` for ``ChartEntry``."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def diagram_entities(text: str) -> dict[str, list[str]]:
    """Returns the attribute names of every entity of the erDiagram in the README.

    Args:
        text: The README.

    Returns:
        dict[str, list[str]]: Attribute names in diagram order, by entity name.
    """
    match = re.search(r"```mermaid\s*\nerDiagram\n(.*?)```", text, flags=re.DOTALL)
    assert match, "README has no erDiagram"
    entities: dict[str, list[str]] = {}
    for name, body in re.findall(r"^\s*(\w+)\s*\{\n(.*?)^\s*\}", match.group(1), flags=re.DOTALL | re.MULTILINE):
        entities[name] = [line.split()[1] for line in body.strip().splitlines() if line.strip()]
    return entities


def example_records(text: str) -> dict[str, list[dict]]:
    """Returns the example records of the README by dataset.

    Args:
        text: The README.

    Returns:
        dict[str, list[dict]]: Parsed JSON examples by dataset name.
    """
    section = text.split("## Example Records", 1)[1].split("\n## ", 1)[0]
    examples: dict[str, list[dict]] = {}
    for dataset, body in re.findall(r"<summary><code>(\w+)</code>.*?</summary>\s*```json\n(.*?)\n```", section,
                                    flags=re.DOTALL):
        examples.setdefault(dataset, []).append(json.loads(body))
    return examples


def test_common_fields_entity_lists_the_fields_of_every_record() -> None:
    entities = diagram_entities(_readme())
    assert entities[COMMON_ENTITY] == list(RecordBase.model_fields)


@pytest.mark.parametrize("dataset", list(DATASET_MODELS))
def test_dataset_entity_lists_all_fields_of_its_model(dataset: str) -> None:
    entities = diagram_entities(_readme())
    expected = [name for name in DATASET_MODELS[dataset].model_fields if name not in RecordBase.model_fields]
    assert sorted(entities.get(dataset, [])) == sorted(expected)


@pytest.mark.parametrize("model", NESTED_MODELS, ids=lambda model: model.__name__)
def test_nested_entity_lists_all_fields_of_its_model(model: type) -> None:
    entities = diagram_entities(_readme())
    assert sorted(entities.get(snake_case(model.__name__), [])) == sorted(model.model_fields)


def test_every_diagram_attribute_has_a_comment() -> None:
    match = re.search(r"```mermaid\s*\nerDiagram\n(.*?)```", _readme(), flags=re.DOTALL)
    assert match
    attribute_lines = [line.strip() for line in match.group(1).splitlines()
                       if line.strip() and not line.strip().endswith(("{", "}")) and "--" not in line
                       and ".." not in line]
    assert attribute_lines
    assert all(re.search(r'"[^"]+"$', line) for line in attribute_lines), \
        [line for line in attribute_lines if not re.search(r'"[^"]+"$', line)]


@pytest.mark.parametrize("dataset", list(DATASET_MODELS))
def test_example_record_validates_against_its_model(dataset: str) -> None:
    examples = example_records(_readme())
    assert len(examples.get(dataset, [])) == 1, f"README needs exactly one example of {dataset}"
    DATASET_MODELS[dataset].model_validate(examples[dataset][0])


def variable_overview_rows(text: str) -> list[tuple[str, str]]:
    """Returns the variable and the dataset of every row of the variable overview of the README.

    Args:
        text: The README.

    Returns:
        list[tuple[str, str]]: Text of the variable cell and dataset name, in table order.
    """
    section = text.split("## Variable Overview", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^\| (.+?) \| `(\w+)` \|", section, flags=re.MULTILINE)


def test_variable_overview_names_content_fields_of_the_datasets() -> None:
    rows = [(variable, dataset) for variable, dataset in variable_overview_rows(_readme())
            if not variable.startswith("Chart ")]
    assert rows
    for variable, dataset in rows:
        name = variable.strip("`")
        assert name in DATASET_MODELS[dataset].model_fields, (variable, dataset)
        assert name not in RecordBase.model_fields, (variable, dataset)


def test_variable_overview_lists_every_chart() -> None:
    rows = variable_overview_rows(_readme())
    charts = [re.fullmatch(r"Chart `([\w.]+)`", variable).group(1) for variable, dataset in rows
              if variable.startswith("Chart ") and dataset == "chart_snapshots"]
    assert charts == [chart.id for chart in load_chart_config().active_charts]
