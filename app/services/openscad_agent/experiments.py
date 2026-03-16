import logging
import random
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class PromptVariant:
    """A single prompt variant within an experiment."""

    id: str
    code_prompt: str | None = None  # Override CODE_PROMPT (None = use default)
    jewelry_context: str | None = None  # Override JEWELRY_CONTEXT (None = use default)
    weight: float = 1.0  # Relative weight for random assignment


@dataclass
class Experiment:
    """An A/B test comparing prompt variants."""

    id: str
    variants: list[PromptVariant] = field(default_factory=list)
    active: bool = True

    def pick_variant(self) -> PromptVariant:
        """Weighted random selection."""
        weights = [v.weight for v in self.variants]
        return random.choices(self.variants, weights=weights, k=1)[0]


# ── Active experiments registry ──────────────────────────────────────────
# Edit this dict to define/activate experiments. Only ONE experiment
# should be active at a time for simplicity.
#
# Example:
#
# EXPERIMENTS: dict[str, Experiment] = {
#     "code_prompt_v2": Experiment(
#         id="code_prompt_v2",
#         active=True,
#         variants=[
#             PromptVariant(id="control"),          # uses default CODE_PROMPT
#             PromptVariant(
#                 id="concise",
#                 code_prompt="You are an expert OpenSCAD programmer. ...",
#             ),
#         ],
#     ),
# }

EXPERIMENTS: dict[str, Experiment] = {}


def get_active_experiment() -> Experiment | None:
    """Return the first active experiment, or None."""
    for exp in EXPERIMENTS.values():
        if exp.active:
            return exp
    return None


def resolve_prompts(
    user_input: str,
) -> tuple[str, str, dict | None]:
    """Resolve CODE_PROMPT and JEWELRY_CONTEXT, applying A/B variant if active.

    Returns:
        (code_prompt, jewelry_context, experiment_metadata_or_none)

    experiment_metadata is None when no experiment is active (opt-in behavior).
    """
    from app.services.openscad_agent.prompts import CODE_PROMPT, JEWELRY_CONTEXT

    experiment = get_active_experiment()
    if experiment is None:
        return CODE_PROMPT, JEWELRY_CONTEXT, None

    variant = experiment.pick_variant()
    logger.info("[AB] experiment=%s variant=%s", experiment.id, variant.id)

    resolved_code = variant.code_prompt if variant.code_prompt is not None else CODE_PROMPT
    resolved_jewelry = variant.jewelry_context if variant.jewelry_context is not None else JEWELRY_CONTEXT

    metadata = {
        "experiment_id": experiment.id,
        "variant_id": variant.id,
    }
    return resolved_code, resolved_jewelry, metadata
