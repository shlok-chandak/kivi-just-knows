"""Token prices, for reporting what a run would cost.

Calls are made on the free tier, so the amount actually billed is zero. That
figure says nothing about whether the pipeline is affordable at scale, so
costs are computed at published list price and reported as such.

Rates are USD per million tokens, from ai.google.dev/gemini-api/docs/pricing
as of 2026-09-08. Two things to note when updating:

- Output rates include thinking tokens, so reasoning is billed as output.
- Gemini 3.8 Flash is discounted until 2026-12-31, after which input goes to
  $1.50 and output to $7.50. A run reported now uses the promotional rate.

Only verified rates belong here. An unknown model falls back to the most
expensive known rate, so a missing entry overstates cost rather than hiding
it -- the safer direction to be wrong in.
"""

from dataclasses import dataclass

USD_PER_MILLION = 1_000_000


@dataclass(frozen=True)
class Rate:
    input_usd: float
    output_usd: float


RATES: dict[str, Rate] = {
    # The model this build actually runs. Its absence meant every call fell
    # back to the most expensive known rate, so every cost reported before
    # now is an upper bound roughly double the real one -- and the warning
    # said so on every call, which is the only reason it was noticed.
    "gemini-3.1-flash-lite": Rate(input_usd=0.25, output_usd=1.50),
    "gemini-3.5-flash-lite": Rate(input_usd=0.30, output_usd=2.50),
    "gemini-3.8-flash": Rate(input_usd=0.75, output_usd=3.75),
}

FALLBACK_RATE = Rate(
    input_usd=max(rate.input_usd for rate in RATES.values()),
    output_usd=max(rate.output_usd for rate in RATES.values()),
)


def rate_for(model: str) -> tuple[Rate, bool]:
    """Returns the rate, and whether it was a fallback rather than a real one."""
    rate = RATES.get(model)
    return (rate, False) if rate else (FALLBACK_RATE, True)


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """List-price cost of one call."""
    rate, _ = rate_for(model)
    return (
        input_tokens * rate.input_usd + output_tokens * rate.output_usd
    ) / USD_PER_MILLION
