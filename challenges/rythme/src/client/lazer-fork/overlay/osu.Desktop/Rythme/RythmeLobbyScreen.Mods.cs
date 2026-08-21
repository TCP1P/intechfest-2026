using System.Collections.Generic;
using System.Linq;
using osu.Framework.Allocation;
using osu.Framework.Bindables;
using osu.Framework.Screens;
using osu.Game.Graphics.UserInterfaceV2;
using osu.Game.Rulesets;
using osu.Game.Rulesets.Mods;
using osu.Game.Utils;
using Rythme.Desktop;

namespace osu.Desktop.Rythme
{
    internal partial class RythmeLobbyScreen
    {
        private readonly RythmeModLoadouts loadouts = new RythmeModLoadouts();

        [Resolved]
        private RulesetStore rulesets { get; set; } = null!;

        private RoundedButton loadoutModeButton = null!;
        private string editingLoadoutRuleset = RythmeModLoadouts.FirstRuleset;
        private bool suppressLoadoutPersistence;

        private void onModsChanged(ValueChangedEvent<IReadOnlyList<Mod>> change)
        {
            IReadOnlyList<Mod> selected = change.NewValue ?? Array.Empty<Mod>();
            if (!suppressLoadoutPersistence &&
                currentRun == null &&
                !preparing &&
                Ruleset.Value.OnlineID == RythmeBeatmapLibrary.ModeFor(editingLoadoutRuleset))
            {
                try
                {
                    if (selected.Count > RythmeModLoadouts.MaximumMods ||
                        selected.Any(mod => mod == null || !UpstreamModCatalog.IsArenaPlayable(mod)) ||
                        !ModUtils.CheckCompatibleSet(selected, out _))
                        throw new ArgumentException("The selected combination is not available for synchronized play.");
                    loadouts.Set(editingLoadoutRuleset, selected.Select(mod => mod.Acronym));
                }
                catch (ArgumentException error)
                {
                    setModsWithoutSaving(change.OldValue ?? Array.Empty<Mod>());
                    status.Text = $"MOD LOADOUT NOT SAVED\n{error.Message}\nThe previous compatible loadout was restored.";
                    return;
                }
            }

            updateModDisplay();
        }

        private void setModsWithoutSaving(IReadOnlyList<Mod> mods)
        {
            suppressLoadoutPersistence = true;
            try
            {
                Mods.Value = mods ?? Array.Empty<Mod>();
            }
            finally
            {
                suppressLoadoutPersistence = false;
            }
        }

        private void applySavedLoadout(string ruleset)
        {
            RulesetInfo rulesetInfo = rulesets.GetRuleset(RythmeBeatmapLibrary.ModeFor(ruleset))
                                      ?? throw new InvalidOperationException($"The {ruleset} ruleset is unavailable.");
            suppressLoadoutPersistence = true;
            try
            {
                Ruleset.Value = rulesetInfo;
                Mods.Value = UpstreamModCatalog.CreateArenaLoadout(rulesetInfo, loadouts.Get(ruleset));
            }
            finally
            {
                suppressLoadoutPersistence = false;
            }
        }

        private void cycleLoadoutMode(int direction)
        {
            if (!loadoutModeButton.Enabled.Value || currentRun != null || preparing)
                return;

            editingLoadoutRuleset = RythmeModLoadouts.Move(editingLoadoutRuleset, direction);
            applySavedLoadout(editingLoadoutRuleset);
            updateFutureLoadoutDisplay(true);
            status.Text = $"EDITING SAVED {displayRuleset(editingLoadoutRuleset)} LOADOUT  •  {loadouts.Describe(editingLoadoutRuleset)}\n" +
                          "Select EDIT MODS or press F1 for the native osu! chooser. Left/right switches modes.\n" +
                          "The matching loadout applies only after that mode is revealed.";
        }

