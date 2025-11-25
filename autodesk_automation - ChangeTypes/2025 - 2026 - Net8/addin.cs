
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Autodesk.Revit.ApplicationServices;
using Autodesk.Revit.DB;
using DesignAutomationFramework;
using Newtonsoft.Json;

namespace TypeParametersFromJsonDA
{
    public class ParameterConfig
    {
        public string ParameterName { get; set; }
        public string ParameterGroup { get; set; }
        public List<TypeTarget> Targets { get; set; } = new List<TypeTarget>();
    }

    public class TypeTarget
    {
        public string TypeName { get; set; }
        public string FamilyName { get; set; }
        public string Value { get; set; }
    }

    public class App : IExternalDBApplication
    {
        private const string JsonLocalName = "revit_type_params.json";
        private const string SharedParamFileName = "revit_json_shared_parameters.txt";

        public ExternalDBApplicationResult OnStartup(ControlledApplication app)
        {
            DesignAutomationBridge.DesignAutomationReadyEvent += OnDesignAutomationReady;
            return ExternalDBApplicationResult.Succeeded;
        }

        public ExternalDBApplicationResult OnShutdown(ControlledApplication app)
        {
            DesignAutomationBridge.DesignAutomationReadyEvent -= OnDesignAutomationReady;
            return ExternalDBApplicationResult.Succeeded;
        }

        private void OnDesignAutomationReady(object sender, DesignAutomationReadyEventArgs e)
        {
            try
            {
                Run(e.DesignAutomationData);
                e.Succeeded = true;
            }
            catch (Exception ex)
            {
                Console.WriteLine("DA ERROR: " + ex);
                e.Succeeded = false;
            }
        }

        private static void Run(DesignAutomationData data)
        {
            if (data == null) throw new ArgumentNullException(nameof(data));

            Application rvtApp = data.RevitApp ?? throw new InvalidOperationException("RevitApp is null.");
            Document doc = data.RevitDoc ?? throw new InvalidOperationException("RevitDoc is null.");

            string wd = Directory.GetCurrentDirectory();
            string jsonPath = Path.Combine(wd, JsonLocalName);

            Console.WriteLine("DA: Working folder, " + wd);
            Console.WriteLine("DA: Looking for, " + jsonPath);

            if (!File.Exists(jsonPath))
            {
                Console.WriteLine("DA: JSON not found, no changes applied.");
                SaveResult(doc);
                return;
            }

            List<ParameterConfig> configs = null;

            try
            {
                string json = File.ReadAllText(jsonPath);
                // First try array of configs
                try
                {
                    configs = JsonConvert.DeserializeObject<List<ParameterConfig>>(json);
                }
                catch
                {
                    configs = null;
                }

                // Fallback to single object for backward compatibility
                if (configs == null)
                {
                    ParameterConfig single = JsonConvert.DeserializeObject<ParameterConfig>(json);
                    if (single != null)
                    {
                        configs = new List<ParameterConfig> { single };
                    }
                }

                if (configs == null || configs.Count == 0)
                {
                    Console.WriteLine("DA: JSON did not contain valid parameter config.");
                    SaveResult(doc);
                    return;
                }

                Console.WriteLine("DA: Parsed " + JsonLocalName + ", configs count, " + configs.Count);
            }
            catch (Exception ex)
            {
                Console.WriteLine("DA: Invalid JSON, " + ex.Message);
                SaveResult(doc);
                return;
            }

            List<ElementType> allTypes = new FilteredElementCollector(doc)
                .OfClass(typeof(ElementType))
                .Cast<ElementType>()
                .Where(t => t.Category != null)
                .ToList();

            if (allTypes.Count == 0)
            {
                Console.WriteLine("DA: No element types found in document.");
                SaveResult(doc);
                return;
            }

            string sharedParamPath = Path.Combine(wd, SharedParamFileName);
            EnsureSharedParameterFile(sharedParamPath);

            rvtApp.SharedParametersFilename = sharedParamPath;
            DefinitionFile defFile = rvtApp.OpenSharedParameterFile();

            if (defFile == null)
            {
                Console.WriteLine("DA: Cannot open shared parameter file, " + sharedParamPath);
                SaveResult(doc);
                return;
            }

            const string groupName = "JsonParameters";
            DefinitionGroup defGroup = defFile.Groups.get_Item(groupName) ?? defFile.Groups.Create(groupName);

            BindingMap map = doc.ParameterBindings;

            int totalUpdatedTypes = 0;
            int totalUnmatchedTargets = 0;
            int totalConfigsProcessed = 0;

            using (Transaction tx = new Transaction(doc, "Add type parameters from JSON"))
            {
                tx.Start();

                foreach (ParameterConfig config in configs)
                {
                    if (config == null)
                    {
                        continue;
                    }

                    if (string.IsNullOrWhiteSpace(config.ParameterName))
                    {
                        Console.WriteLine("DA: Skipping config without ParameterName.");
                        continue;
                    }

                    if (config.Targets == null || config.Targets.Count == 0)
                    {
                        Console.WriteLine("DA: Config for parameter '" + config.ParameterName + "' has no Targets, skipping.");
                        continue;
                    }

                    HashSet<ElementId> targetTypeIds = new HashSet<ElementId>();
                    CategorySet catSet = rvtApp.Create.NewCategorySet();
                    int unmatchedTargets = 0;

                    foreach (TypeTarget target in config.Targets)
                    {
                        if (target == null || string.IsNullOrWhiteSpace(target.TypeName))
                        {
                            continue;
                        }

                        IEnumerable<ElementType> candidates = allTypes
                            .Where(t =>
                                string.Equals(t.Name, target.TypeName, StringComparison.OrdinalIgnoreCase));

                        if (!string.IsNullOrWhiteSpace(target.FamilyName))
                        {
                            candidates = candidates.Where(t =>
                                string.Equals(t.FamilyName, target.FamilyName, StringComparison.OrdinalIgnoreCase));
                        }

                        ElementType match = candidates.FirstOrDefault();

                        if (match == null)
                        {
                            unmatchedTargets++;
                            Console.WriteLine($"DA: [{config.ParameterName}] Target not matched, Type '{target.TypeName}', Family '{target.FamilyName}'.");
                            continue;
                        }

                        if (!targetTypeIds.Contains(match.Id))
                        {
                            targetTypeIds.Add(match.Id);

                            Category cat = match.Category;
                            if (cat != null && !catSet.Contains(cat))
                            {
                                catSet.Insert(cat);
                            }
                        }
                    }

                    if (targetTypeIds.Count == 0 || catSet.IsEmpty)
                    {
                        Console.WriteLine("DA: No matching types found for parameter '" + config.ParameterName + "'.");
                        totalUnmatchedTargets += unmatchedTargets;
                        continue;
                    }

                    Definition definition = defGroup.Definitions.get_Item(config.ParameterName);
                    if (definition == null)
                    {
                        ExternalDefinitionCreationOptions options =
                            new ExternalDefinitionCreationOptions(
                                config.ParameterName,
                                SpecTypeId.String.Text);

                        options.Description = "Parameter created from JSON config file.";

                        definition = defGroup.Definitions.Create(options);
                    }

                    ExternalDefinition externalDef = definition as ExternalDefinition;
                    if (externalDef == null)
                    {
                        Console.WriteLine("DA: Failed to create external definition for '" + config.ParameterName + "'.");
                        totalUnmatchedTargets += unmatchedTargets;
                        continue;
                    }

                    ForgeTypeId groupTypeId = GetGroupTypeIdFromString(config.ParameterGroup);

                    ElementBinding binding = rvtApp.Create.NewTypeBinding(catSet);
                    bool inserted = map.Insert(externalDef, binding, groupTypeId);

                    if (!inserted)
                    {
                        map.ReInsert(externalDef, binding, groupTypeId);
                    }

                    int updatedTypes = 0;

                    foreach (TypeTarget target in config.Targets)
                    {
                        if (target == null || string.IsNullOrWhiteSpace(target.TypeName))
                        {
                            continue;
                        }

                        IEnumerable<ElementType> candidates = allTypes
                            .Where(t =>
                                string.Equals(t.Name, target.TypeName, StringComparison.OrdinalIgnoreCase));

                        if (!string.IsNullOrWhiteSpace(target.FamilyName))
                        {
                            candidates = candidates.Where(t =>
                                string.Equals(t.FamilyName, target.FamilyName, StringComparison.OrdinalIgnoreCase));
                        }

                        ElementType match = candidates.FirstOrDefault();
                        if (match == null)
                        {
                            continue;
                        }

                        Parameter param = match.LookupParameter(config.ParameterName);
                        if (param != null && !param.IsReadOnly && param.StorageType == StorageType.String)
                        {
                            param.Set(target.Value ?? string.Empty);
                            updatedTypes++;
                        }
                    }

                    Console.WriteLine("DA: Parameter, " + config.ParameterName);
                    Console.WriteLine("DA: Categories bound, " + catSet.Size);
                    Console.WriteLine("DA: Types updated, " + updatedTypes);
                    Console.WriteLine("DA: Unmatched targets, " + unmatchedTargets);

                    totalUpdatedTypes += updatedTypes;
                    totalUnmatchedTargets += unmatchedTargets;
                    totalConfigsProcessed++;
                }

                tx.Commit();
            }

            Console.WriteLine("DA: Summary");
            Console.WriteLine("DA: Parameter configs processed, " + totalConfigsProcessed);
            Console.WriteLine("DA: Total types updated, " + totalUpdatedTypes);
            Console.WriteLine("DA: Total unmatched targets, " + totalUnmatchedTargets);

            SaveResult(doc);
        }

