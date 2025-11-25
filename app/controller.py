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
from viktor.core import Storage
from app.model_translation import (
    translate_da_result_for_viewing,
    get_revit_version_from_oss_object,
    get_viewables_from_urn,
    REVIT_VERSION_CONFIG
)

import json

load_dotenv()

CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")

# Storage keys
STORED_OUTPUT_FILE_KEY = "da_output_file"
STORED_OUTPUT_URN_KEY = "da_output_urn"

class APSresult(vkt.WebResult):
    def __init__(self, urn: Annotated[str, "bs64 URN from model derivative"] | None = None):
        token = get_token(CLIENT_ID, CLIENT_SECRET)
        
        # Get viewables from the translated model
        viewables = []
        if urn:
            try:
                viewables = get_viewables_from_urn(urn)
            except Exception as e:
                print(f"Warning: Could not fetch viewables: {e}")
        
        html = (Path(__file__).parent / 'ViewableViewer.html').read_text()
        html = html.replace('APS_TOKEN_PLACEHOLDER', token)
        html = html.replace('URN_PLACEHOLDER', urn)
        html = html.replace('VIEWABLES_PLACEHOLDER', json.dumps(viewables))
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
    
    download_info = vkt.Text("""## Download Updated Model
After processing your model in the 'Updated Model with Parameters' view, you can download the modified Revit file with the new parameters added.""")
    
    download_updated = vkt.DownloadButton(
        "Download updated Revit model",
        method="download_updated_model",
        longpoll=True,
    )
    
