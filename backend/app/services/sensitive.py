"""Content categories that are refused at the door.

A dictation on a sensitive topic is not stored at all. Keeping it and merely
declining to index it leaves the text in the database, which is a weaker
promise than not having it -- and once the toggle exists in the interface,
"we never stored it" is the only claim worth making.

Refusal is unrecoverable, so detection is deliberately narrow. Every rule
below either matches a structural pattern that is almost never anything else
(a key, a card number) or requires two independent signals to agree. A missed
category costs privacy; a false positive costs the user a dictation they will
never get back, and they will not know what it said.

Detection is deterministic and runs on the write path, so it can never fail
because a provider is down. What a lexicon cannot see -- meaning that only
emerges across a whole conversation -- is caught later by the model that
already reads every episode, which then purges what it finds.
"""

import re

SENSITIVE_CATEGORIES: tuple[str, ...] = (
    "credentials",
    "financial_account",
    "health",
    "religion",
    "politics",
    "race_ethnicity",
    "sexual_orientation",
)

REJECTION_RULE = "sensitive_category"

DETECTION_IMPLEMENTED = True


# --- structural patterns ----------------------------------------------------
#
# These match a shape rather than a subject. A 16-digit card number is a card
# number whatever sentence it sits in, so they stand alone.

_STRUCTURAL: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "credentials",
        re.compile(
            r"\b(?:password|passphrase|passcode|api[ _-]?key|secret[ _-]?key"
            r"|access[ _-]?token|auth[ _-]?token|bearer[ _-]?token|private[ _-]?key"
            r"|otp|one[ -]time[ -](?:password|code)|two[ -]factor[ -]code)\b"
            r"\s*(?:is|=|:|was|will be)?\s*\S+",
            re.IGNORECASE,
        ),
    ),
    (
        "credentials",
        # Provider key prefixes, which are unmistakable on their own. Each
        # is a published, documented shape -- not a guess about what a secret
        # looks like, which is why they can stand alone.
        re.compile(
            r"\b(?:sk|pk|rk)[-_](?:live|test|prod)?[-_]?[A-Za-z0-9]{16,}\b"
            r"|\bgh[pousr]_[A-Za-z0-9]{20,}\b"
            r"|\bgithub_pat_[A-Za-z0-9_]{20,}\b"
            r"|\bxox[baprs]-[A-Za-z0-9-]{10,}\b"
            r"|\bAKIA[0-9A-Z]{16}\b"
            r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
        ),
    ),
    (
        "financial_account",
        # 13-19 digits, optionally grouped: a card number.
        re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    ),
    (
        "financial_account",
        re.compile(
            r"\b(?:cvv|cvc|ifsc|iban|swift|sort[ -]code|routing[ -]number"
            r"|account[ -]number|upi[ -]?(?:id|pin)|card[ -]number)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "financial_account",
        # A partial account number is still an account number.
        re.compile(r"\baccount\s+ending\s+\d{3,}", re.IGNORECASE),
    ),
    (
        "financial_account",
        # Indian PAN and Aadhaar, which have fixed shapes.
        re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    ),
)


# --- lexical categories -----------------------------------------------------
#
# A topic word alone is not evidence: "the launch is healthy" is not medical,
# and "the political situation with the vendor" is not politics. Each category
# needs a subject term and a context term, both present.

_LEXICAL: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "health": (
        frozenset({
            "diagnosis", "diagnosed", "biopsy", "chemotherapy", "chemo",
            "prescription", "prescribed", "symptoms", "mri", "ultrasound",
            "blood test", "blood sugar", "cholesterol", "depression",
            "anxiety", "therapy", "therapist", "psychiatrist", "medication",
            "surgery", "tumour", "tumor", "cancer", "diabetes", "pregnancy",
            "pregnant", "miscarriage", "hiv", "covid", "test results",
            "scan results",
        }),
        frozenset({
            "my", "mine", "i", "me", "he", "she", "they", "his", "her",
            "their", "wife", "husband", "mother", "father", "mum", "mom",
            "dad", "papa", "parents", "brother", "sister", "son", "daughter",
            "kid", "child", "doctor", "hospital", "clinic", "appointment",
            "report", "results", "treatment", "been", "since",
        }),
    ),
    "religion": (
        frozenset({
            "muslim", "hindu", "christian", "sikh", "jewish", "buddhist",
            "jain", "atheist", "converted", "conversion", "temple", "mosque",
            "church", "gurudwara", "synagogue", "namaz", "puja", "baptised",
            "baptized",
        }),
        frozenset({
            "my", "mine", "i", "me", "his", "her", "their", "family",
            "believes", "belief", "faith", "practising", "practicing",
            "devout", "religion", "religious",
        }),
    ),
    "politics": (
        frozenset({
            "bjp", "congress", "aap", "communist", "republican", "democrat",
            "labour party", "conservative party", "voted", "voting", "ballot",
            "election", "campaigned", "constituency",
        }),
        frozenset({
            "my", "i", "me", "he", "she", "they", "we", "his", "her", "their",
            "supports", "supporter", "against", "party", "politics",
            "political", "leaning", "views", "for",
        }),
    ),
    "race_ethnicity": (
        frozenset({
            "caste", "dalit", "brahmin", "adivasi", "obc", "scheduled caste",
            "scheduled tribe", "ethnicity", "ethnic", "racial", "immigrant",
            "immigration status", "refugee",
        }),
        frozenset({
            "my", "i", "me", "his", "her", "their", "family", "background",
            "community", "belongs", "because", "status",
        }),
    ),
    "sexual_orientation": (
        frozenset({
            "gay", "lesbian", "bisexual", "transgender", "queer", "asexual",
            "came out", "coming out", "orientation",
        }),
        frozenset({
            "my", "i", "me", "his", "her", "their", "partner", "identifies",
            "is", "friend", "brother", "sister",
        }),
    ),
}

_WORD = re.compile(r"[a-z']+")


def _terms(text: str) -> set[str]:
    """Single words plus adjacent pairs, so two-word terms can be matched.

    A contraction also yields its stem: "I've" has to count as "I", or a
    disclosure that opens with one slips past every context rule.
    """
    words = _WORD.findall(text.casefold())
    stems = {word.split("'")[0] for word in words if "'" in word}
    pairs = {f"{a} {b}" for a, b in zip(words, words[1:])}
    return set(words) | stems | pairs


def detect_sensitive(text: str | None) -> str | None:
    """The category this text falls into, or None if it is not sensitive.

    Checks run in SENSITIVE_CATEGORIES order, so text touching two categories
    always reports the same one and the refusal log stays reproducible.
    """
    if not text or not text.strip():
        return None

    found: set[str] = set()

    for category, pattern in _STRUCTURAL:
        if pattern.search(text):
            found.add(category)

    terms = _terms(text)
    for category, (subjects, contexts) in _LEXICAL.items():
        if terms & subjects and terms & contexts:
            found.add(category)

    for category in SENSITIVE_CATEGORIES:
        if category in found:
            return category
    return None
