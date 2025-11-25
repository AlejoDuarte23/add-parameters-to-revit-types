"""
Helper functions for IFC export functionality.
"""
from typing import Any


def create_ifc_export_json(selected_view_names: list[str]) -> dict[str, Any]:
    """
    Create IFC export settings JSON configuration from selected view names.
    """
    # Base configuration with sensible defaults
    config = {
        "view_names": selected_view_names,
        "FileVersion": "IFC4",
        "IFCFileType": "IFC",
        "ExportBaseQuantities": True,
        "SpaceBoundaryLevel": 2,
        "FamilyMappingFile": "",
        "ExportInternalRevitPropertySets": False,
        "ExportIFCCommonPropertySets": True,
        "ExportAnnotations": False,
        "Export2DElements": False,
        "ExportRoomsInView": False,
        "VisibleElementsOfCurrentView": False,
        "ExportLinkedFiles": False,
        "IncludeSteelElements": False,
        "ExportPartsAsBuildingElements": True,
        "UseActiveViewGeometry": False,
        "UseFamilyAndTypeNameForReference": False,
        "Use2DRoomBoundaryForVolume": False,
        "IncludeSiteElevation": False,
        "ExportBoundingBox": False,
        "ExportSolidModelRep": False,
        "StoreIFCGUID": False,
        "ExportSchedulesAsPsets": False,
        "ExportSpecificSchedules": False,
        "ExportUserDefinedPsets": False,
        "ExportUserDefinedPsetsFileName": "",
        "ExportUserDefinedParameterMapping": False,
        "ExportUserDefinedParameterMappingFileName": "",
        "ActivePhase": "",
        "SitePlacement": 0,
        "TessellationLevelOfDetail": 0.0,
        "UseOnlyTriangulation": False
    }
    
    return config
