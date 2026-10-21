"""Regex marker lists for scoring (R12, G23, T§5 Scoring).

Developer-defined lists; tests/data/phrases.py pins them with fixture phrases.
"""
import re

_I = re.IGNORECASE

# (name, weight, pattern) amygdala marker table.
AMYGDALA = [
    ("absolute", 0.40, re.compile(r"\b(never|always|must|must\s+not|mustn'?t)\b", _I)),
    ("incident", 0.35, re.compile(r"\b(broke|broken|breaks|outage|data[\s-]?loss|lost\s+data|downtime)\b", _I)),
    ("critical", 0.30, re.compile(r"\b(critical|urgent|urgently|security|vulnerab\w*|cve-\d+)\b", _I)),
    ("correction", 0.30, re.compile(
        r"\b(no,?\s+(that'?s|that\s+is|it'?s)\s+(wrong|incorrect|not)|that'?s\s+(wrong|incorrect)|"
        r"that\s+is\s+(wrong|incorrect)|you'?re\s+wrong|actually,?\s+no|not\s+what\s+i\s+(asked|meant))\b", _I)),
    ("decision", 0.25, re.compile(
        r"\b(we\s+(decided|chose|agreed|picked|will\s+use)|decided\s+(to|on)|decision|"
        r"going\s+with|let'?s\s+(use|go\s+with)|we'?re\s+(using|going\s+with))\b", _I)),
    ("remember", 0.25, re.compile(r"\b(remember\s+(this|that)|please\s+remember|don'?t\s+forget|note\s+that|keep\s+in\s+mind)\b", _I)),
    ("error", 0.20, re.compile(r"\b(error|errors|exception|traceback|bug|bugs|crash\w*|fail(ed|s|ing|ure)?|regression)\b", _I)),
    ("deadline", 0.20, re.compile(r"\b(deadline|due\s+(by|on|date)|by\s+(monday|tuesday|wednesday|thursday|friday|eod|end\s+of)|ship\s+by|before\s+the\s+release)\b", _I)),
    ("causal", 0.20, re.compile(r"\b(because|since\s+the|due\s+to|caused|causes|therefore|as\s+a\s+result|leads?\s+to|root\s+cause)\b", _I)),
    ("deprecation", 0.15, re.compile(r"\b(deprecat\w*|migrat\w*|legacy|end[\s-]of[\s-]life|sunset)\b", _I)),
    ("emphasis", 0.15, re.compile(r"(\b[A-Z]{3,}\b(\s+\b[A-Z]{2,}\b)+|!{2,}|\bIMPORTANT\b)")),
]

SHORT_WORDS = 4
SHORT_PENALTY = 0.5
LONG_WORDS = 20
LONG_BONUS = 0.05

# Content-type signals (no affect, still durable).
IDENTITY = re.compile(
    r"\b(i\s*am|i'?m|my\s+(role|name|job|title|team)|i\s+(own|lead|manage|maintain|work\s+(on|at|for)))\b"
    r".*|\b(we\s+own|owned\s+by|owner\s+of)\b", _I)
DECISION = AMYGDALA[4][2]
CAUSAL = AMYGDALA[8][2]
RULE = re.compile(
    r"\b(convention|policy|policies|guideline|standard|rule|style\s+guide|naming|"
    r"should\s+(always|never)|we\s+(always|never|require)|required\s+to)\b", _I)

# Longest suffix first so "seconds" is never read as bare "s", "ms" before "s".
UNITS = ["milliseconds", "millisecond", "seconds", "second", "minutes", "minute", "hours", "hour",
         "msec", "ms", "secs", "sec", "mins", "min", "rps", "qps", "mb", "gb", "kb", "%", "$", "s", "h"]
_UNIT_ALT = "|".join(re.escape(u) for u in sorted(UNITS, key=len, reverse=True))
QUANTITY = re.compile(
    r"(?:\$\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s?(?:%|(?:" + _UNIT_ALT + r")\b))", _I)

# classify cues (checked in this order).
CLASS_IDENTITY = re.compile(r"\b(i\s*am|i'?m|my\s+(role|name|job|title|team)|call\s+me|i\s+(lead|own|manage))\b", _I)
CLASS_PROCEDURAL = re.compile(
    r"^\s*(to\s+\w+|run|use|call|set|add|install|build|deploy|restart|check|open|click|make\s+sure|"
    r"first|then|always\s+run|step\s+\d)\b|\b(how\s+to|steps?:|in\s+order\s+to|workflow|recipe|"
    r"then\s+(run|use|call)|before\s+(you\s+)?(run|deploy|commit|push))\b", _I)
CLASS_SEMANTIC = re.compile(
    r"\b(because|decided|decision|we\s+use|uses|is\s+(a|an|the)|are|runs\s+on|configured|"
    r"requires|caused|due\s+to|since|therefore|currently|stored\s+in)\b", _I)
