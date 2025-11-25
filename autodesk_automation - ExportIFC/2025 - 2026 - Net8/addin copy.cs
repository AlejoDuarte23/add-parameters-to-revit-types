using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;

using Autodesk.Revit.ApplicationServices;
using Autodesk.Revit.DB;
using DesignAutomationFramework;
using Newtonsoft.Json;

namespace IfcExportDA
{
    public class App : IExternalDBApplication
    {
        private const string SettingsLocalName = "ifc_settings.json";
        private const string ResultFolderName = "result";

        public ExternalDBApplicationResult OnStartup(ControlledApplication app)
        {
            DesignAutomationBridge.DesignAutomationReadyEvent += OnReady;
            return ExternalDBApplicationResult.Succeeded;
        }

        public ExternalDBApplicationResult OnShutdown(ControlledApplication app)
        {
            DesignAutomationBridge.DesignAutomationReadyEvent -= OnReady;
            return ExternalDBApplicationResult.Succeeded;
        }

        private void OnReady(object sender, DesignAutomationReadyEventArgs e)
        {
            try
            {
                Run(e.DesignAutomationData);
                e.Succeeded = true;
            }
            catch (Exception ex)
            {
                Console.WriteLine("DA, " + ex);
                e.Succeeded = false;
            }
        }

        private static void Run(DesignAutomationData data)
        {
            if (data == null) throw new ArgumentNullException(nameof(data));
            Application rvtApp = data.RevitApp ?? throw new InvalidOperationException("RevitApp is null");
            Document doc = data.RevitDoc ?? throw new InvalidOperationException("RevitDoc is null");

            string wd = Directory.GetCurrentDirectory();
            string settingsPath = Path.Combine(wd, SettingsLocalName);

            Console.WriteLine("DA, Working folder, " + wd);
            Console.WriteLine("DA, Reading settings, " + settingsPath);

            IfcSettings settings = ReadSettings(settingsPath);

            string outDir = Path.Combine(wd, ResultFolderName);
            Directory.CreateDirectory(outDir);

            List<View> targetViews = ResolveViews(doc, settings.ViewNames);
            if (targetViews.Count == 0)
                throw new InvalidOperationException("No matching printable views for provided view_names");

            int okCount = 0;

            // Group for cleanliness in journal, per view transactions for the Export call
            using (TransactionGroup tg = new TransactionGroup(doc, "IFC Export Group"))
            {
                tg.Start();

                foreach (View view in targetViews)
                {
                    IFCExportOptions ifcOpts = BuildIfcOptions(doc, settings);
                    ifcOpts.FilterViewId = view.Id;

                    string safeViewName = MakeSafe(view.Name);
                    string safeModel = MakeSafe(doc.Title);
                    string fileName = safeModel + "__" + safeViewName + ".ifc";

                    using (Transaction tx = new Transaction(doc, "IFC Export, " + view.Name))
                    {
                        tx.Start();

                        bool exported = doc.Export(outDir, fileName, ifcOpts);
                        if (!exported)
                            throw new InvalidOperationException("IFC export returned false for, " + view.Name);

                        tx.Commit();
                    }

                    okCount++;
                    Console.WriteLine("DA, Exported, " + fileName);
                }

                tg.Assimilate();
            }

            // Optional, keep a copy of the RVT that ran
            try
            {
                string rvtOut = Path.Combine(outDir, "result.rvt");
                var sao = new SaveAsOptions { OverwriteExistingFile = true };
                doc.SaveAs(rvtOut, sao);
                Console.WriteLine("DA, Saved, result.rvt");
            }
            catch (Exception ex)
            {
                Console.WriteLine("DA, Save RVT failed, " + ex.Message);
            }

            Console.WriteLine("DA, Done, files, " + okCount);
        }

        private static IfcSettings ReadSettings(string path)
        {
            if (!File.Exists(path))
                throw new FileNotFoundException("Settings file not found", path);

            string json = File.ReadAllText(path);
            var settings = JsonConvert.DeserializeObject<IfcSettings>(json);
            if (settings == null)
                throw new InvalidOperationException("Settings JSON is invalid");

            return settings;
        }

