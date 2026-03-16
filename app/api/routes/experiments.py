import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user_id_async
from app.db.session import get_db
from app.models.message import Message as MessageModel
from app.services.openscad_agent.experiments import EXPERIMENTS

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/results")
async def get_experiment_results(
    experiment_id: str = Query(..., description="Experiment ID to query results for"),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id_async),
):
    """Aggregate quality metrics grouped by variant for an experiment."""
    # Verify experiment exists
    if experiment_id not in EXPERIMENTS:
        # Still query — experiment may have been deactivated but data remains
        pass

    stmt = select(MessageModel).where(
        MessageModel.role == "assistant",
        MessageModel.metadata_json.isnot(None),
    )
    result = await db.execute(stmt)
    messages = result.scalars().all()

    variant_stats: dict[str, dict] = {}
    for msg in messages:
        meta = msg.metadata_json or {}
        if not isinstance(meta, dict):
            continue

        exp = meta.get("experiment")
        if not isinstance(exp, dict) or exp.get("experiment_id") != experiment_id:
            continue

        variant_id = exp.get("variant_id", "unknown")
        quality = meta.get("quality_metrics", {})

        if variant_id not in variant_stats:
            variant_stats[variant_id] = {
                "count": 0,
                "pass_count": 0,
                "warn_count": 0,
                "total_modules": 0,
                "total_uncalled": 0,
                "total_magic_numbers": 0,
                "total_connection_vars": 0,
                "has_eps_count": 0,
                "has_tree_count": 0,
                "issue_counts": {},
            }

        stats = variant_stats[variant_id]
        stats["count"] += 1
        if quality.get("status") == "PASS":
            stats["pass_count"] += 1
        elif quality.get("status") == "WARN":
            stats["warn_count"] += 1
        stats["total_modules"] += quality.get("defined_modules", 0)
        stats["total_uncalled"] += quality.get("uncalled_modules", 0)
        stats["total_magic_numbers"] += quality.get("magic_numbers_in_translate", 0)
        stats["total_connection_vars"] += quality.get("connection_vars", 0)
        if quality.get("uses_eps"):
            stats["has_eps_count"] += 1
        if quality.get("has_tree"):
            stats["has_tree_count"] += 1
        for issue in quality.get("issues", []):
            stats["issue_counts"][issue] = stats["issue_counts"].get(issue, 0) + 1

    # Compute averages
    for stats in variant_stats.values():
        n = stats["count"]
        if n > 0:
            stats["pass_rate"] = round(stats["pass_count"] / n, 3)
            stats["avg_modules"] = round(stats["total_modules"] / n, 2)
            stats["avg_uncalled"] = round(stats["total_uncalled"] / n, 2)
            stats["avg_magic_numbers"] = round(stats["total_magic_numbers"] / n, 2)
            stats["avg_connection_vars"] = round(stats["total_connection_vars"] / n, 2)
            stats["eps_rate"] = round(stats["has_eps_count"] / n, 3)
            stats["tree_rate"] = round(stats["has_tree_count"] / n, 3)

    return {
        "experiment_id": experiment_id,
        "variants": variant_stats,
    }


@router.get("")
async def list_experiments(
    user_id: str = Depends(get_current_user_id_async),
):
    """List all configured experiments and their status."""
    return {
        "experiments": [
            {"id": exp.id, "active": exp.active, "variant_count": len(exp.variants)}
            for exp in EXPERIMENTS.values()
        ]
    }