        private void handleModsAction()
        {
            if (!this.IsCurrentScreen() || currentRun != null || preparing || autoReadying)
                return;

            string targetRuleset = preparedHeatId != "" && preparedHeat?.Selection != null
                ? preparedHeat.Selection.Ruleset
                : editingLoadoutRuleset;
            editingLoadoutRuleset = targetRuleset;
            applySavedLoadout(targetRuleset);
            updateModDisplay();
            status.Text = $"EDITING {displayRuleset(targetRuleset)} MODS  •  {loadouts.Describe(targetRuleset)}\n" +
                          "Select any compatible native mod combination; changes save immediately for this mode.\n" +
                          "Custom mod settings stay disabled so synchronized scoring remains verifiable.";
            modSelectOverlay.Show();
        }

        private void updateModDisplay()
        {
            if (modsButton == null || loadoutModeButton == null || modeText == null)
                return;
            if (preparedHeatId == "" || preparedHeat?.Selection == null)
            {
                updateFutureLoadoutDisplay(session != null && !preparing && currentRun == null && !autoReadying);
                return;
            }

            AppliedModSet applied = describeSelectedMods();
            string ruleset = preparedHeat.Selection.Ruleset;
            loadoutModeButton.Text = $"MODE  •  {RythmeModLoadouts.DisplayName(ruleset)}";
            loadoutModeButton.Enabled.Value = false;
            loadoutModeButton.TooltipText = "The coordinator revealed this heat's ruleset.";
            modsButton.Text = $"MODS  •  {compactMods(applied.Acronym)}  •  {applied.ScoreMultiplierMilli / 1000.0:0.00}x";
            modsButton.TooltipText = $"Open the native {displayRuleset(ruleset)} mod selector (F1). Current: {applied.Acronym}.";
            modeText.Text = $"{displayRuleset(ruleset)}  •  {applied.Acronym}  •  " +
                            $"{applied.RateMilli / 1000.0:0.00}x RATE  •  {applied.ScoreMultiplierMilli / 1000.0:0.00}x SCORE";
            updateSavedLoadoutSummary();
        }

        private void updateFutureLoadoutDisplay(bool enabled)
        {
            if (modsButton == null || loadoutModeButton == null || modeText == null)
                return;

            string selected = loadouts.Describe(editingLoadoutRuleset);
            loadoutModeButton.Text = $"MODE  •  {RythmeModLoadouts.DisplayName(editingLoadoutRuleset)}";
            loadoutModeButton.Enabled.Value = enabled;
            loadoutModeButton.TooltipText = "Choose a saved loadout to edit. Click or use left/right arrows.";
            modsButton.Text = $"EDIT MODS  •  {compactMods(selected)}";
            modsButton.Enabled.Value = enabled;
            modsButton.TooltipText =
                $"Open the native {displayRuleset(editingLoadoutRuleset)} mod selector (F1). Saved: {selected}.";
            modeText.Text = loadoutSummary();
            updateSavedLoadoutSummary();
        }

        private string loadoutSummary() =>
            "SAVED MODS  •  " + string.Join("  •  ", new[] { "osu", "taiko", "fruits", "mania" }.Select(ruleset =>
                $"{RythmeModLoadouts.DisplayName(ruleset)} {compactMods(loadouts.Describe(ruleset), 11)}"));

        private void updateSavedLoadoutSummary()
        {
            if (savedLoadoutText == null)
                return;

            savedLoadoutText.Text = string.Join("  •  ", new[] { "osu", "taiko", "fruits", "mania" }.Select(ruleset =>
                $"{RythmeModLoadouts.DisplayName(ruleset)} {compactMods(loadouts.Describe(ruleset), 8)}"));
        }

        private static string compactMods(string acronyms, int maximumLength = 15)
        {
            string compact = acronyms.Replace(',', '+');
            return compact.Length <= maximumLength ? compact : compact[..(maximumLength - 1)] + "…";
        }
    }
}
