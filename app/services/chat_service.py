"""
Chat service with ML integration for AI responses.
"""
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime

from app.models.chat import ChatSession, Message
from app.repositories.chat_repo import ChatRepository
from app.services.ml_client import MLClient
from app.core.exceptions import MLServiceUnavailable


class ChatService:
    def __init__(
        self, 
        chat_repo: ChatRepository, 
        ml_client: Optional[MLClient] = None,
        model_service: Optional["ModelService"] = None,
        model_repo: Optional["ModelRepository"] = None
    ):
        self.chat_repo = chat_repo
        self.ml_client = ml_client
        self.model_service = model_service
        self.model_repo = model_repo

    async def create_session(
        self,
        user_id: uuid.UUID,
        title: Optional[str] = None
    ) -> ChatSession:
        """Create a new chat session."""
        if not title:
            title = f"New Chat - {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}"
        session = ChatSession(user_id=user_id, title=title)
        return await self.chat_repo.create_session(session)

    async def get_user_sessions(self, user_id: uuid.UUID) -> List[Dict[str, Any]]:
        """Get all sessions for a user with metadata."""
        return await self.chat_repo.get_user_sessions_with_metadata(user_id)

    async def get_session(self, session_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """Get a session with metadata."""
        return await self.chat_repo.get_session_with_metadata(session_id)

    async def delete_session(self, session_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Delete a session if owned by user."""
        session = await self.chat_repo.get_session(session_id)
        if not session or session.user_id != user_id:
            return False
        return await self.chat_repo.delete_session(session_id)

    async def send_message(
        self,
        session_id: uuid.UUID,
        content: str,
        user_id: uuid.UUID
    ) -> Dict[str, Any]:
        """
        Send a user message and get AI response.
        
        Returns both user message, AI response, and any generated model data.
        """
        # Verify session ownership
        db_session_obj = await self.chat_repo.get_session(session_id)
        if not db_session_obj or db_session_obj.user_id != user_id:
            raise ValueError("Session not found or access denied")
        
        # Store user message
        user_message = Message(
            session_id=session_id,
            role="user",
            content=content
        )
        user_message = await self.chat_repo.add_message(user_message)
        
        # Get AI response - now returns tuple of (content, model_data)
        ai_response_content, generated_model = await self._get_ai_response(session_id, content)
        
        # Store AI response
        final_content = ai_response_content
        if generated_model:
            import json
            # Append hidden JSON block that frontend can parse from history to restore model state
            # This avoids DB migration while ensuring persistence
            final_content += f"\n\n|||JSON_DATA|||{json.dumps(generated_model)}"

        ai_message = Message(
            session_id=session_id,
            role="assistant",
            content=final_content
        )
        ai_message = await self.chat_repo.add_message(ai_message)
        
        result = {
            "user_message": user_message,
            "assistant_message": ai_message
        }
        
        # Include generated model data if available
        if generated_model:
            result["generated_model"] = generated_model
        
        return result

    async def _get_ai_response(self, session_id: uuid.UUID, content: str) -> tuple:
        """
        Get AI response from ML service or fallback.
        
        Returns:
            tuple: (text_content: str, model_data: Optional[Dict])
                   model_data contains SDF URLs, specification, etc. when a model is generated
        """
        import json
        
        if self.ml_client:
            try:
                # Get conversation history for context
                messages = await self.chat_repo.get_messages(session_id)
                message_history = [
                    {"role": msg.role, "content": msg.content}
                    for msg in messages[-10:]  # Last 10 messages for context
                ]
                message_history.append({"role": "user", "content": content})
                
                response = await self.ml_client.chat_completion(message_history, session_id=str(session_id))
                
                # Debug: Log raw response
                print(f"CHAT_SERVICE: Raw ML response -> {response}")
                print(f"CHAT_SERVICE: Response type -> {type(response)}")
                
                # Handle string response (direct text from RunPod)
                if isinstance(response, str):
                    stripped = response.strip()
                    if stripped:
                        return (stripped, None)
                    return ("I apologize, I couldn't generate a response.", None)
                
                # Handle list response
                if isinstance(response, list) and len(response) > 0:
                    first_item = response[0]
                    if isinstance(first_item, str):
                        return (first_item.strip(), None)
                    elif isinstance(first_item, dict):
                        for key in ["content", "text", "response", "message"]:
                            if first_item.get(key):
                                return (str(first_item[key]).strip(), None)

                # Handle dict response
                if isinstance(response, dict):
                    # 1. Check for ML handler format: {"status": "success", "data": result}
                    # Or a flat success response with model data
                    is_success = response.get("status") == "success"
                    data = response.get("data")
                    
                    # If flat success, treat the whole response as data (minus status keys)
                    if is_success and data is None:
                        data = response
                        print("CHAT_SERVICE: Detected flat success response")

                    if is_success and data:
                        print(f"CHAT_SERVICE: Processing success data...")
                        if isinstance(data, dict):
                            # Extract model data for frontend
                            # Use ModelService's superior parsing if available
                            if self.model_service:
                                # We need to simulate a job for trigger_generation context if we want to use it directly,
                                # or we can just use the parsing logic. 
                                # Better: Let's use the parsing logic directly from data
                                model_data = await self._extract_model_data_v2(data)
                            else:
                                model_data = await self._extract_model_data(data)
                            
                            # PERSISTENCE: Create GeneratedModel record if we have data
                            if model_data and self.model_repo:
                                try:
                                    from app.models.model import GeneratedModel, GenerationJob
                                    import uuid
                                    
                                    # Create a dummy job record for the chat-generated model to maintain schema integrity
                                    job = GenerationJob(
                                        user_id=db_session_obj.user_id,
                                        session_id=session_id,
                                        status="completed",
                                        completed_at=datetime.utcnow()
                                    )
                                    # We need to save the job first
                                    if hasattr(self.model_repo, 'create_job'):
                                        job = await self.model_repo.create_job(job)
                                    
                                    model_record = GeneratedModel(
                                        job_id=job.id,
                                        user_id=db_session_obj.user_id,
                                        name=model_data.get("model_name", "Chat Generated Model"),
                                        specification=model_data.get("specification") or model_data,
                                        files={
                                            "sdf_url": model_data.get("sdf_url"),
                                            "yaml_url": model_data.get("yaml_url"),
                                            "assets": model_data.get("assets", [])
                                        },
                                        generation_time=model_data.get("metrics", {}).get("total_time")
                                    )
                                    await self.model_repo.create_model(model_record)
                                    print(f"CHAT_SERVICE: Persisted model {model_record.id} to DB")
                                    
                                    # Attach the real model ID to model_data for frontend
                                    model_data["model_id"] = str(model_record.id)
                                except Exception as db_err:
                                    print(f"CHAT_SERVICE: Failed to persist model: {db_err}")

                            # Generate user-friendly message
                            if model_data:
                                text_content = self._format_generation_message(data)
                                return (text_content, model_data)
                            
                        if isinstance(data, str):
                            return (data.strip() if data.strip() else "Model generated successfully.", None)
                    
                    # Check for error from ML handler
                    if response.get("error"):
                        error_msg = response["error"]
                        print(f"CHAT_SERVICE: ML handler error -> {error_msg}")
                        # If it's a known error like 'Unknown action', maybe it's still a success for the main goal
                        if "Unknown action" not in str(error_msg):
                            return (f"I apologize, there was an error: {error_msg}", None)
                    
                    # Try common LLM response keys
                    for key in ["response", "output", "content", "text", "message", "result"]:
                        val = response.get(key)
                        if val and isinstance(val, str) and val.strip():
                            return (val.strip(), None)
                    
                    # Check if it looks like a model response anyway (has URLs)
                    if any(k.endswith(".sdf") for k in response.get("url", {}).keys()) or "url" in response:
                        model_data = await self._extract_model_data(response)
                        if model_data:
                            return (self._format_generation_message(response), model_data)

                    # Fallback: return formatted JSON if it's small, otherwise a generic msg
                    try:
                        if len(response) < 10: # Only if it's a small control dict
                             formatted = json.dumps(response, indent=2)
                             return (f"Response received:\n```json\n{formatted}\n```", None)
                    except:
                        pass
                
                return ("Model generation complete. You can now view and download the assets.", None)
                
            except MLServiceUnavailable:
                return (self._generate_fallback_response(content), None)
            except Exception as e:
                print(f"CHAT_SERVICE: Exception -> {e}")
                return (f"I apologize, there was an error: {str(e)}", None)
        
        return (self._generate_fallback_response(content), None)
    
    async def _extract_model_data(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Extract structured model data from ML response for frontend.
        """
        if not data:
            return None
        
        # Debug logging for response structure
        print(f"CHAT_SERVICE: Processing data keys -> {list(data.keys())}")
        
        # Extract URLs dynamically
        urls = data.get("url", {})
        if not urls:
            # Fallback: Look for URLs at the top level (flat response)
            urls = {k: v for k, v in data.items() if isinstance(v, str) and (
                v.lower().endswith((".sdf", ".yaml", ".config", ".stl", ".obj", ".gltf", ".glb", ".json")) or 
                "supabase.co" in v
            )}
            print(f"CHAT_SERVICE: Discovered {len(urls)} flat URLs")

        sdf_url = None
        yaml_url = None
        assets = []
        
        # --- DEEP TOKEN DISCOVERY ---
        # 1. Search common keys
        token_keys = ["token", "assets_token", "signed_token", "access_token", "signature", "sas_token", "id"]
        global_token = None
        for tk in token_keys:
            if data.get(tk):
                global_token = data.get(tk)
                print(f"CHAT_SERVICE: Found token in '{tk}'")
                break
        
        # 2. Extract from existing URLs if present
        if not global_token:
            for u in urls.values():
                if isinstance(u, str) and "token=" in u:
                    try:
                        global_token = u.split("token=")[1].split("&")[0]
                        print(f"CHAT_SERVICE: Extracted token from URL")
                        break
                    except:
                        pass

        # 3. Last resort: If still no token and we have asset_id, try fetch
        asset_id = data.get("asset_id")
        if not global_token and asset_id and self.ml_client:
            print(f"CHAT_SERVICE: Token missing, attempting to fetch for asset_id: {asset_id}")
            try:
                token_resp = await self.ml_client.get_tokens(asset_id)
                if isinstance(token_resp, dict):
                    for tk in token_keys:
                        if token_resp.get(tk):
                            global_token = token_resp.get(tk)
                            break
            except:
                pass

        # URL processing helper
        def process_url(u: str) -> str:
            if not u or not isinstance(u, str):
                return u
            
            if "supabase.co" in u:
                # FORCE /sign/ for Supabase as requested by user
                u = u.replace("/object/public/", "/object/sign/")
                
                # Append token if found
                if global_token and "token=" not in u:
                    sep = "&" if "?" in u else "?"
                    u += f"{sep}token={global_token}"
                elif not global_token:
                    print(f"CHAT_SERVICE: [!] WARNING: No token found for Supabase URL: {u}")
            return u

        # Initial extraction from 'url' map
        print(f"CHAT_SERVICE: Processing URL map...")
        processed_urls = {}
        for key, url in urls.items():
            processed_url = process_url(url)
            processed_urls[key] = processed_url
            
            # Asset tracking
            if not any(a["filename"] == key for a in assets):
                assets.append({"filename": key, "url": processed_url})

            lower_key = key.lower()
            if lower_key.endswith(".sdf"):
                sdf_url = processed_url
            elif lower_key.endswith(".yaml") or lower_key.endswith(".config") or lower_key.endswith("specification.json"):
                if not yaml_url: # Take the first one found
                    yaml_url = processed_url

        # Automatic Mesh Discovery from Specification
        specification = data.get("specification", {})
        parts = specification.get("parts", [])
        
        if sdf_url and (parts or not processed_urls):
            print(f"CHAT_SERVICE: Checking for missing meshes in {len(parts)} parts...")
            # Determine base path from SDF URL
            # Simple string split is safer than adding a dependency like yarl
            if "/" in sdf_url:
                base_path = sdf_url.rsplit('/', 1)[0] + "/"
            else:
                base_path = "./"
            
            for part in parts:
                mesh_file = part.get("mesh_file")
                if mesh_file:
                    # Check if this mesh is already in our assets
                    if not any(a["filename"] == mesh_file for a in assets):
                        # Not in URL map, but in spec. Infer URL!
                        inferred_url = process_url(base_path + mesh_file)
                        assets.append({
                            "filename": mesh_file,
                            "url": inferred_url
                        })
                        print(f"  - Discovered missing mesh: {mesh_file}")

        # Final check for SDF URL
        if not sdf_url:
            print("CHAT_SERVICE: [!] Error: No SDF URL found in response!")
            return None
        
        return {
            "asset_id": asset_id,
            "sdf_url": sdf_url,
            "yaml_url": yaml_url,
            "assets": assets,
            "model_name": specification.get("model_name", "Generated Model"),
            "model_type": specification.get("model_type", "custom"),
            "description": specification.get("description", ""),
            "parts": parts,
            "joints": specification.get("joints", []),
            "parameters": specification.get("parameters", {}),
            "requirements": data.get("requirements", {}),
            "metrics": data.get("metrics", {})
        }
    
    def _format_generation_message(self, data: Dict[str, Any]) -> str:
        """Format a user-friendly message for successful model generation."""
        spec = data.get("specification", {})
        metrics = data.get("metrics", {})
        urls = data.get("url", {}) or data.get("urls", {})
        if not urls:
             urls = {k: v for k, v in data.items() if isinstance(v, str) and (
                v.lower().endswith((".sdf", ".yaml", ".config", ".stl", ".obj", ".gltf", ".glb", ".json")) or 
                "supabase.co" in v
            )}
        
        model_name = spec.get("model_name", "Model")
        description = spec.get("description", "")
        parts = spec.get("parts", [])
        
        # Build message
        lines = [f"✅ **{model_name}** generated successfully!"]
        
        if description:
            lines.append(f"\n{description}")
        
        if parts:
            lines.append(f"\n**Components:** {len(parts)} parts")
            part_names = [p.get("name", "Part") for p in parts[:5]]
            lines.append(f"• {', '.join(part_names)}")
            if len(parts) > 5:
                lines.append(f"• ...and {len(parts) - 5} more")
        
        if metrics.get("total_time"):
            lines.append(f"\n⏱️ Generated in {metrics['total_time']:.1f}s")
        
        if urls.get("model.sdf"):
            lines.append("\n🎨 *Model ready for viewing!*")
        
        return "\n".join(lines)
    
    def _generate_fallback_response(self, content: str) -> str:
        """Generate a fallback response when ML service is unavailable."""
        content_lower = content.lower()
        
        if any(word in content_lower for word in ["robot", "arm", "manipulator"]):
            return """I understand you're interested in robotic systems. I can help you design:

• **Robotic Arms**: 6-DOF manipulators with gripper mechanisms
• **Mobile Robots**: Wheeled or legged platforms
• **Industrial Systems**: Conveyor systems, pick-and-place units

What specific robot would you like to create?"""
        
        if any(word in content_lower for word in ["car", "vehicle", "wheel"]):
            return """I can help you design vehicle models! Options include:

• **Sports Cars**: Sleek designs with realistic suspension
• **Trucks**: Heavy-duty vehicles with cargo systems
• **Custom Vehicles**: Specify your requirements

What type of vehicle interests you?"""
        
        return """I'm here to help you design 3D models for simulation. I can assist with:

• **Robots**: Arms, mobile platforms, manipulators
• **Vehicles**: Cars, trucks, drones
• **Industrial Equipment**: Conveyors, machinery
• **Custom Designs**: Describe your vision

What would you like to create today?"""

    async def get_session_messages(self, session_id: uuid.UUID) -> List[Message]:
        """Get all messages for a session."""
        return await self.chat_repo.get_messages(session_id)

    async def _extract_model_data_v2(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Improved extraction using ModelService logic style but for ChatService context.
        """
        if not data:
            return None
            
        spec = data.get("specification", {})
        urls = data.get("url", {}) or data.get("urls", {})
        
        # If flat response, try to discover URLs
        if not urls:
             urls = {k: v for k, v in data.items() if isinstance(v, str) and (
                v.lower().endswith((".sdf", ".yaml", ".config", ".stl", ".obj", ".gltf", ".glb", ".json")) or 
                "supabase.co" in v
            )}

        # Use existing extraction logic 
        model_data = await self._extract_model_data(data)
        return model_data
