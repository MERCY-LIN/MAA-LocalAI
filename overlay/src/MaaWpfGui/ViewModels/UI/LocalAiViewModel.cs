// <copyright file="LocalAiViewModel.cs" company="MaaAssistantArknights">
// Part of the MaaWpfGui project, maintained by the MaaAssistantArknights team (Maa Team)
// Copyright (C) 2021-2026 MaaAssistantArknights Contributors
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License v3.0 only as published by
// the Free Software Foundation, either version 3 of the License, or
// any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY
// </copyright>

#nullable enable
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Collections.Specialized;
using System.Linq;
using System.Threading.Tasks;
using JetBrains.Annotations;
using MaaWpfGui.Configuration.Factory;
using MaaWpfGui.Helper;
using MaaWpfGui.Models;
using MaaWpfGui.Services.LocalModels;
using MaaWpfGui.Utilities;
using MaaWpfGui.ViewModels.UserControl.TaskQueue;
using Serilog;
using Stylet;

namespace MaaWpfGui.ViewModels.UI;

/// <summary>
/// Coordinates local-model combat planning and applying a validated plan to the selected Fight task.
/// </summary>
public class LocalAiViewModel : Screen
{
    private static readonly ILogger _logger = Log.ForContext<LocalAiViewModel>();

    private string _goal = string.Empty;
    private string _summary = string.Empty;
    private string _reason = string.Empty;
    private string _statusMessage = string.Empty;
    private string _statusKey = "LocalAi.Status.Ready";
    private object[] _statusArguments = [];
    private bool _isBusy;
    private int _suggestedTimes = 1;

    /// <summary>
    /// Initializes a new instance of the <see cref="LocalAiViewModel"/> class.
    /// </summary>
    public LocalAiViewModel()
    {
        PropertyDependsOnUtility.InitializePropertyDependencies(this);
        PlanStages.CollectionChanged += PlanStagesOnCollectionChanged;
        LocalizationHelper.LanguageChanged += RefreshLocalization;
        RefreshLocalization();
    }

    /// <summary>
    /// Gets the validated stages returned by the model.
    /// </summary>
    public ObservableCollection<PlannedStageItem> PlanStages { get; } = [];

    /// <summary>
    /// Gets or sets the OpenAI-compatible endpoint.
    /// </summary>
    public string Endpoint
    {
        get => ConfigFactory.Root.LocalAi.Endpoint;
        set {
            if (ConfigFactory.Root.LocalAi.Endpoint == value)
            {
                return;
            }

            ConfigFactory.Root.LocalAi.Endpoint = value;
            NotifyOfPropertyChange();
        }
    }

    /// <summary>
    /// Gets or sets the model name.
    /// </summary>
    public string Model
    {
        get => ConfigFactory.Root.LocalAi.Model;
        set {
            if (ConfigFactory.Root.LocalAi.Model == value)
            {
                return;
            }

            ConfigFactory.Root.LocalAi.Model = value;
            NotifyOfPropertyChange();
        }
    }

    /// <summary>
    /// Gets or sets the request timeout.
    /// </summary>
    public int TimeoutSeconds
    {
        get => ConfigFactory.Root.LocalAi.TimeoutSeconds;
        set {
            int normalized = Math.Clamp(value, 5, 600);
            if (ConfigFactory.Root.LocalAi.TimeoutSeconds == normalized)
            {
                return;
            }

            ConfigFactory.Root.LocalAi.TimeoutSeconds = normalized;
            NotifyOfPropertyChange();
        }
    }

    /// <summary>
    /// Gets or sets a value indicating whether non-loopback endpoints are allowed.
    /// </summary>
    public bool AllowRemoteEndpoint
    {
        get => ConfigFactory.Root.LocalAi.AllowRemoteEndpoint;
        set {
            if (ConfigFactory.Root.LocalAi.AllowRemoteEndpoint == value)
            {
                return;
            }

            ConfigFactory.Root.LocalAi.AllowRemoteEndpoint = value;
            NotifyOfPropertyChange();
        }
    }

    /// <summary>
    /// Gets or sets the user's combat goal.
    /// </summary>
    public string Goal
    {
        get => _goal;
        set => SetAndNotify(ref _goal, value);
    }

    /// <summary>
    /// Gets the model's short plan summary.
    /// </summary>
    public string Summary
    {
        get => _summary;
        private set => SetAndNotify(ref _summary, value);
    }

    /// <summary>
    /// Gets the model's reasoning and stated assumptions.
    /// </summary>
    public string Reason
    {
        get => _reason;
        private set => SetAndNotify(ref _reason, value);
    }

    /// <summary>
    /// Gets the current operation status.
    /// </summary>
    public string StatusMessage
    {
        get => _statusMessage;
        private set => SetAndNotify(ref _statusMessage, value);
    }

    /// <summary>
    /// Gets a value indicating whether a model request is running.
    /// </summary>
    public bool IsBusy
    {
        get => _isBusy;
        private set {
            if (SetAndNotify(ref _isBusy, value))
            {
                NotifyOfPropertyChange(nameof(CanApply));
            }
        }
    }

    /// <summary>
    /// Gets or sets the requested clear count.
    /// </summary>
    public int SuggestedTimes
    {
        get => _suggestedTimes;
        set => SetAndNotify(ref _suggestedTimes, Math.Clamp(value, 1, 99));
    }

    /// <summary>
    /// Gets a value indicating whether a plan can be applied.
    /// </summary>
    public bool CanApply => !IsBusy && PlanStages.Any(stage => stage.IsSelected);

