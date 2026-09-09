"""A held-out corpus, written to be unlike the one the rules were tuned on.

FROZEN. Nothing in this file may be edited in response to a rule failing
against it, and no rule may be changed because of what it finds here. The
moment either happens this stops being held out and every number measured on
it becomes as meaningless as the in-sample ones.

Why it exists: the sensitive lexicon in app/services/sensitive.py was
extended after running it against corpus.jsonl and finding five misses. The
corpus and the rules therefore have the same author and the same blind spots,
so "28 of 28 caught" measures whether I remembered the cases I thought of --
not whether the rules generalise.

What makes this different rather than merely separate:

  - A different industry. Freight and warehousing, not B2B software. Almost
    no shared vocabulary.
  - Sensitive disclosures phrased with terms deliberately absent from the
    lexicon: dialysis rather than biopsy, insulin rather than medication,
    a gate code rather than a password.
  - Near misses drawn from logistics idiom, where words like "health",
    "party" and "fasting" occur in ordinary business senses.

It is still written by the same author, so this is *less* in-sample rather
than genuinely out of sample. Expect misses. Report them.
"""

PERSONA = {
    "user": "Meera Krishnan",
    "role": "operations lead",
    "company": "Haulr",
    "domain": "regional freight and warehousing, Chennai",
}

# --- ordinary work ----------------------------------------------------------

WORK = [
    "Consignment for Coimbatore is loaded, LR number goes out this evening.",
    "Two trucks off the road at Krishnagiri, tyre burst on one.",
    "Warehouse three is at ninety percent, we cannot take the Salem stock.",
    "Diesel went up again, that is the third revision this quarter.",
    "The Trichy route is running four hours behind because of the checkpost.",
    "Driver allowances need revising, they have not moved since last year.",
    "PO from Sundaram Fasteners came through, forty tonnes a month.",
    "Loading bay two is blocked till they clear the returns.",
    "The e-way bill for the Hosur load expired, redoing it now.",
    "Night shift is short two loaders again.",
    "Cold chain unit at the Ambattur depot is throwing errors.",
    "Insurance renewal is due before the end of the month.",
    "Rates for the Bangalore lane hold at eighteen a kilo.",
    "Moving the Salem dispatch to Thursday, the customer cannot receive.",
    "Fleet utilisation is at seventy two percent this week.",
    "Signed the annual contract with Sundaram at eighteen rupees a kilo.",
    "We are switching the Hosur lane to the new transporter from next month.",
    "Vendor payments go out on the fifth from now on, not the first.",
    "Kumar is taking over the Coimbatore depot from Ravi.",
    "The GPS units on the older trucks are being replaced this quarter.",
    "Detention charges start after four hours, that is the new policy.",
    "Ravi will send the reconciliation before Friday.",
    "Warehouse audit is on the twelfth.",
    "The Salem customer wants a dedicated vehicle, quoting them separately.",
    "Fuel cards are moving to the new provider next week.",
    "Told the team no dispatches after eight at night, it is not safe.",
    "Always put the LR number in the subject line, makes tracing possible.",
    "I want the daily report as a table, not paragraphs.",
    "Loader wages are going up by eight percent from October.",
    "Three claims pending with the insurer, oldest is from June.",
]

FOLLOW_UPS = [
    "Still stuck at the checkpost.",
    "Cleared now.",
    "Same as yesterday.",
    "He is handling it.",
    "She said she would call back.",
    "Pushed to next week.",
    "They have not confirmed yet.",
    "Sorted, ignore the earlier message.",
    "Reopened, the vehicle broke down again.",
    "Changed my mind, sending the bigger truck.",
]

LOGISTICS_CHATTER = [
    "On the way to the depot.",
    "Call me when you are free.",
    "Sent the sheet.",
    "Checking now.",
    "Will confirm in an hour.",
    "Running late, traffic on GST road.",
    "In a meeting with the transporter.",
    "Back at the office by four.",
    "Missed your call.",
    "Bumping this one.",
]

# --- sensitive: phrased with vocabulary the lexicon was never shown ---------

SENSITIVE = {
    "credentials": [
        "Gate code for the Ambattur warehouse is 4471, do not share it around.",
        "My netbanking user id is meerak and the login pin is 8823.",
        "The tracker box has an SSH key on it, Ravi set it up.",
        "Wifi at the depot is haulr2026, tell the new loaders.",
    ],
    "financial_account": [
        "Our GSTIN is 33AABCU9603R1ZM for the invoice.",
        "Driver salary account is with Indian Bank, the branch is Ambattur.",
        "He gave me his account details over the phone, I have written them down.",
        "The company debit card is in the safe, spending limit is fifty thousand.",
    ],
    "health": [
        "Amma's dialysis schedule has changed to Tuesdays and Fridays.",
        "Ravi has been off with sciatica for two weeks now.",
        "I started insulin last month, so early meetings are difficult.",
        "One of the loaders had a seizure at the depot yesterday.",
    ],
    "religion": [
        "He fasts through Ramzan so the night shift is hard for him.",
        "The whole family goes to the Velankanni church every December.",
    ],
    "politics": [
        "The transport union is backing the DMK this time.",
        "My father has voted the same way for forty years.",
    ],
    "race_ethnicity": [
        "Most of our loaders come through the OBC quota hiring.",
        "He mentioned his community at the interview, which he should not have had to.",
    ],
    "sexual_orientation": [
        "Her partner is a woman, she mentioned it when we did the insurance forms.",
        "He is not out at work and I am not going to be the one who says anything.",
    ],
}

# --- near misses: the same words, ordinary business senses ------------------

NEAR_MISSES = [
    "The truck health check is due before the permit renewal.",
    "We need a proper diagnosis of why the route optimiser is slow.",
    "Party goods consignment for Diwali, handle it carefully.",
    "The fasting season always slows the Madurai deliveries.",
    "Church Street pickup point is closed for construction.",
    "Union negotiations start Monday, expect a slow week.",
    "Community warehouse in Salem has space if we need it.",
    "His account of what happened does not match the GPS log.",
    "Card payments at the fuel pump are down again.",
    "The Congress of freight operators meets in November.",
    "Bank holiday on Friday, no dispatches.",
    "Quota for the Bangalore lane is full this month.",
    "Race to get the Hosur load out before the checkpost closes.",
    "Blood bank consignment needs the cold chain vehicle.",
]

# --- junk: same shapes, different words -------------------------------------

JUNK = {
    "no_content": [
        "Check check.",
        "Testing.",
        "One two.",
        "Hello hello.",
    ],
    "discarded_by_user": [
        "No wait.",
        "Wrong chat.",
        "Cancel that.",
        "Start over.",
    ],
    "low_asr_confidence": [
        "The uh the the load for.",
        "So so the truck the.",
        "Um can you the the.",
        "Yeah the the other one uh.",
    ],
    "implausible_timing": [
        "Okay.",
        "Noted.",
    ],
}

RETRIES = [
    "Move the Salem dispatch to Thursday.",
    "Tell Ravi the reconciliation is due Friday.",
]

# --- what the holdout asserts, for the entity and supersession checks -------

EXPECTED_MEMORIES = [
    "Sundaram Fasteners contract is eighteen rupees a kilo",
    "Kumar took over the Coimbatore depot from Ravi",
    "Vendor payments go out on the fifth",
    "Detention charges start after four hours",
    "Loader wages rise eight percent from October",
    "No dispatches after eight at night",
]

EXPECTED_PREFERENCES = [
    "Prefers the LR number in the subject line",
    "Prefers the daily report as a table, not paragraphs",
]
