"""One exact reviewed CHECK spelling pair; archive bytes always stay unchanged.

PostgreSQL reparses pg_dump's CHECK SQL and flattens this nested conjunction.
This is deliberately not a general SQL normalizer or a license to ignore CHECKs.
"""

from copy import deepcopy

IDENTITY = (
    "historical_input",
    "chapter_payloads_raw_content_check",
    "chapter_payloads",
    "",
)
NESTED = "CHECK ((((octet_length(raw_content) >= 1) AND (octet_length(raw_content) <= 2097152)) AND (jsonb_typeof((raw_content)::jsonb) = 'object'::text)))"
FLAT = "CHECK (((octet_length(raw_content) >= 1) AND (octet_length(raw_content) <= 2097152) AND (jsonb_typeof((raw_content)::jsonb) = 'object'::text)))"


def canonical(definitions):
    result = deepcopy(definitions)
    for row in result.get("constraints", []):
        if tuple(row[:4]) == IDENTITY and len(row) == 5 and row[4] in (NESTED, FLAT):
            row[4] = FLAT
    return result


def equivalent(expected, actual):
    """Preserve order, every other field, identity and every unrecognized SQL byte."""
    return canonical(expected) == canonical(actual)