class Controller(vkt.Controller):
    parametrization = Parametrization

    @staticmethod
    def clear_da_storage(storage: Storage) -> None:
        """Remove stored DA output when there is no valid input file."""
        for key in (STORED_OUTPUT_FILE_KEY, STORED_OUTPUT_URN_KEY):
            try:
                storage.delete(key, scope="entity")
            except FileNotFoundError:
                pass

    @APSView("Model Viewer", duration_guess=40)
    def process_cadd_file(self, params, **kwargs) -> APSresult:
        
        file: vkt.File = params.cad_file.file
        file_bytes: bytes = file.getvalue_binary()
        name = params.cad_file.filename
        
        client_id = os.environ.get("CLIENT_ID")
        client_secret = os.environ.get("CLIENT_SECRET")

        if not client_id or not client_secret:
            raise vkt.UserError("CLIENT_ID and CLIENT_SECRET must be set in the environment variables.")
        
        vkt.UserMessage.info("Starting model processing workflow...")
        vkt.progress_message("Uploading model to APS...", percentage=10)
        
        # Step 1: Create a temporary file from the uploaded bytes
        with tempfile.NamedTemporaryFile(mode='wb', suffix=Path(name).suffix, delete=False) as temp_file:
            temp_file.write(file_bytes)
            temp_file_path = temp_file.name
        
        try:
            # Step 2: Upload the file to APS bucket and get bucket_key and object_key
            vkt.UserMessage.info(f"Uploading file: {name}")
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
            vkt.progress_message("Translating model for viewing...", percentage=40)
            
            # Step 3: Translate the uploaded model for viewing
            vkt.UserMessage.info("Starting model translation...")
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
        storage = Storage()

        # If file is gone: clean storage and stop
        if params.cad_file is None:
            self.clear_da_storage(storage)
            raise vkt.UserError("Please upload a CAD/Revit file first.")

        # If we already have an URN in storage, reuse it
        try:
            stored_urn_file = storage.get(STORED_OUTPUT_URN_KEY, scope="entity")
            if stored_urn_file:
                stored_urn = stored_urn_file.getvalue()
                vkt.UserMessage.info("Using stored Design Automation result.")
                vkt.progress_message("Updated model ready for viewing!", percentage=100)
                return APSresult(urn=stored_urn)
        except FileNotFoundError:
            pass

        file: vkt.File = params.cad_file.file
        file_bytes: bytes = file.getvalue_binary()
        name = params.cad_file.filename
        
        client_id = os.environ.get("CLIENT_ID")
        client_secret = os.environ.get("CLIENT_SECRET")

        if not client_id or not client_secret:
            raise vkt.UserError("CLIENT_ID and CLIENT_SECRET must be set in the environment variables.")
        
        vkt.UserMessage.info("Starting Design Automation workflow...")
        vkt.progress_message("Preparing files...", percentage=5)
        
        temp_file_path = None
        
        # Step 1: Create a temporary file from the uploaded bytes
        with tempfile.NamedTemporaryFile(mode='wb', suffix=Path(name).suffix, delete=False) as temp_file:
            temp_file.write(file_bytes)
            temp_file_path = temp_file.name
        
        try:
            # Step 2: Get authentication and set up activity
            vkt.UserMessage.info("Authenticating with APS...")
            token = get_token(client_id, client_secret)
            nickname = set_nickname(token, "myUniqueNickNameHere")
            
            vkt.progress_message("Uploading input file...", percentage=15)
            
            # Step 3: Generate unique bucket key and use simple object keys
            bucket_key = uuid.uuid4().hex
            input_object_key = "input.rvt"
            output_object_key = "output.rvt"
            
            # Step 4: Create input parameter for Revit file
            vkt.UserMessage.info(f"Uploading file: {name}")
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
            vkt.progress_message("Detecting Revit version...", percentage=25)
            
            # Step 4.5: Detect Revit version from the uploaded file
            vkt.UserMessage.info("Detecting Revit version from model...")
            revit_version = get_revit_version_from_oss_object(token, bucket_key, input_object_key)
            
            if not revit_version:
                raise Exception("Could not detect Revit version from the uploaded model. Please ensure the file is a valid Revit model.")
            
            if revit_version not in REVIT_VERSION_CONFIG:
                supported_versions = ", ".join(sorted(REVIT_VERSION_CONFIG.keys()))
                raise Exception(
                    f"Revit version {revit_version} is not supported. "
                    f"Supported versions are: {supported_versions}"
                )
            
            # Get activity configuration for detected version
            version_config = REVIT_VERSION_CONFIG[revit_version]
            activity_name = version_config["activity_name"]
            alias = version_config["alias"]
            activity_full_alias = f"{nickname}.{activity_name}+{alias}"
            
            vkt.UserMessage.info(f"✅ Using Revit {revit_version}")
            vkt.UserMessage.info(f"Using activity: {activity_full_alias}")
            vkt.progress_message("Setting up parameters...", percentage=35)
            
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
            vkt.UserMessage.info("Generating parameter configuration...")
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
            vkt.UserMessage.info("Creating work item...")
            vkt.progress_message("Running Design Automation (this may take a few minutes)...", percentage=45)
            
            work_item = WorkItem(
                parameters=[input_revit, output_file, input_json],
                activity_full_alias=activity_full_alias
            )
            
            vkt.UserMessage.info("Executing work item...")
            status_resp = work_item.execute(token=token, max_wait=600, interval=10)
            last_status = status_resp.get("status", "")
            
            vkt.UserMessage.info(f"Work item status: {last_status}")
            
            if last_status != "success":
                error_msg = f"Work item failed with status: {last_status}"
                vkt.UserMessage.info(f"❌ {error_msg}")
                raise vkt.UserError(error_msg)
            
            vkt.UserMessage.info("✅ Work item completed successfully!")
            vkt.progress_message("Translating updated model for viewing...", percentage=70)
            
            # Step 8: Translate the output model for viewing
            vkt.UserMessage.info("Starting model translation...")
            viewer_urn = translate_da_result_for_viewing(bucket_key, output_object_key)
            vkt.UserMessage.info(f"Translation complete! URN: {viewer_urn}")
            vkt.progress_message("Updated model ready for viewing!", percentage=100)
            
            # Step 9: Download DA output and store in Storage
            with tempfile.NamedTemporaryFile(mode="wb", suffix=".rvt", delete=False) as out_temp:
                output_temp_path = out_temp.name

            try:
                output_file.download_to(output_path=output_temp_path, token=token)

                with open(output_temp_path, "rb") as f:
                    out_bytes = f.read()

                viktor_output_file = vkt.File.from_data(out_bytes)
                storage.set(STORED_OUTPUT_FILE_KEY, data=viktor_output_file, scope="entity")
                
                # Store URN as a File object (Storage only accepts File objects)
                urn_file = vkt.File.from_data(viewer_urn)
                storage.set(STORED_OUTPUT_URN_KEY, data=urn_file, scope="entity")
            finally:
                if os.path.exists(output_temp_path):
                    os.unlink(output_temp_path)
            
            # Step 10: Return the viewer URN to display in APS viewer
            return APSresult(urn=viewer_urn)
            
        finally:
            # Clean up temporary file
            if temp_file_path and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    def download_updated_model(self, params, **kwargs):
        """
        Return the last updated Revit model stored in Storage.
        """
        storage = Storage()

        if params.cad_file is None:
            self.clear_da_storage(storage)
            raise vkt.UserError("Please upload a CAD/Revit file first.")

        # Check if file exists in storage
        stored_files = storage.list(scope="entity")
        
        if STORED_OUTPUT_FILE_KEY not in stored_files:
            raise vkt.UserError("No updated model available. Please process the file in 'Updated Model with Parameters' view first.")
        
        stored_file = storage.get(STORED_OUTPUT_FILE_KEY, scope="entity")
        file_bytes = stored_file.getvalue_binary()
        
        base_filename = params.cad_file.filename or 'model.rvt'
        rvt_filename = f"updated_{base_filename}"
        
        return vkt.DownloadResult(file_bytes, rvt_filename)

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

