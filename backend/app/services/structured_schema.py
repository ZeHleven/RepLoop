"""Project a bounded Pydantic schema into DeepSeek's supported strict subset.

Array/string length limits are described, not sent as unsupported keywords.
The original model remains the authoritative validator on every transport.
https://api-docs.deepseek.com/guides/tool_calls/
"""
from copy import deepcopy
from typing import Any

from pydantic import BaseModel


def strict_tool_schema(model: type[BaseModel]) -> dict[str, Any]:
    root = model.model_json_schema()
    definitions = root.get("$defs", {})

    def project(node: dict[str, Any]) -> dict[str, Any]:
        node = deepcopy(node)
        if "$ref" in node:
            reference = node.pop("$ref")
            if not reference.startswith("#/$defs/"):
                raise ValueError("Only local, non-recursive model definitions are supported")
            node = {**deepcopy(definitions[reference.removeprefix("#/$defs/")]), **node}
        notes = []
        for key, label in (("minItems", "最少条数"), ("maxItems", "最多条数"),
                           ("minLength", "最少字符数"), ("maxLength", "最多字符数")):
            if key in node:
                notes.append(f"{label}：{node.pop(key)}")
        for key in ("title", "default", "$defs"):
            node.pop(key, None)
        if notes:
            node["description"] = "；".join(filter(None, [node.get("description", ""), *notes]))
        if "properties" in node:
            node["properties"] = {key: project(value) for key, value in node["properties"].items()}
            node["required"] = list(node["properties"])
            node["additionalProperties"] = False
        if isinstance(node.get("items"), dict):
            node["items"] = project(node["items"])
        for key in ("anyOf", "allOf", "oneOf"):
            if key in node:
                node[key] = [project(value) for value in node[key]]
        return node

    return project(root)
