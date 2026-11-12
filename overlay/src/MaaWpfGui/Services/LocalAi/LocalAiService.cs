// <copyright file="LocalAiService.cs" company="MaaAssistantArknights">
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
using System.Globalization;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using MaaWpfGui.Configuration.Global;
using MaaWpfGui.Models;
using Serilog;

namespace MaaWpfGui.Services.LocalModels;

/// <summary>
/// Calls an OpenAI-compatible local model and validates its combat plan.
/// </summary>
public sealed class LocalAiService
{
    private static readonly Regex StageCodeRegex = new(
        @"\b[A-Z]{1,4}-\d{1,2}(?:-[A-Z])?\b",
        RegexOptions.Compiled | RegexOptions.CultureInvariant | RegexOptions.IgnoreCase);

    private static readonly Regex MainStageRegex = new(
        @"^(?<chapter>\d{1,2})-(?<stage>\d{1,2})(?:-[A-Za-z]+)?$",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);

    private static readonly string[] ResourceStageCodes =
    [
        "CE-6",
        "LS-6",
        "CA-5",
        "AP-5",
        "SK-5",
        "PR-A-1",
        "PR-B-1",
        "PR-C-1",
        "PR-D-1",
        "Annihilation",
    ];

    private static readonly ILogger _logger = Log.ForContext<LocalAiService>();

    private static readonly HttpClient _httpClient = new() { Timeout = Timeout.InfiniteTimeSpan };

    private readonly LocalAi _settings;

    /// <summary>
    /// Initializes a new instance of the <see cref="LocalAiService"/> class.
    /// </summary>
    /// <param name="settings">Local model settings.</param>
    public LocalAiService(LocalAi settings)
    {
        _settings = settings;
    }

    /// <summary>
    /// Tests the configured model endpoint.
    /// </summary>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>A short model response.</returns>
    public async Task<string> TestConnectionAsync(CancellationToken cancellationToken = default)
    {
        string reply = await CompleteAsync(
            "You are a connection tester. Reply with exactly OK. /no_think",
            "Connection test.",
            maxTokens: 32,
            cancellationToken).ConfigureAwait(false);
        return reply.Trim();
    }

    /// <summary>
    /// Generates a stage plan from natural-language intent.
    /// </summary>
    /// <param name="goal">User goal and optional account context.</param>
    /// <param name="stages">Stages currently open and navigable by MAA.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>A validated plan.</returns>
    public async Task<LocalAiPlan> CreatePlanAsync(
        string goal,
        IReadOnlyCollection<StageInfo> stages,
        CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(goal))
        {
            throw new LocalAiException(LocalAiError.EmptyGoal);
        }

        Dictionary<string, StageInfo> stageByCode = stages
            .Where(stage => !string.IsNullOrWhiteSpace(stage.Value))
            .DistinctBy(stage => stage.Value, StringComparer.OrdinalIgnoreCase)
            .ToDictionary(stage => stage.Value, StringComparer.OrdinalIgnoreCase);
        if (stageByCode.Count == 0)
        {
            throw new LocalAiException(LocalAiError.NoStages);
        }

        string catalog = BuildCompactCatalog(goal, stageByCode);
        string systemPrompt =
            """
            You are the combat planner inside MAA (MaaAssistantArknights).
            Produce a conservative stage plan from the supplied catalog only.
            Return one JSON object without Markdown or extra text:
            {"summary":"short Chinese summary","reason":"short Chinese reasoning mentioning uncertainty","stages":["exact stage code"],"times":1}

            Rules:
            1. Every stage code must exactly match one catalog Value.
            2. Return 1 to 5 unique stages in execution order.
            3. times must be an integer from 1 to 99.
            4. Never invent inventory, cleared progress, drop rates, or account data.
            5. If the goal depends on unknown account progress, state that assumption in reason.
            6. Do not include clicks, coordinates, or any game action outside the stage plan.
            /no_think
            """;
        string userPrompt =
            $"""
            User goal:
            {goal.Trim()}

            Currently open MAA stage catalog:
            {catalog}
            """;

