import os
import time
import base64
import requests
import viktor as vkt
from functools import lru_cache
from dotenv import load_dotenv
from typing import Tuple, Dict, Any

load_dotenv()

# APS API endpoints
APS_BASE_URL = "https://developer.api.autodesk.com"
MD_BASE_URL = f"{APS_BASE_URL}/modelderivative/v2"
AUTH_URL = f"{APS_BASE_URL}/authentication/v2/token"

# Credentials
CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")

# Revit version to activity/engine mapping
# Based on the activities created in create_activities_by_revit_version.ipynb
REVIT_VERSION_CONFIG = {
    "2023": {
        "engine": "Autodesk.Revit+2023",
        "activity_name": "TypeParametersActivity2023",
        "alias": "dev"
    },
    "2024": {
        "engine": "Autodesk.Revit+2024",
        "activity_name": "TypeParametersActivity2024",
        "alias": "dev"
    },
    "2025": {
        "engine": "Autodesk.Revit+2025",
        "activity_name": "TypeParametersActivity2025",
        "alias": "dev"
    },
    "2026": {
        "engine": "Autodesk.Revit+2026",
        "activity_name": "TypeParametersActivity2026",
        "alias": "dev"
    }
}

# Default to 2024 if version cannot be detected
DEFAULT_REVIT_VERSION = "2024"


@lru_cache(maxsize=1)
def get_token(client_id: str, client_secret: str) -> str:
    """Get a 2-legged access token for APS API."""
    print("📡 Requesting APS token for Model Derivative...")
    
    response = requests.post(
        AUTH_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
            "scope": "data:read data:write",
        },
        timeout=15,
    )
    response.raise_for_status()
    token = response.json()["access_token"]
    print("✅ Token obtained successfully.")
    return token


def safe_base64_encode(text: str) -> str:
    """Encode text to URL-safe base64 format (used for URNs)."""
    return base64.urlsafe_b64encode(text.encode()).decode().strip("=")


def to_md_urn(wip_urn: str) -> str:
    """Convert WIP URN to Model Derivative URN."""
    raw = wip_urn.split("?", 1)[0]
    encoded = base64.urlsafe_b64encode(raw.encode("utf8")).decode("utf8")
    return encoded.rstrip("=")


def get_revit_version_from_manifest(manifest: dict) -> str | None:
    """Extract Revit version from manifest."""
    try:
        derivatives = manifest.get("derivatives", [])
        if not derivatives:
            return None
        
        for derivative in derivatives:
            properties = derivative.get("properties", {})
            doc_info = properties.get("Document Information", {})
            rvt_version = doc_info.get("RVTVersion")
            if rvt_version:
                return str(rvt_version)
        
        return None
    except Exception as e:
        print(f"Error extracting Revit version from manifest: {e}")
        return None


