"""
Model service for 3D model generation coordination.
"""
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime

from app.models.model import GenerationJob, GeneratedModel
from app.repositories.model_repo import ModelRepository
from app.repositories.chat_repo import ChatRepository
from app.services.ml_client import MLClient
from app.core.exceptions import MLServiceUnavailable, MLServiceTimeout, MLGenerationFailed


class ModelService:
    def __init__(
        self,
        model_repo: ModelRepository,
        ml_client: MLClient,
        chat_repo: Optional[ChatRepository] = None
    ):
        self.model_repo = model_repo
        self.ml_client = ml_client
        self.chat_repo = chat_repo

    async def create_generation_job(
        self,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        prompt: Optional[str] = None
    ) -> GenerationJob:
        """Create a new model generation job."""
        job = GenerationJob(
            user_id=user_id,
            session_id=session_id,
            status="queued"
        )
        return await self.model_repo.create_job(job)

    async def trigger_generation(
        self,
        job_id: uuid.UUID,
        prompt: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Trigger model generation via ML service.
        
        Updates job status and handles errors appropriately.
        """
        job = await self.model_repo.get_job(job_id)
        if not job:
            raise ValueError("Job not found")
        
        # Update to processing
        job.status = "processing"
        await self.model_repo.update_job(job)
        
        try:
            # Call ML Service
            result = await self.ml_client.generate_model(
                prompt=prompt,
                session_id=str(job.session_id),
                context=context
            )
            
            # Handle response from ML service
            model_data = None
            if isinstance(result, str):
                try:
                    import json
                    parsed = json.loads(result)
                    if isinstance(parsed, dict):
                        result = parsed
                except:
                    pass

            if isinstance(result, dict):
                # Unwrap 'data' key if present (RunPod handler wrapper)
                if "data" in result and isinstance(result["data"], dict):
                    result = result["data"]

                # CASE 1: Response already has 'model_data' (Old/Mock format)
                if "model_data" in result:
                    model_data = result.get("model_data")
                
                # CASE 2: Flat response from actual ML Service (New format)
                # Expected structure: { "specification": {...}, "url": {...}, "metrics": {...} }
                elif "specification" in result and "url" in result:
                    spec = result.get("specification", {})
                    urls = result.get("url", {})
                    metrics = result.get("metrics", {})
                    
                    # Parse URLs into structured files dict
                    files = {
                        "sdf_url": None,
                        "urdf_url": None,
                        "config_url": None,
                        "meshes": [],
                        "assets": []
                    }
                    
                    # --- DEEP TOKEN DISCOVERY ---
                    token_keys = ["token", "assets_token", "signed_token", "access_token", "signature", "sas_token", "id"]
                    global_token = None
                    for tk in token_keys:
                        if result.get(tk):
                            global_token = result.get(tk)
                            print(f"MODEL_SERVICE: Found token in '{tk}'")
                            break
                    
                    if not global_token:
                        for u in urls.values():
                            if isinstance(u, str) and "token=" in u:
                                try:
                                    global_token = u.split("token=")[1].split("&")[0]
                                    print(f"MODEL_SERVICE: Extracted token from URL")
                                    break
                                except:
                                    pass

                    # URL processing helper
                    def process_url(u: str) -> str:
                        if not u or not isinstance(u, str):
                            return u
                        if "supabase.co" in u:
                            # FORCE /sign/ for Supabase
                            u = u.replace("/object/public/", "/object/sign/")
                            if global_token and "token=" not in u:
                                sep = "&" if "?" in u else "?"
                                u += f"{sep}token={global_token}"
                        return u

                    for filename, url in urls.items():
                        processed_url = process_url(url)
                        lower_name = filename.lower()
                        
                        # Store in assets (standard)
                        files["assets"].append({"filename": filename, "url": processed_url})
                        
                        if lower_name.endswith(".sdf"):
                            files["sdf_url"] = processed_url
                        elif lower_name.endswith(".urdf"):
                            files["urdf_url"] = processed_url
                        elif lower_name.endswith((".yaml", ".config", "specification.json")):
                            # Priority for higher quality config
                            priority = 2 if lower_name.endswith(".yaml") else 1
                            if priority > config_priority:
                                files["config_url"] = processed_url
                                config_priority = priority
                        elif lower_name.endswith((".stl", ".obj")):
                            files["meshes"].append({"filename": filename, "url": processed_url})

                    # Automatic Mesh Discovery from Specification
                    parts = result.get("specification", {}).get("parts", [])
                    if files["sdf_url"] and (parts or not urls):
                        # Determine base path from SDF URL
                        sdf_url = files["sdf_url"]
                        if "/" in sdf_url:
                            base_path = sdf_url.rsplit('/', 1)[0] + "/"
                        else:
                            base_path = "./"
                        
                        for part in parts:
                            mesh_file = part.get("mesh_file")
                            if mesh_file:
                                # Check if already tracked
                                if not any(a["filename"] == mesh_file for a in files["assets"]):
                                    inferred_url = process_url(base_path + mesh_file)
                                    files["assets"].append({
                                        "filename": mesh_file,
                                        "url": inferred_url
                                    })
                                    # Also add to meshes list for compatibility
                                    files["meshes"].append({
                                        "filename": mesh_file,
                                        "url": inferred_url,
                                        "format": "stl"
                                    })

                    model_data = {
                        "name": spec.get("model_name", f"Model-{job.id}"),
                        "specification": spec,
                        "files": files,
                        "generation_time": metrics.get("total_time")
                    }

            # If successful, update job
            job.status = "completed"
            job.completed_at = datetime.utcnow()
            await self.model_repo.update_job(job)
            
            # Create model record if result contains model data
            if model_data:
                await self._create_model_from_result(job, model_data)
            
            return {
                "job_id": str(job.id),
                "status": job.status,
                "result": result
            }
            
        except (MLServiceUnavailable, MLServiceTimeout) as e:
            job.status = "failed"
            job.error_message = e.message
            await self.model_repo.update_job(job)
            raise
            
        except MLGenerationFailed as e:
            job.status = "failed"
            job.error_message = e.message
            await self.model_repo.update_job(job)
            raise
            
        except Exception as e:
            job.status = "failed"
            job.error_message = str(e)
            await self.model_repo.update_job(job)
            raise

    async def _create_model_from_result(
        self,
        job: GenerationJob,
        model_data: Dict[str, Any]
    ) -> GeneratedModel:
        """Create a GeneratedModel record from ML service result."""
        model = GeneratedModel(
            job_id=job.id,
            user_id=job.user_id,
            name=model_data.get("name", f"Model-{job.id}"),
            specification=model_data.get("specification", {}),
            files=model_data.get("files", {}),
            generation_time=model_data.get("generation_time")
        )
        return await self.model_repo.create_model(model)

    async def get_job_status(self, job_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """Get job status with progress info."""
        job = await self.model_repo.get_job(job_id)
        if not job:
            return None
        
        # Calculate progress
        progress = None
        if job.status == "queued":
            progress = 0
        elif job.status == "processing":
            progress = 50  # Could be more granular with ML service polling
        elif job.status == "completed":
            progress = 100
        
        return {
            "job_id": str(job.id),
            "status": job.status,
            "progress": progress,
            "error": job.error_message,
            "created_at": job.created_at.isoformat(),
            "completed_at": job.completed_at.isoformat() if job.completed_at else None
        }

    async def get_model(self, model_id: uuid.UUID) -> Optional[GeneratedModel]:
        """Get a generated model by ID."""
        return await self.model_repo.get_model(model_id)

    async def get_user_models(self, user_id: uuid.UUID) -> List[GeneratedModel]:
        """Get all models for a user."""
        return await self.model_repo.get_user_models(user_id)

    async def get_model_files(self, model_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """Get model files with URLs for viewer."""
        model = await self.model_repo.get_model(model_id)
        if not model:
            return None
        
        files = model.files or {}
        return {
            "sdf_url": files.get("sdf_url"),
            "urdf_url": files.get("urdf_url"),
            "config_url": files.get("config_url"),
            "meshes": files.get("meshes", []),
            "assets": files.get("assets", [])
        }

    async def get_model_metadata(self, model_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """Get model metadata for viewer."""
        model = await self.model_repo.get_model(model_id)
        if not model:
            return None
        
        spec = model.specification or {}
        return {
            "id": str(model.id),
            "name": model.name,
            "assembly_plan": spec.get("assembly_plan", {}),
            "parts_list": spec.get("parts_list", []),
            "joint_configurations": spec.get("joint_configurations", []),
            "generation_time": model.generation_time,
            "created_at": model.created_at.isoformat()
        }
