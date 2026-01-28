from fastapi import APIRouter, HTTPException, Response, Request
import httpx

router = APIRouter()


@router.get("/{protocol}/{domain}/{path:path}")
async def proxy_asset(protocol: str, domain: str, path: str, request: Request):
    """
    Proxy external assets with a cleaner path-based URL structure.
    Correctly handles spaces and special characters by re-encoding.
    """
    from urllib.parse import quote, urlencode, unquote

    # First decode to handle already-encoded parts (like %20 from browser)
    decoded_path = unquote(path)
    
    # Then re-encode properly, keeping slashes safe
    encoded_path = quote(decoded_path, safe="/")
    
    # Reconstruct query string correctly
    query_params = dict(request.query_params)
    target_url = f"{protocol}://{domain}/{encoded_path}"
    
    if query_params:
        target_url += f"?{urlencode(query_params)}"

    print(f"PROXY: Reconstructing {protocol}://{domain}/{path} -> {target_url}")

    async with httpx.AsyncClient() as client:
        try:
            # Follow redirects and handle timeouts
            resp = await client.get(target_url, follow_redirects=True, timeout=30.0)

            if resp.status_code >= 400:
                print(f"Proxy upstream error {resp.status_code} for {target_url}")
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Upstream error: {resp.status_code}",
                )

            return Response(
                content=resp.content,
                media_type=resp.headers.get("content-type"),
                status_code=resp.status_code,
            )
        except HTTPException:
            raise
        except Exception as e:
            print(f"Proxy exception for {target_url}: {e}")
            raise HTTPException(status_code=400, detail=str(e))


@router.get("/")
async def proxy_asset_legacy(url: str):
    """Fallback for legacy query-param based proxying."""
    if not url:
        raise HTTPException(status_code=400, detail="Missing URL")

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, follow_redirects=True, timeout=30.0)
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail="Upstream error")
            
            return Response(
                content=resp.content,
                media_type=resp.headers.get("content-type"),
                status_code=resp.status_code,
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
