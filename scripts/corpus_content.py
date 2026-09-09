"""The hand-authored material the corpus is built from.

Separated from the generator so the writing can be read as writing. Every
line here was typed by a person; the generator only places them on a
timeline, degrades them into ASR output, and edits them.

The style throughout follows corpus/spec.yaml's dictation_style: short,
elliptical, and assuming everything the speaker already knows. A person names
a colleague when they first come up and then says "he" for the rest of the
afternoon. Fragments are normal. Most of what anyone says in a day is not
worth remembering, and the filler pools are large for that reason.

`{who}` is a slot the generator fills with either a name or a pronoun,
depending on whether that person has already come up in the sitting.
"""

# --- the decision arcs ------------------------------------------------------
#
# Scripted beats, placed at fixed weeks. Only the opening beat of an arc
# states its subject; every later one is how a person actually revises
# something -- the delta alone, with the subject left implied.
#
# `asserts` is what ground truth will claim about this record. `supersedes`
# marks a beat that replaces an earlier value.

SCRIPTED = [
    # --- pricing: 499 -> 299 -> 349 -----------------------------------------
    dict(arc="pricing", week=1, day=1, app="slack", ctx="pricing",
         text="Putting list price at ₹499 for the Pro tier.",
         asserts="Pro tier list price is ₹499", kind="decision"),
    dict(arc="pricing", week=1, day=3, app="slack", ctx="pricing",
         text="{who} thinks that's steep for the SMB segment.",
         person="Aditya Sharma", asserts="Aditya thinks ₹499 is steep for SMBs",
         kind="fact"),
    dict(arc="pricing", week=2, day=2, app="gmail", ctx="abc",
         text="ABC came back on price. They're anchored around 250.",
         asserts="ABC Inc anchored around ₹250", kind="fact"),
    dict(arc="pricing", week=2, day=2, app="slack", ctx="pricing",
         text="Dropping to 299.",
         asserts="Pro tier price is ₹299", kind="decision",
         supersedes="Pro tier list price is ₹499"),
    dict(arc="pricing", week=2, day=4, app="notion", ctx="pricingdoc",
         text="Pro tier 299 a month, effective from the October launch.",
         asserts="Pro tier price is ₹299", kind="decision", corroborates=True),
    dict(arc="pricing", week=5, day=2, app="slack", ctx="pricing",
         text="Margin at 299 is thinner than we modelled. {who} is pulling the real numbers.",
         person="Pranav Iyer", asserts="Pranav is pulling margin numbers",
         kind="commitment"),
    dict(arc="pricing", week=9, day=1, app="slack", ctx="pricing",
         text="349 now. Margin came back better than we thought at the higher tier.",
         asserts="Pro tier price is ₹349", kind="decision",
         supersedes="Pro tier price is ₹299"),
    dict(arc="pricing", week=9, day=3, app="whatsapp", ctx="adi",
         text="went with 349 in the end",
         asserts="Pro tier price is ₹349", kind="decision", corroborates=True),

    # --- launch date: October -> November -> November 18 --------------------
    dict(arc="launch_date", week=3, day=1, app="linear", ctx="rules",
         text="Inbox rules v2 ships in October.",
         asserts="Inbox rules v2 launches in October", kind="decision"),
    dict(arc="launch_date", week=6, day=2, app="slack", ctx="eng",
         text="Gateway review is going to eat two weeks. Slipping to November.",
         asserts="Inbox rules v2 launches in November", kind="decision",
         supersedes="Inbox rules v2 launches in October"),
    dict(arc="launch_date", week=6, day=3, app="gmail", ctx="board",
         text="Confirming the launch moves to November.",
         asserts="Inbox rules v2 launches in November", kind="decision",
         corroborates=True),
    dict(arc="launch_date", week=10, day=2, app="linear", ctx="rules",
         text="Locking the 18th.",
         asserts="Inbox rules v2 launches on November 18", kind="decision",
         supersedes="Inbox rules v2 launches in November"),

    # --- payment gateway: Razorpay -> Cashfree ------------------------------
    dict(arc="gateway", week=2, day=1, app="notion", ctx="gatewaydoc",
         text="Going with Razorpay for the gateway migration.",
         asserts="Payment gateway is Razorpay", kind="decision"),
    dict(arc="gateway", week=5, day=1, app="slack", ctx="eng",
         text="Settlement is T+3 with them, which kills the refund flow. Switching to Cashfree.",
         asserts="Payment gateway is Cashfree", kind="decision",
         supersedes="Payment gateway is Razorpay"),
    dict(arc="gateway", week=5, day=4, app="cursor", ctx=None,
         text="swapping the gateway client over to cashfree",
         asserts="Payment gateway is Cashfree", kind="decision",
         corroborates=True),

    # --- SOC2 auditor: Sequoia Assurance -> Nomad Audit ---------------------
    dict(arc="soc2_auditor", week=6, day=1, app="gmail", ctx="soc2",
         text="We're going with Sequoia Assurance for the SOC2 audit.",
         asserts="SOC2 auditor is Sequoia Assurance", kind="decision"),
    dict(arc="soc2_auditor", week=8, day=2, app="slack", ctx="eng",
         text="Their quote came in at nearly double. Moving to Nomad Audit.",
         asserts="SOC2 auditor is Nomad Audit", kind="decision",
         supersedes="SOC2 auditor is Sequoia Assurance"),

    # --- standup time: 9:30 -> 10 -------------------------------------------
    dict(arc="standup", week=1, day=2, app="slack", ctx="general",
         text="Standup at 9:30 from tomorrow.",
         asserts="Standup is at 9:30am", kind="decision"),
    dict(arc="standup", week=4, day=1, app="slack", ctx="general",
         text="Nobody makes 9:30. Moving it to 10.",
         asserts="Standup is at 10am", kind="decision",
         supersedes="Standup is at 9:30am"),

    # --- inbox rules owner: Pranav -> Priya Menon ---------------------------
    dict(arc="inbox_owner", week=3, day=2, app="linear", ctx="rules",
         text="{who} owns inbox rules v2 end to end.",
         person="Pranav Iyer", asserts="Pranav Iyer owns Inbox rules v2",
         kind="fact"),
    dict(arc="inbox_owner", week=7, day=1, app="slack", ctx="eng",
         text="{who} is taking inbox rules, he's full time on SOC2 now.",
         person="Priya Menon", asserts="Priya Menon owns Inbox rules v2",
         kind="fact", supersedes="Pranav Iyer owns Inbox rules v2"),
]

