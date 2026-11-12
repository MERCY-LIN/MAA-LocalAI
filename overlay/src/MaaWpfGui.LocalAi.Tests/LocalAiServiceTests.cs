// <copyright file="LocalAiServiceTests.cs" company="MaaAssistantArknights">
// Part of the MaaWpfGui.LocalAi.Tests project, maintained by the MaaAssistantArknights team (Maa Team)
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
using System.Linq;
using System.Threading;
using MaaWpfGui.Models;
using MaaWpfGui.Services.LocalModels;

namespace MaaWpfGui.LocalAi.Tests;

internal static class LocalAiServiceTests
{
    public static int Main()
    {
        var tests = new (string Name, Action Run)[]
        {
            ("Accept JSON wrapped in model text", AcceptJsonWrappedInModelText),
            ("Reject unknown stage code", RejectUnknownStageCode),
            ("Reject more than five stages", RejectMoreThanFiveStages),
            ("Clamp clear counts", ClampClearCounts),
            ("Reject empty stage list", RejectEmptyStageList),
            ("Append chat completions for loopback", AppendChatCompletionsForLoopback),
            ("Block remote endpoints by default", BlockRemoteByDefault),
            ("Allow explicit remote endpoint", AllowExplicitRemoteEndpoint),
            ("Create stable device mutex names", CreateStableDeviceMutexNames),
            ("Reject concurrent device ownership", RejectConcurrentDeviceOwnership),
        };

        int failures = 0;
        foreach (var test in tests)
        {
            try
            {
                test.Run();
                Console.WriteLine($"[PASS] {test.Name}");
            }
            catch (Exception exception)
            {
                failures++;
                Console.Error.WriteLine($"[FAIL] {test.Name}: {exception.Message}");
            }
        }

        Console.WriteLine($"{tests.Length - failures} passed, {failures} failed.");
        return failures == 0 ? 0 : 1;
    }

    private static void AcceptJsonWrappedInModelText()
    {
        const string Raw =
            """
            Here is the plan:
            {"summary":"刷取材料","reason":"按开放关卡给出保守计划。","stages":["1-7","CE-6","1-7"],"times":12}
            """;

        LocalAiPlan plan = LocalAiService.ParsePlan(Raw, ["1-7", "CE-6"]);

        Equal("刷取材料", plan.Summary);
        EqualSequence(["1-7", "CE-6"], plan.Stages);
        Equal(12, plan.Times);
    }

    private static void RejectUnknownStageCode()
    {
        const string Raw = """{"stages":["9-99"],"times":1}""";

        LocalAiException exception = Throws(
            LocalAiError.UnsafePlan,
            () => LocalAiService.ParsePlan(Raw, ["1-7"]));

        Equal(LocalAiError.UnsafePlan, exception.Error);
    }

    private static void RejectMoreThanFiveStages()
    {
        const string Raw = """{"stages":["1-1","1-2","1-3","1-4","1-5","1-6"],"times":1}""";

        LocalAiException exception = Throws(
            LocalAiError.UnsafePlan,
            () => LocalAiService.ParsePlan(Raw, ["1-1", "1-2", "1-3", "1-4", "1-5", "1-6"]));

        Equal(LocalAiError.UnsafePlan, exception.Error);
    }

    private static void ClampClearCounts()
    {
        Equal(1, LocalAiService.ParsePlan("""{"stages":["1-7"],"times":0}""", ["1-7"]).Times);
        Equal(27, LocalAiService.ParsePlan("""{"stages":["1-7"],"times":27}""", ["1-7"]).Times);
        Equal(99, LocalAiService.ParsePlan("""{"stages":["1-7"],"times":500}""", ["1-7"]).Times);
    }

    private static void RejectEmptyStageList()
    {
        const string Raw = """{"stages":[],"times":1}""";

        LocalAiException exception = Throws(
            LocalAiError.UnsafePlan,
            () => LocalAiService.ParsePlan(Raw, ["1-7"]));

        Equal(LocalAiError.UnsafePlan, exception.Error);
    }

    private static void AppendChatCompletionsForLoopback()
    {
        Uri endpoint = LocalAiService.ResolveCompletionEndpoint(
            "http://127.0.0.1:11434/v1",
            allowRemote: false);

        Equal("http://127.0.0.1:11434/v1/chat/completions", endpoint.AbsoluteUri);
    }

    private static void BlockRemoteByDefault()
    {
        LocalAiException exception = Throws(
            LocalAiError.RemoteBlocked,
            () => LocalAiService.ResolveCompletionEndpoint(
                "https://example.com/v1",
                allowRemote: false));

        Equal(LocalAiError.RemoteBlocked, exception.Error);
    }

    private static void AllowExplicitRemoteEndpoint()
    {
        Uri endpoint = LocalAiService.ResolveCompletionEndpoint(
            "https://example.com/v1/chat/completions",
            allowRemote: true);

        Equal("https://example.com/v1/chat/completions", endpoint.AbsoluteUri);
    }

    private static void CreateStableDeviceMutexNames()
    {
        string first = DeviceControlLease.GetMutexName("emulator-5554");
        string second = DeviceControlLease.GetMutexName("EMULATOR-5554");
        string other = DeviceControlLease.GetMutexName("127.0.0.1:16384");

        Equal(first, second);
        Equal("MAA-LocalAI-Device-04AB3FC382BFB33E", first);
        if (first == other)
        {
            throw new InvalidOperationException("Different devices must not share a mutex.");
        }
    }

    private static void RejectConcurrentDeviceOwnership()
    {
        string address = $"test-{Guid.NewGuid():N}";
        using DeviceControlLease first = new();
        if (!first.TryAcquire(address))
        {
            throw new InvalidOperationException("Initial device lease was not acquired.");
        }

        bool secondAcquired = true;
        Exception? workerError = null;
        Thread worker = new(() => {
            try
            {
                using DeviceControlLease second = new();
                secondAcquired = second.TryAcquire(address);
            }
            catch (Exception exception)
            {
                workerError = exception;
            }
        });
        worker.Start();
        worker.Join();

        if (workerError is not null)
        {
            throw workerError;
        }

        Equal(false, secondAcquired);
    }

    private static LocalAiException Throws(LocalAiError expected, Action action)
    {
        try
        {
            action();
        }
        catch (LocalAiException exception)
        {
            Equal(expected, exception.Error);
            return exception;
        }

        throw new InvalidOperationException($"Expected LocalAiException with {expected}.");
    }

    private static void Equal<T>(T expected, T actual)
    {
        if (EqualityComparer<T>.Default.Equals(expected, actual))
        {
            return;
        }

        throw new InvalidOperationException($"Expected '{expected}', got '{actual}'.");
    }

    private static void EqualSequence<T>(IEnumerable<T> expected, IEnumerable<T> actual)
    {
        if (expected.SequenceEqual(actual))
        {
            return;
        }

        throw new InvalidOperationException(
            $"Expected sequence '{string.Join(", ", expected)}', got '{string.Join(", ", actual)}'.");
    }
}
