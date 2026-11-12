// <copyright file="LocalAi.cs" company="MaaAssistantArknights">
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
using MaaWpfGui.Models;

namespace MaaWpfGui.Configuration.Global;

/// <summary>
/// Local AI planning settings shared by all MAA configurations.
/// </summary>
public class LocalAi : NotifyPropertyChangedWithValue
{
    /// <summary>
    /// Gets or sets the OpenAI-compatible API base URL.
    /// </summary>
    public string Endpoint { get; set; } = "http://127.0.0.1:11434/v1";

    /// <summary>
    /// Gets or sets the model name exposed by the local API.
    /// </summary>
    public string Model { get; set; } = "huihui_ai/qwen3-abliterated:14b-v2-q4_K_M";

    /// <summary>
    /// Gets or sets the request timeout in seconds.
    /// </summary>
    public int TimeoutSeconds { get; set; } = 90;

    /// <summary>
    /// Gets or sets a value indicating whether non-loopback endpoints are allowed.
    /// </summary>
    public bool AllowRemoteEndpoint { get; set; }
}
