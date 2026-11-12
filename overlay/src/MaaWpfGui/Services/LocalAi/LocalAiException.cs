// <copyright file="LocalAiException.cs" company="MaaAssistantArknights">
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

namespace MaaWpfGui.Services.LocalModels;

/// <summary>
/// A controlled exception for user-facing local AI failures.
/// </summary>
public sealed class LocalAiException : Exception
{
    /// <summary>
    /// Initializes a new instance of the <see cref="LocalAiException"/> class.
    /// </summary>
    /// <param name="error">Failure category.</param>
    /// <param name="detail">Optional non-sensitive detail.</param>
    public LocalAiException(LocalAiError error, string? detail = null)
        : base(detail ?? error.ToString())
    {
        Error = error;
        Detail = detail;
    }

    /// <summary>
    /// Gets the failure category.
    /// </summary>
    public LocalAiError Error { get; }

    /// <summary>
    /// Gets optional non-sensitive detail.
    /// </summary>
    public string? Detail { get; }
}