        private static List<View> ResolveViews(Document doc, List<string> names)
        {
            var printable = new FilteredElementCollector(doc)
                .OfClass(typeof(View))
                .Cast<View>()
                .Where(v => !v.IsTemplate && v.CanBePrinted)
                .ToList();

            var byName = printable
                .GroupBy(v => v.Name?.Trim() ?? string.Empty, StringComparer.OrdinalIgnoreCase)
                .ToDictionary(
                    g => g.Key,
                    g => g.OrderByDescending(IsPreferredForIfc).ThenBy(v => v.Id.Value).ToList(),
                    StringComparer.OrdinalIgnoreCase);

            var result = new List<View>();

            foreach (string raw in names ?? new List<string>())
            {
                string key = (raw ?? string.Empty).Trim();
                if (key.Length == 0) continue;
                if (byName.TryGetValue(key, out var candidates) && candidates.Count > 0)
                    result.Add(candidates.First());
            }

            return result;
        }

        private static IFCExportOptions BuildIfcOptions(Document doc, IfcSettings s)
        {
            var opts = new IFCExportOptions
            {
                FileVersion = MapIfcVersion(s.FileVersion),
                ExportBaseQuantities = s.ExportBaseQuantities,
                SpaceBoundaryLevel = s.SpaceBoundaryLevel
            };

            // Mapping file is optional
            if (!string.IsNullOrWhiteSpace(s.FamilyMappingFile))
            {
                string mapPath = s.FamilyMappingFile;
                if (!Path.IsPathRooted(mapPath))
                    mapPath = Path.Combine(Directory.GetCurrentDirectory(), mapPath);

                if (File.Exists(mapPath))
                    opts.FamilyMappingFile = mapPath;
                else
                    Console.WriteLine("DA, Mapping file not found, " + mapPath);
            }

            if (s.TessellationLevelOfDetail > 0)
                opts.AddOption("TessellationLevelOfDetail", s.TessellationLevelOfDetail.ToString(CultureInfo.InvariantCulture));
            if (s.UseOnlyTriangulation)
                opts.AddOption("UseOnlyTriangulation", "true");

            opts.AddOption("SitePlacement", s.SitePlacement.ToString(CultureInfo.InvariantCulture));

            AddBool(opts, "ExportInternalRevitPropertySets", s.ExportInternalRevitPropertySets);
            AddBool(opts, "ExportIFCCommonPropertySets", s.ExportIFCCommonPropertySets);
            AddBool(opts, "ExportAnnotations", s.ExportAnnotations);
            AddBool(opts, "Export2DElements", s.Export2DElements);
            AddBool(opts, "ExportRoomsInView", s.ExportRoomsInView);
            AddBool(opts, "VisibleElementsOfCurrentView", s.VisibleElementsOfCurrentView);
            AddBool(opts, "ExportLinkedFiles", s.ExportLinkedFiles);
            AddBool(opts, "IncludeSteelElements", s.IncludeSteelElements);
            AddBool(opts, "ExportPartsAsBuildingElements", s.ExportPartsAsBuildingElements);
            AddBool(opts, "UseActiveViewGeometry", s.UseActiveViewGeometry);
            AddBool(opts, "UseFamilyAndTypeNameForReference", s.UseFamilyAndTypeNameForReference);
            AddBool(opts, "Use2DRoomBoundaryForVolume", s.Use2DRoomBoundaryForVolume);
            AddBool(opts, "IncludeSiteElevation", s.IncludeSiteElevation);
            AddBool(opts, "ExportBoundingBox", s.ExportBoundingBox);
            AddBool(opts, "ExportSolidModelRep", s.ExportSolidModelRep);
            AddBool(opts, "StoreIFCGUID", s.StoreIFCGUID);
            AddBool(opts, "ExportSchedulesAsPsets", s.ExportSchedulesAsPsets);
            AddBool(opts, "ExportSpecificSchedules", s.ExportSpecificSchedules);

            if (s.ExportUserDefinedPsets)
            {
                AddBool(opts, "ExportUserDefinedPsets", true);
                if (!string.IsNullOrWhiteSpace(s.ExportUserDefinedPsetsFileName))
                    opts.AddOption("ExportUserDefinedPsetsFileName", s.ExportUserDefinedPsetsFileName);
            }

            if (s.ExportUserDefinedParameterMapping)
            {
                AddBool(opts, "ExportUserDefinedParameterMapping", true);
                if (!string.IsNullOrWhiteSpace(s.ExportUserDefinedParameterMappingFileName))
                    opts.AddOption("ExportUserDefinedParameterMappingFileName", s.ExportUserDefinedParameterMappingFileName);
            }

            if (!string.IsNullOrWhiteSpace(s.ActivePhase))
                opts.AddOption("ActivePhase", s.ActivePhase);

            return opts;
        }

