import os
import uuid
import tempfile
import viktor as vkt
from pathlib import Path
from typing import Annotated, Any
from aps_automation_sdk import get_token
from aps_automation_sdk.classes import (
    ActivityInputParameter,
    ActivityOutputParameter,
    ActivityJsonParameter,
    WorkItem
)
from aps_automation_sdk.utils import set_nickname
from dotenv import load_dotenv
from app.model_translation import translate_da_result_for_viewing

load_dotenv()

CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")

class APSresult(vkt.WebResult):
    def __init__(self, urn: Annotated[str, "bs64 URN from model derivative"] | None = None):
        token = get_token(CLIENT_ID, CLIENT_SECRET)
        html = (Path(__file__).parent / 'ApsViewer.html').read_text()
        html = html.replace('APS_TOKEN_PLACEHOLDER', token)
        html = html.replace('URN_PLACEHOLDER', urn)
        super().__init__(html=html)
        

class APSView(vkt.WebView):
    pass


class Parametrization(vkt.Parametrization):
    title = vkt.Text("""# Add Parameters to Revit Types
This app helps you add custom parameters to your Revit model elements automatically. 
Upload your Revit file, define which parameters you want to add and which elements should get them, then view the results in 3D.""")
    cad_file = vkt.FileField("Upload Your CAD File!")
    suptite1 = vkt.Text("""## Parameter Table
In this table, you define what parameters to add to which elements in your Revit model. 
Each row specifies a parameter name (like "Carbon_Rating"), the element type and family it should be added to, 
and the value to set. You can add multiple rows with the same parameter name to apply it to different elements.""")
    targets = vkt.Table("Targets", default=[
        {
            "parameter_name": "Carbon_Dataset_Code",
            "parameter_group": "PG_DATA",
            "type_name": "400x400mm",
            "family_name": "CO_01_001_Geheide_prefab_betonpaal",
            "value": "95"
        }
    ])
    targets.parameter_name = vkt.TextField("Parameter Name")
    targets.parameter_group = vkt.OptionField(
        "Parameter Group",
        options=["PG_TEXT", "PG_DATA", "PG_IDENTITY_DATA", "PG_GEOMETRY"]
    )
    targets.type_name = vkt.TextField("Type Name")
    targets.family_name = vkt.TextField("Family Name")
    targets.value = vkt.TextField("Value")
    
