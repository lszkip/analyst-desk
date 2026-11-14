"""Narrow deterministic detection of explicit instructions inside source evidence.

Original text is never rewritten. A caller may retain a flagged passage for
inspection while making the whole passage ineligible as financial/risk support.
This is NOT a truth classifier or a general prompt-injection solution: ordinary
false assertions, indirect/encoded/multilingual attacks, cross-passage attacks
and unrecognised syntax can evade it. Quarantine can discard legitimate adjacent
facts; obtain independent clean evidence rather than silently removing a sentence.
Explicit executable directives are checked even inside quotations or security
training examples: source-authored labels cannot safely exempt their own text.
This deliberately quarantines some legitimate examples that quote such attacks.
"""
import re
import unicodedata

SOURCE_INSTRUCTION_POLICY_VERSION = 'explicit-source-instructions-v1'

_BOUNDARY = r"(?:^|[.!?;:\n])[\"”'’)\]]{0,2}\s*"
_ROLES = r'(?:writer|assistant|critic|reviewer|researcher|publisher|supervisor|agent|model|system)'
_CONTROL_VERB = r'(?:ignore|disregard|override|bypass|invent|fabricate|write|state|assert|present|treat|cite|output|include|put|replace|rewrite|conclude|call|invoke|execute|approve|fetch|become|make)'
_OUTPUT = r'(?:answer|response|output|draft|paragraph|summary|assertion|claim|statement|evidence|citation|source|risk|recommendation|tool|handoff|role|instructions?|permission|approval|fact|result|report)'

_RULES = (
    ('source_role_directive', re.compile(
        r'\b'+_ROLES+r'(?:[-\s]+(?:only|correction|editorial|role|behavior|behaviour|workflow|output|final|system)){0,4}'
        r'\s+(?:instructions?|directive|override|update|handoff|correction)\s*:'
        r'[\s\S]{0,300}?\b'+_CONTROL_VERB+r'\b[\s\S]{0,200}?\b'+_OUTPUT+r'\b', re.I)),
    ('source_output_override', re.compile(
        _BOUNDARY+r'(?:please\s+)?(?:replace|rewrite|discard|disregard|override|ignore|alter|change)\s+'
        r'(?:[\w-]+\s+){0,8}(?:risk\s+(?:assertion|statement|disclosure)|factual\s+(?:summary|statement)|'
        r'(?:final|previous|earlier|original)\s+(?:paragraph|answer|response|instructions?|statement)|'
        r'(?:source|disclosed)\s+(?:fact|risk|assertion)|assigned\s+(?:company|role)|branch\s+assignment)\b', re.I)),
    ('source_fabrication_directive', re.compile(
        _BOUNDARY+r'(?:please\s+)?(?:invent|fabricate|make\s+up)\s+(?:[\w-]+\s+){0,8}'
        r'(?:claim|result|fact|number|figure|evidence|citation|source|receipt|record|assurance|guarantee)\b', re.I)),
    ('source_evidence_laundering', re.compile(
        _BOUNDARY+r'(?:please\s+)?(?:present|treat|report|state|assert|describe|cite|use)\s+'
        r'(?:this|that|the|an?)\s+(?:invented|fabricated|unsupported|unverified|false)\s+'
        r'(?:claim|result|fact|evidence|citation|source|receipt|record|id|assurance|guarantee)\b'
        r'[\s\S]{0,100}?\bas\s+(?:(?:an?|the)\s+)?(?:verified|established|true|fact|evidence|proof)\b', re.I)),
    ('source_tool_instruction', re.compile(
        _BOUNDARY+r'(?:please\s+)?(?:call|invoke|execute|fetch|run)\s+[a-z][a-z0-9]*_[a-z0-9_]+\s+with\s*\{', re.I)),
    ('source_authority_override', re.compile(
        r'\b(?:your\s+next\s+action\s+must\s+be|'
        r'this\s+(?:source|paragraph|filing)\s+(?:overrides|changes|grants)\s+(?:[\w-]+\s+){0,5}(?:role|permission|authority|period|company|instructions?)|'
        r'treat\s+this\s+(?:source|paragraph|filing)\s+as\s+(?:[\w-]+\s+){0,5}(?:higher\s+priority|system\s+message|supervisor\s+message)|'
        r'follow\s+(?:the|this|that)\s+(?:quoted\s+)?(?:attack|example|instruction)\s+(?:as\s+(?:a\s+)?command|instead\s+of\s+(?:your|the)\s+(?:system|original)\s+instructions))\b', re.I)),
    ('source_required_output', re.compile(
        r'\b(?:required|mandatory)\s+(?:final\s+wording|editorial\s+conclusion|positive\s+spin|conclusion\s+for\s+the\s+assistant)\s*:'
        r'[\s\S]{0,260}?\b(?:replace|write|state|output|present|include)\b', re.I)),
    ('source_editorial_override', re.compile(
        r'\b(?:editorial|citation|provenance|document\s+integrity)\s+(?:repair\s+)?'
        r'(?:instruction|override|directive)(?:\s+for\s+the\s+'+_ROLES+r')?\s*:'
        r'[\s\S]{0,260}?\b'+_CONTROL_VERB+r'\b', re.I)),
)


def _comparison_text(text):
    # This representation is used only for recognition. Original source bytes,
    # offsets, hashes, citations and returned source text remain the caller's.
    compared = unicodedata.normalize('NFKC', text)
    compared = ''.join(c for c in compared if unicodedata.category(c) != 'Cf')
    return compared


def source_instruction_findings(text):
    """Return stable rule codes for explicit source-to-agent directives.

    Inspect the COMPLETE original passage, never only a selected quote or a
    preview. Findings contain no transformed source text and no misleading
    original-text offsets. Callers decide whether a record is ineligible as
    evidence; this function does not modify data, grant trust or execute content.
    """
    if not isinstance(text,str):
        raise TypeError('Full source text must be a string.')
    compared = _comparison_text(text)
    return [{'code':code} for code,pattern in _RULES if pattern.search(compared)]