def fetch_manifest(token: str, object_urn: str) -> dict:
    """Fetch model derivative manifest."""
    response = requests.get(
        f"{MD_BASE_URL}/designdata/{object_urn}/manifest",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def get_revit_version_from_oss_object(token: str, bucket_key: str, object_key: str) -> str | None:
    """
    Get Revit version from an OSS object by translating it and checking the manifest.
    Revit version string (e.g., "2024") or None if not detected
    """
    print(f"🔍 Detecting Revit version for object: {object_key}")
    
    # Build the OSS object URN
    oss_object_id = f"urn:adsk.objects:os.object:{bucket_key}/{object_key}"
    object_urn = safe_base64_encode(oss_object_id)
    
    # Start a basic translation job to get the manifest
    try:
        start_svf_translation_job(token, object_urn)
        
        # Wait a bit for the manifest to be generated
        import time
        max_wait = 60
        interval = 5
        elapsed = 0
        
        while elapsed < max_wait:
            try:
                manifest = fetch_manifest(token, object_urn)
                version = get_revit_version_from_manifest(manifest)
                
                if version:
                    print(f"✅ Detected Revit version: {version}")
                    return version
                    
                # If manifest exists but no version yet, wait a bit more
                if manifest.get("status") in ["success", "failed"]:
                    break
                    
            except requests.exceptions.RequestException:
                pass
            
            time.sleep(interval)
            elapsed += interval
        
        print("⚠️  Could not detect Revit version from manifest")
        return None
        
    except Exception as e:
        print(f"⚠️  Error detecting Revit version: {e}")
        return None


def start_svf_translation_job(token: str, object_urn: str) -> Dict[str, Any]:
    """
    Start SVF translation job for the Revit file generated by Design Automation.
    """
    print("🔄 Starting Model Derivative translation job...")
    
    job_payload = {
        "input": {"urn": object_urn},
        "output": {
            "formats": [
                {
                    "type": "svf2",
                    "views": ["3d"]
                }
            ]
        }
    }
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "x-ads-force": "true"
    }
    
    response = requests.post(
        f"{MD_BASE_URL}/designdata/job",
        headers=headers,
        json=job_payload,
        timeout=30
    )
    response.raise_for_status()
    
    result = response.json()
    print("✅ Translation job submitted successfully.")
    return result


def get_translation_status(token: str, object_urn: str) -> Tuple[str, str]:
    """
    Check the status of a Model Derivative translation job.
    """
    response = requests.get(
        f"{MD_BASE_URL}/designdata/{object_urn}/manifest",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30
    )
    
    if response.status_code == 202:
        return "inprogress", "Manifest not ready"
    
    response.raise_for_status()
    manifest = response.json()
    
    status = manifest.get("status", "unknown")
    progress = manifest.get("progress", "N/A")
    
    return status, progress


def translate_da_result_for_viewing(bucket_key: str, output_object_key: str) -> str:
    """
    Translate a Revit file generated by Design Automation for viewing in APS Viewer.
    """
    vkt.UserMessage.info("🎯 STARTING MODEL TRANSLATION FOR VIEWING")
    vkt.UserMessage.info(f"Bucket: {bucket_key}")
    vkt.UserMessage.info(f"Object: {output_object_key}")
    
    # Get authentication token
    token = get_token(CLIENT_ID, CLIENT_SECRET)
    
    # Build the OSS object URN for Model Derivative
    oss_object_id = f"urn:adsk.objects:os.object:{bucket_key}/{output_object_key}"
    object_urn = safe_base64_encode(oss_object_id)
    
    vkt.UserMessage.info(f"🔗 Object URN: {object_urn}")
    
    # Start translation job
    translation_job = start_svf_translation_job(token, object_urn)
    vkt.UserMessage.info(f"📋 Job Details: {translation_job.get('urn', 'No URN in response')}")
    
    # Monitor translation progress
    vkt.UserMessage.info("⏳ Monitoring translation progress...")
    vkt.progress_message("🔄 Model translation in progress...")
    max_wait_time = 300
    poll_interval = 15
    elapsed_time = 0
    
    while elapsed_time < max_wait_time:
        try:
            status, progress = get_translation_status(token, object_urn)
            vkt.UserMessage.info(f"  > MD Status: {status} ({progress})")
            
            progress_pct = min((elapsed_time / max_wait_time) * 100, 99)
            vkt.progress_message(
                f"🔄 Model translation in progress [{elapsed_time}s]\n"
                f"Status: {status} | Progress: {progress}",
                percentage=progress_pct
            )
            
            if status == "success":
                vkt.UserMessage.info("✅ Translation completed successfully!")
                vkt.UserMessage.info(f"🎉 Model ready for viewing with URN: {object_urn}")
                vkt.progress_message("✅ Translation completed successfully!", percentage=100)
                return object_urn
                
            elif status == "failed":
                vkt.UserMessage.info("❌ Translation failed!")
                # Try to get more details about the failure
                try:
                    response = requests.get(
                        f"{MD_BASE_URL}/designdata/{object_urn}/manifest",
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=30
                    )
                    if response.status_code == 200:
                        manifest = response.json()
                        messages = manifest.get("derivatives", [{}])[0].get("messages", [])
                        for msg in messages:
                            vkt.UserMessage.info(f"   Error: {msg.get('message', 'Unknown error')}")
                except Exception as e:
                    vkt.UserMessage.info(f"   Could not get error details: {e}")
                
                raise Exception("Model Derivative translation failed!")
                
            elif status in ["inprogress", "pending"]:
                pass
            else:
                vkt.UserMessage.info(f"⚠️  Unknown status: {status}")
            
        except requests.exceptions.RequestException as e:
            vkt.UserMessage.info(f"⚠️  Error checking translation status: {e}. Retrying...")
        
        time.sleep(poll_interval)
        elapsed_time += poll_interval
    
    # Timeout reached
    vkt.UserMessage.info(f"⏰ Translation timed out after {max_wait_time}s")
    raise Exception(f"Translation timeout after {max_wait_time} seconds")


