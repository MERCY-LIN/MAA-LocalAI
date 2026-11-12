// <copyright file="LocalAiPlan.cs" company="MaaAssistantArknights">
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
using System.Collections.Generic;

namespace MaaWpfGui.Models;

/// <summary>
/// A validated local-model stage plan.
/// </summary>
/// <param name="Summary">Short plan summary.</param>
/// <param name="Reason">Model reasoning and stated uncertainty.</param>
/// <param name="Stages">Exact MAA stage codes in execution order.</param>
/// <param name="Times">Requested clear count.</param>
public sealed record LocalAiPlan(string Summary, string Reason, IReadOnlyList<string> Stages, int Times);