# --- stated preferences -----------------------------------------------------
# Said out loud, once, in passing. Believed on one hearing because the user
# stated them outright.

STATED_PREFERENCES = [
    dict(week=2, app="slack", ctx="general",
         text="Release notes as short bullets from now on, never paragraphs.",
         asserts="Prefers release notes as short bullet points, never paragraphs"),
    dict(week=4, app="gmail", ctx="abc",
         text="Keep customer emails to three bullets max. Nobody reads past that.",
         asserts="Prefers customer emails to three bullets or fewer"),
    dict(week=5, app="slack", ctx="pricing",
         text="Always put the rupee figure in, don't just say the price changed.",
         asserts="Prefers the rupee figure stated explicitly, not 'the price'"),
    dict(week=7, app="notion", ctx="ops",
         text="I want meetings before noon. Afternoons are for building.",
         asserts="Prefers meetings before noon"),
    dict(week=8, app="gmail", ctx="trellis",
         text="Trellis get full sentences. They're formal and they notice.",
         asserts="Prefers full sentences when writing to Trellis Health"),
]

# --- the two named Priyas ---------------------------------------------------
# The ambiguity is the point. After the first mention each becomes "she", so
# nothing downstream can lean on the surname.

PRIYA_DISAMBIGUATION = [
    dict(week=7, day=3, app="slack", ctx="legal",
         text="Priya Raghavan is doing the DPA review, she's external counsel.",
         asserts="Priya Raghavan is external legal counsel"),
    dict(week=8, day=1, app="slack", ctx="legal",
         text="She's flagged the data residency clause.",
         asserts=None),
    dict(week=8, day=2, app="slack", ctx="design",
         text="Priya wants another pass on the empty states.",
         asserts=None, ambiguous_person=True),
]

# --- sensitive: must never be stored ----------------------------------------

SENSITIVE = {
    "credentials": [
        "The staging password is correct-horse-battery-staple, don't put it in the doc.",
        "API key for the sandbox is sk_test_4eC39HqLyjWDarjtT1zdp7dc.",
        "My laptop passcode is 449021 if you need to grab the file.",
        "Reset token came through, it's ghp_16C7e42F292c6912E7710c838347Ae178B4a.",
        "OTP is 771402, expires in a minute.",
        "The shared drive password is nomi2026admin, change it after.",
    ],
    "financial_account": [
        "Card number is 4111 1111 1111 1111, expiry next March.",
        "The CVV is 442, I'll read the rest out on the call.",
        "Vendor IFSC is HDFC0001234, account ending 3312.",
        "My PAN is ABCDE1234F for the contract.",
        "UPI id for reimbursements is rhea@okhdfcbank.",
        "Transfer ₹40,000 from the current account ending 3312 today.",
    ],
    "health": [
        "My biopsy results came back clear, huge relief.",
        "Moving the review, I have an MRI on Thursday morning.",
        "Dad's diabetes medication got changed again this week.",
        "I've been on anxiety medication since March, it's helping.",
        "Kavya's scan results are in and her doctor wants a follow up.",
    ],
    "religion": [
        "My family is quite devout, we do puja every morning before work.",
        "He converted last year and his family still doesn't know.",
    ],
    "politics": [
        "I voted for the Congress candidate in my constituency this time.",
        "She campaigned for the BJP before she joined us.",
    ],
    "race_ethnicity": [
        "His family belongs to a scheduled caste, he mentioned it at dinner.",
        "My immigration status is still pending, the visa is taking months.",
    ],
    "sexual_orientation": [
        "My brother came out to the family last year.",
        "She identifies as bisexual, she mentioned it in passing.",
    ],
}