def get_translation_info(object_urn: str) -> Dict[str, Any]:
    """
    Get detailed information about a translated model.
    """
    token = get_token(CLIENT_ID, CLIENT_SECRET)
    
    response = requests.get(
        f"{MD_BASE_URL}/designdata/{object_urn}/manifest",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30
    )
    response.raise_for_status()
    
    manifest = response.json()
    
    # Extract useful information
    info = {
        "status": manifest.get("status"),
        "progress": manifest.get("progress"),
        "type": manifest.get("type"),
        "region": manifest.get("region"),
        "urn": object_urn
    }
    
    # Get derivative information
    derivatives = manifest.get("derivatives", [])
    if derivatives:
        derivative = derivatives[0]
        info.update({
            "output_type": derivative.get("outputType"),
            "has_thumbnail": "thumbnail" in derivative,
            "children_count": len(derivative.get("children", [])),
        })
    
    return info


def get_viewables_from_urn(object_urn: str) -> list[Dict[str, Any]]:
    """
    Get available viewables (views) from a translated model.
    """
    token = get_token(CLIENT_ID, CLIENT_SECRET)
    
    response = requests.get(
        f"{MD_BASE_URL}/designdata/{object_urn}/manifest",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30
    )
    response.raise_for_status()
    
    manifest = response.json()
    viewables = []
    
    def extract_viewables(children: list, parent_name: str = ""):
        """Recursively extract viewables from manifest children."""
        for child in children:
            role = child.get("role", "")
            guid = child.get("guid", "")
            name = child.get("name", "Unnamed View")
            
            # Viewables typically have role '3d' or '2d'
            if role in ["3d", "2d"] and guid:
                viewables.append({
                    "guid": guid,
                    "name": name,
                    "role": role
                })
            
            # Recurse into nested children
            if "children" in child:
                extract_viewables(child["children"], name)
    
    # Process derivatives
    derivatives = manifest.get("derivatives", [])
    for derivative in derivatives:
        children = derivative.get("children", [])
        extract_viewables(children)
    
    print(f"Found {len(viewables)} viewable(s) in manifest")
    return viewables


def translate_and_prepare_for_viewing(da_workflow_result: Dict[str, Any]) -> str:
    """
    Integration function to translate Design Automation results for viewing.
    """
    # Extract information from DA workflow result
    workitem_result = da_workflow_result.get("workitem_result", {})
    
    if workitem_result.get("status") != "success":
        raise Exception(f"Cannot translate: DA workflow status is '{workitem_result.get('status')}'")
    
    bucket_key = workitem_result.get("bucket_key")
    output_object_key = workitem_result.get("output_object_key")
    
    if not bucket_key or not output_object_key:
        raise Exception("Missing bucket_key or output_object_key in DA workflow result")
    
    # Start translation
    viewer_urn = translate_da_result_for_viewing(bucket_key, output_object_key)
    
    return viewer_urn