    /// <summary>
    /// Tests the configured endpoint.
    /// </summary>
    /// <returns>A task representing the operation.</returns>
    [UsedImplicitly]
    public async Task TestConnection()
    {
        if (IsBusy)
        {
            return;
        }

        IsBusy = true;
        SetStatus("LocalAi.Status.Testing");
        try
        {
            _ = await new LocalAiService(ConfigFactory.Root.LocalAi).TestConnectionAsync();
            SetStatus("LocalAi.Status.Connected");
        }
        catch (LocalAiException exception)
        {
            ShowError(exception);
        }
        catch (Exception exception)
        {
            _logger.Error(exception, "Unexpected local AI connection test failure");
            SetStatus("LocalAi.Error.Generic");
        }
        finally
        {
            IsBusy = false;
        }
    }

    /// <summary>
    /// Generates and validates a stage plan.
    /// </summary>
    /// <returns>A task representing the operation.</returns>
    [UsedImplicitly]
    public async Task GeneratePlan()
    {
        if (IsBusy)
        {
            return;
        }

        IsBusy = true;
        SetStatus("LocalAi.Status.Generating");
        try
        {
            var stages = Instances.StageManager
                .GetStageList(Instances.TaskQueueViewModel.CurDayOfWeek)
                .Where(stage => !string.IsNullOrWhiteSpace(stage.Value))
                .ToList();
            LocalAiPlan plan = await new LocalAiService(ConfigFactory.Root.LocalAi)
                .CreatePlanAsync(Goal, stages);

            PlanStages.Clear();
            foreach (string stageCode in plan.Stages)
            {
                var stage = Instances.StageManager.GetStageInfo(stageCode);
                PlanStages.Add(new PlannedStageItem(stageCode, stage.Display, stage.Tip));
            }

            Summary = plan.Summary;
            Reason = plan.Reason;
            SuggestedTimes = plan.Times;
            SetStatus("LocalAi.Status.Generated");
        }
        catch (LocalAiException exception)
        {
            ShowError(exception);
        }
        catch (Exception exception)
        {
            _logger.Error(exception, "Unexpected local AI planning failure");
            SetStatus("LocalAi.Error.Generic");
        }
        finally
        {
            IsBusy = false;
            NotifyOfPropertyChange(nameof(CanApply));
        }
    }

    /// <summary>
    /// Applies selected stages to the currently selected MAA Fight task.
    /// </summary>
    [UsedImplicitly]
    public void ApplyPlan()
    {
        string[] stages = PlanStages
            .Where(stage => stage.IsSelected)
            .Select(stage => stage.Code)
            .ToArray();
        (bool success, string messageKey) = FightTaskUserControlModel.Instance
            .TryApplyLocalAiStagePlan(stages, SuggestedTimes);
        SetStatus(messageKey);
        if (!success)
        {
            return;
        }

        _logger.Information(
            "Applied local AI plan to Fight task: stages={Stages}, times={Times}",
            string.Join(", ", stages),
            SuggestedTimes);
    }

    private void ShowError(LocalAiException exception)
    {
        string key = $"LocalAi.Error.{exception.Error}";
        SetStatus(key);
        _logger.Warning(
            "Local AI operation failed: error={Error}, detail={Detail}",
            exception.Error,
            exception.Detail);
    }

    private void SetStatus(string key, params object[] arguments)
    {
        _statusKey = key;
        _statusArguments = arguments;
        StatusMessage = arguments.Length == 0
            ? LocalizationHelper.GetString(key)
            : LocalizationHelper.GetStringFormat(key, arguments);
    }

    private void RefreshLocalization()
    {
        DisplayName = LocalizationHelper.GetString("LocalAi.Title");
        if (!IsBusy)
        {
            SetStatus(_statusKey, _statusArguments);
        }
    }

    private void PlanStagesOnCollectionChanged(
        object? sender,
        NotifyCollectionChangedEventArgs eventArgs)
    {
        if (eventArgs.OldItems is not null)
        {
            foreach (PlannedStageItem item in eventArgs.OldItems.OfType<PlannedStageItem>())
            {
                item.PropertyChanged -= PlannedStageOnPropertyChanged;
            }
        }

        if (eventArgs.NewItems is not null)
        {
            foreach (PlannedStageItem item in eventArgs.NewItems.OfType<PlannedStageItem>())
            {
                item.PropertyChanged += PlannedStageOnPropertyChanged;
            }
        }

        NotifyOfPropertyChange(nameof(CanApply));
    }

    private void PlannedStageOnPropertyChanged(object? sender, System.ComponentModel.PropertyChangedEventArgs e)
    {
        if (e.PropertyName == nameof(PlannedStageItem.IsSelected))
        {
            NotifyOfPropertyChange(nameof(CanApply));
        }
    }

    /// <summary>
    /// A selectable stage in a validated model plan.
    /// </summary>
    public sealed class PlannedStageItem : PropertyChangedBase
    {
        private bool _isSelected = true;

        /// <summary>
        /// Initializes a new instance of the <see cref="PlannedStageItem"/> class.
        /// </summary>
        /// <param name="code">Exact MAA stage code.</param>
        /// <param name="display">Localized stage name.</param>
        /// <param name="tip">Optional stage tip.</param>
        public PlannedStageItem(string code, string display, string tip)
        {
            Code = code;
            Display = string.IsNullOrWhiteSpace(display) ? code : display;
            Tip = tip;
        }

        /// <summary>
        /// Gets the exact MAA stage code.
        /// </summary>
        public string Code { get; }

        /// <summary>
        /// Gets the localized stage name.
        /// </summary>
        public string Display { get; }

        /// <summary>
        /// Gets the optional stage tip.
        /// </summary>
        public string Tip { get; }

        /// <summary>
        /// Gets or sets a value indicating whether this stage is included when applying the plan.
        /// </summary>
        public bool IsSelected
        {
            get => _isSelected;
            set => SetAndNotify(ref _isSelected, value);
        }
    }
}