# --- near misses: same vocabulary, must be stored ---------------------------
# A false refusal loses a dictation permanently and the user never learns
# what it said, so these matter more than the true positives.

NEAR_MISSES = [
    "Trellis Health want SSO before they'll sign.",
    "Launch metrics look healthy this quarter.",
    "We need a proper diagnosis of why checkout is failing.",
    "The political situation with the vendor is holding up the contract.",
    "Race condition in the retry handler, {who} is on it.",
    "Revenue is up 18% on last quarter.",
    "Trellis are in healthcare so the DPA is going to be painful.",
    "Card sort results came back, the nav is the problem.",
    "Bank holiday Monday, settlement will be late.",
    "The account team at ABC changed again.",
    "Checking the health of the queue workers before we ship.",
    "Their security review is basically an audit.",
    "Faith in that vendor is running low honestly.",
    "Party invite went out for the launch, keep it small.",
]

# --- junk: stored, never embedded -------------------------------------------

JUNK = {
    "no_content": [
        "Testing testing.",
        "Testing one two three.",
        "Mic check.",
        "brb",
        "Hello?",
        "Hey.",
        "Okay.",
        "Hmm.",
    ],
    "discarded_by_user": [
        "Actually never mind.",
        "Wait, that's wrong.",
        "Scratch that.",
        "Ignore this one.",
        "No hang on.",
        "Testing the new mic setup.",
        "Sorry wrong window.",
        "Let me start again.",
    ],
    "low_asr_confidence": [
        "The the uh sorry the margin on.",
        "Can you the thing with the.",
        "I think that we should uh.",
        "Yeah so the the other one.",
        "Wait what was the um.",
        "So so the thing is that the.",
        "And then the uh the other.",
        "Right so um the the.",
    ],
    "implausible_timing": [
        "Yes.",
        "Sure.",
        "Done.",
        "Right.",
    ],
}

# Said, not heard properly, said again seconds later. The generator emits both
# halves into the same sitting; only the second should be flagged.
RETRIES = [
    "Send the renewal quote to ABC by Thursday.",
    "Standup moves to 10 from Monday.",
    "Tell Lumen the rate limits are going up.",
    "Push the design review to next week.",
    "The gateway cutover is Saturday night.",
    "Board deck needs the churn slide updated.",
]

# --- filler: the ordinary day -----------------------------------------------
# Half of what anyone dictates is logistics that stop mattering by tomorrow.
# A corpus without this makes extraction look far better than it is.
#
# These are frames rather than fixed lines. Five hundred records drawn from a
# list of eighty sentences repeats each one six times, which would manufacture
# reinforcement that never happened and make claim identity look far better
# than it is. Slots give combinatorial variety from hand-written structure.

SLOTS = {
    "customer": ["ABC", "ABC Inc", "Lumen", "Lumen Retail", "Trellis",
                 "Trellis Health"],
    "project": ["inbox rules", "the gateway migration", "SOC2", "the pricing work",
                "the nav redesign", "the onboarding flow", "the billing rewrite"],
    "artifact": ["the deck", "the doc", "the spec", "the PR", "the ticket",
                 "the thread", "the draft", "the quote", "the contract",
                 "the changelog", "the runbook", "the dashboard"],
    "day": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "tomorrow",
            "end of week", "next week", "the 14th", "the 21st"],
    "time": ["9:30", "10", "11", "2", "3", "4", "4:30", "5"],
    "metric": ["churn", "signups", "activation", "latency", "error rate",
               "conversion", "the trial-to-paid rate", "NPS"],
    "pct": ["3.1%", "4%", "8%", "12%", "18%", "22%", "31%"],
    "team": ["support", "design", "eng", "sales", "legal", "finance"],
}

