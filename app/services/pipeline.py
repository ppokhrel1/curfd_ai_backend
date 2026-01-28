from datetime import datetime, timezone


def build_default_spec(prompt: str | None) -> dict:
    """Build a minimal normalized spec placeholder aligned with app_diagram.xml."""
    return {
        "object_spec": {
            "prompt": prompt,
            "reference_images": [],
            "components": [],
        },
        "export_spec": {
            "formats": ["stl", "glb"],
            "quality": "standard",
        },
        "pipeline": {
            "llm": {"status": "pending"},
            "rag": {"status": "pending"},
            "generator": {"status": "pending"},
            "mesh_qa": {"status": "pending"},
            "exporter": {"status": "pending"},
            "storage": {"status": "pending"},
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
