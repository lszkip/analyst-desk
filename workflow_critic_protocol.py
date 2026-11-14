"""Short critic reference selections, bound to unchanged canonical receipts."""
from copy import deepcopy
from typing import Literal

from pydantic import Field, create_model

from workflow_contracts import WorkflowError, digest
from workflow_support import ParagraphSelection, SentenceSelection, support_catalog


CRITIC_PROTOCOL_VERSION = "critic-short-references-strict-schema-v1"
API_UNSUPPORTED_BOUNDS = frozenset({"minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum",
                                    "exclusiveMaximum", "multipleOf", "minItems", "maxItems", "uniqueItems",
                                    "minProperties", "maxProperties", "pattern", "format"})


def portable_api_schema(value):
    """Keep local Pydantic bounds; omit provider-dependent API keywords only."""
    if isinstance(value, dict):
        return {key: portable_api_schema(item) for key, item in value.items() if key not in API_UNSUPPORTED_BOUNDS}
    if isinstance(value, list):
        return [portable_api_schema(item) for item in value]
    return value


def critic_protocol(paragraph_index, paragraph, records):
    catalog = support_catalog(paragraph, records)
    aliases = {f"P{paragraph_index + 1}R{index + 1}": ref
               for index, ref in enumerate(catalog["references"])}
    view = {**deepcopy(catalog), "version": CRITIC_PROTOCOL_VERSION,
            "references": {alias: deepcopy(catalog["references"][ref]) for alias, ref in aliases.items()}}
    # An empty catalog still permits supported=false and reference_ids=[].
    # JSON Schema's enum must not be empty; maxItems=0 expresses that case.
    item_type = Literal[tuple(aliases)] if aliases else str
    sentence_type = create_model("CriticSentenceSelection", __base__=SentenceSelection,
        reference_ids=(list[item_type], Field(max_length=8 if aliases else 0)))
    selection_type = create_model("CriticParagraphSelection", __base__=ParagraphSelection,
        sentences=(list[sentence_type], Field(min_length=1, max_length=8)))
    final_schema = deepcopy(selection_type.model_json_schema())
    definitions = final_schema.pop("$defs", {})
    empty_arguments = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    passages = [key for key, source in catalog["sources"].items() if source["kind"] == "passage"]
    arguments = [empty_arguments]
    if passages:
        arguments.append({"type": "object", "properties": {"passage_id": {"type": "string", "enum": passages}},
                          "required": ["passage_id"], "additionalProperties": False})
    schema = {"type": "object", "properties": {
        "tool": {"type": "string", "enum": ["finish", *(["get_passage"] if passages else [])]},
        "arguments": {"anyOf": arguments}, "final": {"anyOf": [final_schema, {"type": "null"}]}},
        "required": ["tool", "arguments", "final"], "additionalProperties": False, "$defs": definitions}
    response_format = {"type": "json_schema", "json_schema": {
        "name": "critic_action", "strict": True, "schema": portable_api_schema(schema)}}
    return {"version": CRITIC_PROTOCOL_VERSION, "paragraph_index": paragraph_index, "catalog": catalog, "view": view,
            "aliases": aliases, "selection_type": selection_type, "response_format": response_format,
            "binding_sha256": digest([CRITIC_PROTOCOL_VERSION, paragraph_index, paragraph, catalog, aliases])}


def canonical_selection(selection, protocol, paragraph, records):
    """Translate exact selections only; never choose or infer supporting refs."""
    if protocol["catalog"] != support_catalog(paragraph, records):
        raise WorkflowError("changed_support_catalog", "The critic's paragraph or source catalog changed.")
    expected_aliases = {f"P{protocol['paragraph_index'] + 1}R{index + 1}": ref
                        for index, ref in enumerate(protocol["catalog"]["references"])}
    expected_view = {**deepcopy(protocol["catalog"]), "version": CRITIC_PROTOCOL_VERSION,
                     "references": {alias: deepcopy(protocol["catalog"]["references"][ref])
                                    for alias, ref in expected_aliases.items()}}
    if (protocol["aliases"] != expected_aliases or protocol["view"] != expected_view
            or protocol["binding_sha256"] != digest([CRITIC_PROTOCOL_VERSION, protocol["paragraph_index"], paragraph, protocol["catalog"], expected_aliases])):
        raise WorkflowError("changed_support_catalog", "The critic's short reference binding changed.")
    result = deepcopy(selection)
    for row in result["sentences"]:
        if any(ref not in protocol["aliases"] for ref in row["reference_ids"]):
            raise WorkflowError("unknown_support_reference", "Select only this paragraph's short reference IDs.")
        row["reference_ids"] = [protocol["aliases"][ref] for ref in row["reference_ids"]]
    return result