# {who} resolves to a name on first mention in a sitting, a pronoun after.
FRAME_LOGISTICS = [
    "Running {n} late.",
    "On the call.",
    "Sent {artifact}.",
    "Looking at {artifact} now.",
    "Will do it after standup.",
    "Can't make {time}, {day} instead?",
    "Moved to the small room.",
    "In a meeting, back in {n}.",
    "Give me an hour on {artifact}.",
    "Bumping this, {who} needs it.",
    "Any update on {project}?",
    "Let's take {project} offline.",
    "Quick sync at {time}?",
    "Reviewing {artifact}, comments coming.",
    "Approved, {who} can ship it.",
    "Can we push {artifact} to {day}?",
    "Not urgent, whenever.",
    "Thanks for turning {artifact} around.",
    "{who} is out until {day}.",
    "Ping {who} about {artifact}.",
    "Missed this, catching up.",
    "Rescheduling to {day}.",
    "Working from home {day}.",
    "Calendar is a mess this week.",
]

# Longer frames, so the top of the length distribution reflects the times
# someone thinks out loud rather than fires off a fragment.
FRAME_LONG = [
    "{who} came back on {project} and the short version is {customer} want it "
    "before {day}, which we cannot do without dropping {artifact}.",
    "Spoke to {customer} about {project}. They are fine on scope, the problem "
    "is timing, and {who} thinks {day} is optimistic.",
    "Two things from the {project} review. {metric} is worse than we thought "
    "at {pct}, and {who} wants another week on {artifact}.",
    "Long call with {customer}. They will sign if we can commit to {day}, "
    "otherwise it slides to next quarter and we lose the {metric} bump.",
    "Went through {artifact} with {who}. Most of it is fine, but the "
    "{project} section needs rewriting before {team} see it.",
    "Thinking about {project} out loud. If {metric} stays at {pct} we are "
    "fine, if it slips we need to tell {customer} before {day}.",
    "{who} raised something worth writing down. {customer} have been asking "
    "about {project} for weeks and nobody owns the answer.",
    "Recap of the {team} sync: {artifact} lands {day}, {project} is blocked "
    "until then, and {who} is off next week so it needs to be earlier.",
]

FRAME_UPDATE = [
    "{metric} is at {pct} this month.",
    "{metric} moved to {pct}, worth a look.",
    "Two tickets on the same import bug, {who} is looking.",
    "{customer} asked about the API rate limits.",
    "{customer} want a trial after the demo.",
    "{customer} are slow on procurement, slower than the sales cycle.",
    "{customer} renewal is up {day}.",
    "{artifact} for {project} is in review.",
    "{project} is blocked on {team}.",
    "{project} slipped a week, nothing dramatic.",
    "Deployed {project} to staging.",
    "Rolled back the release, small regression.",
    "Pipeline failed overnight, rerunning.",
    "Latency spiked at {time}, looking into it.",
    "Docs are stale on the webhook page, {who} said they would fix it.",
    "Backlog is around forty tickets.",
    "{who} pushed the fix, waiting on review.",
    "{who} is picking up the escalation.",
    "{who} wants a call about {project}.",
    "{who} flagged something in {artifact}.",
    "{who} is taking {project} while I'm out.",
    "Need the numbers before the {project} call.",
    "{artifact} is with legal.",
    "Invoice went out, {who} is chasing {customer} on it.",
    "They want a security questionnaire before signing.",
    "Support queue is clear, {who} cleared the backlog overnight.",
    "Error rate back to baseline.",
    "{team} want {artifact} by {day}.",
    "Design review moved to {day}.",
    "Board deck due {day}.",
]

# Deltas and follow-ups: how something already said gets referred to later.
# No subject, no name, only what changed. The hardest material in the corpus.
FRAME_FOLLOWUP = [
    "Still pushing back on it.",
    "Same as before.",
    "That's sorted.",
    "Moved it to {day}.",
    "He's on it.",
    "She's not convinced.",
    "They agreed in the end.",
    "Still waiting on them.",
    "Changed my mind on that.",
    "Went with the second option.",
    "Parked for now.",
    "Picking this back up.",
    "Closed it out.",
    "Reopened, it wasn't fixed.",
    "Same problem as {day}.",
    "Fixed, ignore the earlier one.",
    "Doesn't matter now.",
    "Worth revisiting after {project}.",
]

# Joined onto another clause to produce the longer end of the distribution.
FRAME_TAIL = [
    "worth a look",
    "nothing urgent",
    "flagging it now so it doesn't bite us",
    "will confirm once I hear back",
    "same as last time",
    "let's decide {day}",
    "{who} has the context",
    "I'll pick it up after {project}",
    "not blocking anything yet",
]

# Kept for the ingest-gate fixtures, which reference them by name.
LOGISTICS = FRAME_LOGISTICS
WORK_UPDATES = FRAME_UPDATE
FOLLOW_UPS = FRAME_FOLLOWUP