class Controller(vkt.Controller):
    parametrization = Parametrization

    @APSView("Model Viewer", duration_guess=40)
    def process_cadd_file(self, params, **kwargs) -> APSresult:
        
        file: vkt.File = params.cad_file.file
        file_bytes: bytes = file.getvalue_binary()
        name = params.cad_file.filename
        
        client_id = os.environ.get("CLIENT_ID")
        client_secret = os.environ.get("CLIENT_SECRET")

        if not client_id or not client_secret:
            raise vkt.UserError("CLIENT_ID and CLIENT_SECRET must be set in the environment variables.")
        
        vkt.UserMessage.info("🚀 Starting model processing workflow...")
        vkt.progress_message("⏳ Uploading model to APS...", percentage=10)
        
        # Step 1: Create a temporary file from the uploaded bytes
        with tempfile.NamedTemporaryFile(mode='wb', suffix=Path(name).suffix, delete=False) as temp_file:
            temp_file.write(file_bytes)
            temp_file_path = temp_file.name
        
        try:
            # Step 2: Upload the file to APS bucket and get bucket_key and object_key
            vkt.UserMessage.info(f"📤 Uploading file: {name}")
            token = get_token(client_id, client_secret)
            
            # Generate unique bucket key for this upload
            bucket_key = uuid.uuid4().hex
            object_key = f"input_{uuid.uuid4()}{Path(name).suffix}"
            
            # Create input parameter to handle the upload
            input_param = ActivityInputParameter(
                name="inputFile",
                localName=name,
                verb="get",
                description="Input CAD model",
                required=True,
                is_engine_input=False,
                bucketKey=bucket_key,
                objectKey=object_key,
            )
            
            # Upload the file to OSS
            input_param.upload_file_to_oss(file_path=temp_file_path, token=token)
            vkt.UserMessage.info(f"✅ File uploaded to bucket: {bucket_key}")
            vkt.UserMessage.info(f"   Object key: {object_key}")
            vkt.progress_message("🔄 Translating model for viewing...", percentage=40)
            
            # Step 3: Translate the uploaded model for viewing
            vkt.UserMessage.info("🔄 Starting model translation...")
            viewer_urn = translate_da_result_for_viewing(bucket_key, object_key)
            vkt.UserMessage.info(f"✅ Translation complete! URN: {viewer_urn}")
            vkt.progress_message("✅ Model ready for viewing!", percentage=100)
            
            # Step 4: Return the viewer URN to display in APS viewer
            return APSresult(urn=viewer_urn)
            
        finally:
            # Clean up temporary file
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    @APSView("Updated Model with Parameters", duration_guess=120)
    def process_with_workitem(self, params, **kwargs) -> APSresult:
        """
        Process the CAD file with Design Automation to add type parameters,
        then display the updated model in APS Viewer.
        """
        file: vkt.File = params.cad_file.file
        file_bytes: bytes = file.getvalue_binary()
        name = params.cad_file.filename
        
        client_id = os.environ.get("CLIENT_ID")
        client_secret = os.environ.get("CLIENT_SECRET")

        if not client_id or not client_secret:
            raise vkt.UserError("CLIENT_ID and CLIENT_SECRET must be set in the environment variables.")
        
        vkt.UserMessage.info("🚀 Starting Design Automation workflow...")
        vkt.progress_message("⏳ Preparing files...", percentage=5)
        
        # Step 1: Create a temporary file from the uploaded bytes
        with tempfile.NamedTemporaryFile(mode='wb', suffix=Path(name).suffix, delete=False) as temp_file:
            temp_file.write(file_bytes)
            temp_file_path = temp_file.name
        
        try:
            # Step 2: Get authentication and set up activity
            vkt.UserMessage.info("🔐 Authenticating with APS...")
            token = get_token(client_id, client_secret)
            nickname = set_nickname(token, "myUniqueNickNameHere")
            
            # Define activity details (must match existing activity)
            activity_name = "TypeParametersActivity"
            alias = "dev"
            activity_full_alias = f"{nickname}.{activity_name}+{alias}"
            
            vkt.UserMessage.info(f"📋 Using activity: {activity_full_alias}")
            vkt.progress_message("📤 Uploading input file...", percentage=15)
            
            # Step 3: Generate unique bucket key and use simple object keys
            bucket_key = uuid.uuid4().hex
            input_object_key = "input.rvt"
            output_object_key = "output.rvt"
            
            # Step 4: Create input parameter for Revit file
            vkt.UserMessage.info(f"📤 Uploading file: {name}")
            input_revit = ActivityInputParameter(
                name="rvtFile",
                localName="input.rvt",
                verb="get",
                description="Input Revit model",
                required=True,
                is_engine_input=True,
                bucketKey=bucket_key,
                objectKey=input_object_key,
            )
            
            # Upload the input file to OSS
            input_revit.upload_file_to_oss(file_path=temp_file_path, token=token)
            vkt.UserMessage.info(f"✅ File uploaded to bucket: {bucket_key}")
            vkt.progress_message("⚙️ Setting up parameters...", percentage=25)
            
            # Step 5: Create output parameter
            output_file = ActivityOutputParameter(
                name="result",
                localName="output.rvt",
                verb="put",
                description="Result Revit model",
                bucketKey=bucket_key,
                objectKey=output_object_key,
            )
            
            # Step 6: Create JSON configuration from params
            vkt.UserMessage.info("📝 Generating parameter configuration...")
            type_params_config = self.create_json_from_params(params)
            vkt.UserMessage.info(f"   Adding {len(type_params_config)} parameter(s)")
            
            input_json = ActivityJsonParameter(
                name="configJson",
                localName="revit_type_params.json",
                verb="get",
                description="Type parameter JSON configuration",
            )
            input_json.set_content(type_params_config)
            
            # Step 7: Create and execute work item
            vkt.UserMessage.info("🔧 Creating work item...")
            vkt.progress_message("🔧 Running Design Automation (this may take a few minutes)...", percentage=35)
            
            work_item = WorkItem(
                parameters=[input_revit, output_file, input_json],
                activity_full_alias=activity_full_alias
            )
            
            vkt.UserMessage.info("⚙️ Executing work item...")
            status_resp = work_item.execute(token=token, max_wait=600, interval=10)
            last_status = status_resp.get("status", "")
            
            vkt.UserMessage.info(f"Work item status: {last_status}")
            
            if last_status != "success":
                error_msg = f"Work item failed with status: {last_status}"
                vkt.UserMessage.info(f"❌ {error_msg}")
                raise vkt.UserError(error_msg)
            
            vkt.UserMessage.info("✅ Work item completed successfully!")
            vkt.progress_message("🔄 Translating updated model for viewing...", percentage=70)
            
            # Step 8: Translate the output model for viewing
            vkt.UserMessage.info("🔄 Starting model translation...")
            viewer_urn = translate_da_result_for_viewing(bucket_key, output_object_key)
            vkt.UserMessage.info(f"✅ Translation complete! URN: {viewer_urn}")
            vkt.progress_message("✅ Updated model ready for viewing!", percentage=100)
            
            # Step 9: Return the viewer URN to display in APS viewer
            return APSresult(urn=viewer_urn)
            
        finally:
            # Clean up temporary file
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    @staticmethod
    def create_json_from_params(params, **kwargs) -> list[dict[str, Any]]:
        """
        Create JSON configuration for type parameters.
        Groups all targets by parameter name and parameter group.
        Returns an array of parameter configurations, one for each unique parameter.
        """
        from collections import defaultdict
        
        # Group rows by (parameter_name, parameter_group)
        grouped = defaultdict(list)
        
        for row in params.targets:
            key = (row["parameter_name"], row["parameter_group"])
            grouped[key].append({
                "TypeName": row["type_name"],
                "FamilyName": row["family_name"],
                "Value": row["value"]
            })
        
        # Build the result array
        result = []
        for (param_name, param_group), targets in grouped.items():
            result.append({
                "ParameterName": param_name,
                "ParameterGroup": param_group,
                "Targets": targets
            })
        
        return result