        private static void AddBool(IFCExportOptions opts, string name, bool val)
        {
            opts.AddOption(name, val ? "true" : "false");
        }

        private static IFCVersion MapIfcVersion(string label)
        {
            if (string.IsNullOrWhiteSpace(label))
                return IFCVersion.IFC2x3;

            switch (label.Trim())
            {
                case "IFC2x2":
                    return IFCVersion.IFC2x2;
                case "IFC2x3":
                case "IFC2x3 Coordination View 2.0":
                case "IFC2x3 Basic FM Handover (BFM)":
                case "IFC2x3 Extended FM Handover":
                case "IFC2x3 COBie 2010":
                    return IFCVersion.IFC2x3;
                case "IFC4":
                case "IFC4 Reference View (RV)":
                case "IFC4 Design Transfer View (DTV)":
                    return IFCVersion.IFC4;
                default:
                    return IFCVersion.IFC2x3;
            }
        }

        private static string MakeSafe(string name)
        {
            foreach (char c in Path.GetInvalidFileNameChars())
                name = name.Replace(c, '_');
            return name;
        }

        private static int IsPreferredForIfc(View v)
        {
            if (v is View3D) return 3;
            if (v is ViewPlan) return 2;
            if (v is ViewSection) return 1;
            return 0;
        }
    }

    public class IfcSettings
    {
        [JsonProperty("view_names")]
        public List<string> ViewNames { get; set; } = new List<string>();

        public string FileVersion { get; set; } = "IFC2x3 Coordination View 2.0";
        public string IFCFileType { get; set; } = "IFC";
        public bool ExportBaseQuantities { get; set; } = false;
        public int SpaceBoundaryLevel { get; set; } = 0;
        public string FamilyMappingFile { get; set; } = string.Empty;

        public bool ExportInternalRevitPropertySets { get; set; } = false;
        public bool ExportIFCCommonPropertySets { get; set; } = true;
        public bool ExportAnnotations { get; set; } = false;
        public bool Export2DElements { get; set; } = false;
        public bool ExportRoomsInView { get; set; } = false;
        public bool VisibleElementsOfCurrentView { get; set; } = false;
        public bool ExportLinkedFiles { get; set; } = false;
        public bool IncludeSteelElements { get; set; } = false;
        public bool ExportPartsAsBuildingElements { get; set; } = true;
        public bool UseActiveViewGeometry { get; set; } = false;
        public bool UseFamilyAndTypeNameForReference { get; set; } = false;
        public bool Use2DRoomBoundaryForVolume { get; set; } = false;
        public bool IncludeSiteElevation { get; set; } = false;
        public bool ExportBoundingBox { get; set; } = false;
        public bool ExportSolidModelRep { get; set; } = false;
        public bool StoreIFCGUID { get; set; } = false;
        public bool ExportSchedulesAsPsets { get; set; } = false;
        public bool ExportSpecificSchedules { get; set; } = false;
        public bool ExportUserDefinedPsets { get; set; } = false;
        public string ExportUserDefinedPsetsFileName { get; set; } = string.Empty;
        public bool ExportUserDefinedParameterMapping { get; set; } = false;
        public string ExportUserDefinedParameterMappingFileName { get; set; } = string.Empty;
        public string ActivePhase { get; set; } = string.Empty;
        public int SitePlacement { get; set; } = 0;
        public double TessellationLevelOfDetail { get; set; } = 0.0;
        public bool UseOnlyTriangulation { get; set; } = false;
    }
}
