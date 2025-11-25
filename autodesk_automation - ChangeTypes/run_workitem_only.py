import os
import json
import logging
import uuid
from pathlib import Path
from dotenv import load_dotenv

from aps_automation_sdk.classes import (
    ActivityInputParameter,
    ActivityOutputParameter,
    ActivityJsonParameter,
    WorkItem
)

from aps_automation_sdk.utils import get_token, set_nickname

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def main():
    # Load environment variables
    load_dotenv()
    
    CLIENT_ID = os.getenv("CLIENT_ID", "")
    CLIENT_SECRET = os.getenv("CLIENT_SECRET", "")
    
    if not CLIENT_ID or not CLIENT_SECRET:
        raise ValueError("CLIENT_ID and CLIENT_SECRET must be set in .env file")
    
    # Get authentication token
    print("Authenticating...")
    token = get_token(client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
    
    # Get nickname (must match the one used when creating the activity)
    nickname = set_nickname(token, "myUniqueNickNameHere")
    print(f"✅ Authentication successful. Nickname: {nickname}")
    
    # Define constants - these should match your existing activity
    # For other Revit versions, use:
    # - TypeParametersActivity2023 for Revit 2023
    # - TypeParametersActivity2024 for Revit 2024
    # - TypeParametersActivity2025 for Revit 2025
    # - TypeParametersActivity2026 for Revit 2026
    activity_name = "TypeParametersActivity2024"
    alias = "dev"
    bucket_key = uuid.uuid4().hex
    
    # Create activity full alias (must match existing activity)
    activity_full_alias = f"{nickname}.{activity_name}+{alias}"
    print(f"Using activity: {activity_full_alias}")
    
    # Define input/output parameters
    print("\nSetting up parameters...")
    
    # Input: Revit file
    input_revit = ActivityInputParameter(
        name="rvtFile",
        localName="input.rvt",
        verb="get",
        description="Input Revit model",
        required=True,
        is_engine_input=True,
        bucketKey=bucket_key,
        objectKey="input.rvt",
    )
    
    # Output: Result Revit file
    output_file = ActivityOutputParameter(
        name="result",
        localName="result.rvt",
        verb="put",
        description="Result Revit model",
        bucketKey=bucket_key,
        objectKey="result.rvt",
    )
    
    # Input: JSON configuration for type parameters
    input_json = ActivityJsonParameter(
        name="configJson",
        localName="revit_type_params.json",
        verb="get",
        description="Type parameter JSON configuration",
    )
    
    # Upload input Revit file
    print("\nUploading input Revit file...")
    input_rvt_path = Path.cwd() / "KNA_KNA09C-Bestaand.rvt"
    
    if not input_rvt_path.exists():
        raise FileNotFoundError(f"Input Revit file not found: {input_rvt_path}")
    
    input_revit.upload_file_to_oss(file_path=str(input_rvt_path), token=token)
    print(f"✅ Input Revit file uploaded: {input_rvt_path.name}")
    
    # Load and set JSON configuration
    print("\nLoading JSON configuration...")
    json_config_path = Path.cwd() / "revit_type_params.json"
    
    if not json_config_path.exists():
        raise FileNotFoundError(f"JSON configuration file not found: {json_config_path}")
    
    with open(json_config_path, 'r') as f:
        type_params_config = json.load(f)
    
    input_json.set_content(type_params_config)
    print("✅ Type parameters configuration loaded:")
    print(json.dumps(type_params_config, indent=2))
    
    # Create and execute work item
    print("\n" + "="*60)
    print("Creating work item...")
    work_item = WorkItem(
        parameters=[input_revit, output_file, input_json],
        activity_full_alias=activity_full_alias
    )
    
    print("Executing work item (this may take several minutes)...")
    status_resp = work_item.execute(token=token, max_wait=600, interval=10)
    last_status = status_resp.get("status", "")
    
    print("\n" + "="*60)
    print(f"Work item completed with status: {last_status}")
    print("Full response:")
    print(json.dumps(status_resp, indent=2))
    
    # Download results if successful
    if last_status == "success":
        print("\n" + "="*60)
        print("Downloading results...")
        out_dir = Path.cwd() / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "result.rvt"
        
        output_file.download_to(output_path=str(out_file), token=token)
        print(f"✅ Download successful: {out_file}")
        print("\n🎉 Type parameters workflow completed successfully!")
    else:
        print(f"\n❌ Work item failed with status: {last_status}")
        print("Check the response above for error details.")
        return 1
    
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
        exit(exit_code)
    except Exception as e:
        logging.error(f"Error: {e}", exc_info=True)
        exit(1)