        private static void SaveResult(Document doc)
        {
            try
            {
                var sao = new SaveAsOptions { OverwriteExistingFile = true };
                string outPath = Path.Combine(Directory.GetCurrentDirectory(), "result.rvt");
                doc.SaveAs(outPath, sao);
                Console.WriteLine("DA: Saved result.rvt");
            }
            catch (Exception ex)
            {
                Console.WriteLine("DA: Save failed, " + ex.Message);
                throw;
            }
        }

        private static void EnsureSharedParameterFile(string sharedParamPath)
        {
            if (!File.Exists(sharedParamPath))
            {
                using (StreamWriter writer = new StreamWriter(sharedParamPath))
                {
                    writer.WriteLine("# This file is created automatically from JSON config");
                    writer.WriteLine("*PARAMETER VERSION 1.0");
                    writer.WriteLine("");
                }
            }
        }

        private static ForgeTypeId GetGroupTypeIdFromString(string groupName)
        {
            if (string.IsNullOrWhiteSpace(groupName))
            {
                return GroupTypeId.Data;
            }

            string g = groupName.Trim().ToUpperInvariant();

            switch (g)
            {
                case "PG_TEXT":
                case "TEXT":
                    return GroupTypeId.Text;

                case "PG_GEOMETRY":
                case "GEOMETRY":
                    return GroupTypeId.Geometry;

                case "PG_IDENTITY_DATA":
                case "IDENTITY_DATA":
                    return GroupTypeId.IdentityData;

                case "PG_CONSTRAINTS":
                case "CONSTRAINTS":
                    return GroupTypeId.Constraints;

                case "PG_DATA":
                case "DATA":
                default:
                    return GroupTypeId.Data;
            }
        }
    }
}