        string raw = await CompleteAsync(systemPrompt, userPrompt, maxTokens: 700, cancellationToken)
            .ConfigureAwait(false);
        return ParsePlan(raw, stageByCode.Keys);
    }

    internal static LocalAiPlan ParsePlan(string raw, IEnumerable<string> allowedStageCodes)
    {
        string json = ExtractJsonObject(raw);
        JsonDocument document;
        try
        {
            document = JsonDocument.Parse(json);
        }
        catch (JsonException)
        {
            throw new LocalAiException(LocalAiError.InvalidResponse, Truncate(raw, 600));
        }

        using (document)
        {
        JsonElement root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("stages", out JsonElement stagesElement)
            || stagesElement.ValueKind != JsonValueKind.Array)
        {
            throw new LocalAiException(LocalAiError.InvalidResponse);
        }

        HashSet<string> allowed = new(allowedStageCodes, StringComparer.OrdinalIgnoreCase);
        List<string> stages = [];
        foreach (JsonElement item in stagesElement.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.String)
            {
                continue;
            }

            string stage = item.GetString()?.Trim() ?? string.Empty;
            if (!allowed.Contains(stage))
            {
                throw new LocalAiException(LocalAiError.UnsafePlan);
            }

            if (!stages.Contains(stage, StringComparer.OrdinalIgnoreCase))
            {
                stages.Add(stage);
            }
        }

        if (stages.Count is < 1 or > 5)
        {
            throw new LocalAiException(LocalAiError.UnsafePlan);
        }

        int times = root.TryGetProperty("times", out JsonElement timesElement) && timesElement.TryGetInt32(out int parsedTimes)
            ? Math.Clamp(parsedTimes, 1, 99)
            : 1;
        string summary = ReadString(root, "summary", 160);
        string reason = ReadString(root, "reason", 320);
        return new LocalAiPlan(summary, reason, stages, times);
        }
    }

    private async Task<string> CompleteAsync(
        string systemPrompt,
        string userPrompt,
        int maxTokens,
        CancellationToken cancellationToken)
    {
        Uri endpoint = ResolveCompletionEndpoint(_settings.Endpoint, _settings.AllowRemoteEndpoint);
        if (string.IsNullOrWhiteSpace(_settings.Model))
        {
            throw new LocalAiException(LocalAiError.EmptyModel);
        }

        var payload = new {
            model = _settings.Model.Trim(),
            messages = new[] {
                new { role = "system", content = systemPrompt },
                new { role = "user", content = userPrompt },
            },
            temperature = 0.1,
            max_tokens = maxTokens,
            response_format = new { type = "json_object" },
            stream = false,

            // Ollama uses this extension to return final JSON instead of spending
            // the token budget entirely on a Qwen3 reasoning block.
            think = false,
        };
        using var request = new HttpRequestMessage(HttpMethod.Post, endpoint) {
            Content = new StringContent(JsonSerializer.Serialize(payload), Encoding.UTF8, "application/json"),
        };
        string apiKey = Environment.GetEnvironmentVariable("MAA_LOCAL_AI_API_KEY") ?? string.Empty;
        if (!string.IsNullOrWhiteSpace(apiKey))
        {
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
        }

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(Math.Clamp(_settings.TimeoutSeconds, 5, 600)));
        try
        {
            using HttpResponseMessage response = await _httpClient.SendAsync(
                request,
                HttpCompletionOption.ResponseContentRead,
                timeout.Token).ConfigureAwait(false);
            string body = await response.Content.ReadAsStringAsync(timeout.Token).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode)
            {
                _logger.Warning("Local AI endpoint returned HTTP {StatusCode}", (int)response.StatusCode);
                throw new LocalAiException(
                    LocalAiError.HttpError,
                    $"HTTP {(int)response.StatusCode}: {Truncate(body, 240)}");
            }

            using JsonDocument document = JsonDocument.Parse(body);
            if (!document.RootElement.TryGetProperty("choices", out JsonElement choices)
                || choices.ValueKind != JsonValueKind.Array
                || choices.GetArrayLength() == 0
                || !choices[0].TryGetProperty("message", out JsonElement message)
                || !message.TryGetProperty("content", out JsonElement content)
                || content.ValueKind != JsonValueKind.String)
            {
                throw new LocalAiException(LocalAiError.InvalidResponse);
            }

            return content.GetString() ?? string.Empty;
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            throw new LocalAiException(LocalAiError.Timeout);
        }
        catch (HttpRequestException exception)
        {
            _logger.Warning(exception, "Failed to call local AI endpoint");
            throw new LocalAiException(LocalAiError.Connection, exception.Message);
        }
        catch (JsonException exception)
        {
            _logger.Warning(exception, "Local AI endpoint returned invalid JSON");
            throw new LocalAiException(LocalAiError.InvalidResponse);
        }
    }

    internal static Uri ResolveCompletionEndpoint(string endpointText, bool allowRemote)
    {
        if (!Uri.TryCreate(endpointText.Trim(), UriKind.Absolute, out Uri? uri)
            || uri.Scheme is not ("http" or "https"))
        {
            throw new LocalAiException(LocalAiError.InvalidEndpoint);
        }

        if (!allowRemote && !uri.IsLoopback)
        {
            throw new LocalAiException(LocalAiError.RemoteBlocked);
        }

        string path = uri.AbsolutePath.TrimEnd('/');
        if (!path.EndsWith("/chat/completions", StringComparison.OrdinalIgnoreCase))
        {
            path += "/chat/completions";
        }

        UriBuilder builder = new(uri) {
            Path = path,
            Query = string.Empty,
            Fragment = string.Empty,
        };
        return builder.Uri;
    }

    private static string ExtractJsonObject(string raw)
    {
        int start = raw.IndexOf('{', StringComparison.Ordinal);
        if (start < 0)
        {
            throw new LocalAiException(LocalAiError.InvalidResponse);
        }

        int depth = 0;
        bool inString = false;
        bool escaped = false;
        for (int index = start; index < raw.Length; index++)
        {
            char current = raw[index];
            if (inString)
            {
                if (escaped)
                {
                    escaped = false;
                }
                else if (current == '\\')
                {
                    escaped = true;
                }
                else if (current == '"')
                {
                    inString = false;
                }
                continue;
            }

            if (current == '"')
            {
                inString = true;
            }
            else if (current == '{')
            {
                depth++;
            }
            else if (current == '}' && --depth == 0)
            {
                return raw[start..(index + 1)];
            }
        }

        throw new LocalAiException(LocalAiError.InvalidResponse, Truncate(raw, 600));
    }

    private static string ReadString(JsonElement root, string name, int maxLength)
    {
        return root.TryGetProperty(name, out JsonElement value) && value.ValueKind == JsonValueKind.String
            ? Truncate(value.GetString() ?? string.Empty, maxLength)
            : string.Empty;
    }

    private static string NormalizeCatalogText(string text)
    {
        string normalized = string.Join(' ', text.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries));
        return Truncate(normalized, 80);
    }

    private static string Truncate(string text, int maxLength)
    {
        string normalized = text.Trim();
        return normalized.Length <= maxLength ? normalized : normalized[..maxLength];
    }

    private static string BuildCompactCatalog(
        string goal,
        IReadOnlyDictionary<string, StageInfo> stageByCode)
    {
        List<StageInfo> selected = [];
        HashSet<string> seen = new(StringComparer.OrdinalIgnoreCase);

        void Add(StageInfo? stage)
        {
            if (stage is not null && !string.IsNullOrWhiteSpace(stage.Value) && seen.Add(stage.Value))
            {
                selected.Add(stage);
            }
        }

        foreach (Match match in StageCodeRegex.Matches(goal))
        {
            stageByCode.TryGetValue(match.Value, out StageInfo? mentioned);
            Add(mentioned);
        }

        foreach (string code in ResourceStageCodes)
        {
            stageByCode.TryGetValue(code, out StageInfo? resourceStage);
            Add(resourceStage);
        }

        foreach (StageInfo stage in stageByCode.Values.Where(
                     stage => stage.Activity is { BeingOpen: true }))
        {
            Add(stage);
        }

        var mainStages = stageByCode.Values
            .Select(stage => (Stage: stage, Match: MainStageRegex.Match(stage.Value)))
            .Where(item => item.Match.Success)
            .Select(item => (
                Info: item.Stage,
                Chapter: int.Parse(item.Match.Groups["chapter"].Value, CultureInfo.InvariantCulture),
                StageNumber: int.Parse(item.Match.Groups["stage"].Value, CultureInfo.InvariantCulture)))
            .ToList();
        int latestChapter = mainStages.Count == 0 ? 0 : mainStages.Max(item => item.Chapter);
        foreach (var item in mainStages
                     .Where(item => item.Chapter >= latestChapter - 2)
                     .OrderBy(item => item.Chapter)
                     .ThenBy(item => item.StageNumber))
        {
            Add(item.Info);
        }

        foreach (StageInfo stage in stageByCode.Values
                     .Where(stage => !MainStageRegex.IsMatch(stage.Value)
                         && !ResourceStageCodes.Contains(stage.Value, StringComparer.OrdinalIgnoreCase))
                     .TakeLast(40))
        {
            Add(stage);
        }

        return string.Join(
            '\n',
            selected.Take(120).Select(stage => {
                string tip = NormalizeCatalogText(stage.Tip);
                return string.IsNullOrEmpty(tip)
                    ? $"- {stage.Value} | {stage.Display}"
                    : $"- {stage.Value} | {stage.Display} | {Truncate(tip, 24)}";
            }));
    }
}
