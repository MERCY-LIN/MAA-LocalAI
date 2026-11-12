// <copyright file="DeviceControlLease.cs" company="MaaAssistantArknights">
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
using System.Security.Cryptography;
using System.Text;
using System.Threading;

namespace MaaWpfGui.Services.LocalModels;

/// <summary>
/// Owns the named mutex shared with Python debug workers for one device.
/// </summary>
public sealed class DeviceControlLease : IDisposable
{
    private const string MutexNamePrefix = "MAA-LocalAI-Device-";
    private Mutex? _mutex;

    /// <summary>
    /// Gets the address currently owned by this lease.
    /// </summary>
    public string? Address { get; private set; }

    /// <summary>
    /// Gets a value indicating whether this lease currently owns the device.
    /// </summary>
    public bool IsAcquired => _mutex is not null;

    /// <summary>
    /// Returns the cross-process mutex name for an ADB address.
    /// </summary>
    /// <param name="address">ADB serial or connection address.</param>
    /// <returns>Stable mutex name.</returns>
    public static string GetMutexName(string address)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(address);
        byte[] digest = SHA256.HashData(Encoding.UTF8.GetBytes(address.Trim().ToLowerInvariant()));
        return MutexNamePrefix + Convert.ToHexString(digest)[..16];
    }

    /// <summary>
    /// Attempts to acquire exclusive control without waiting.
    /// </summary>
    /// <param name="address">ADB serial or connection address.</param>
    /// <returns><see langword="true"/> when ownership was acquired.</returns>
    public bool TryAcquire(string address)
    {
        if (IsAcquired)
        {
            return string.Equals(Address, address, StringComparison.OrdinalIgnoreCase);
        }

        var mutex = new Mutex(initiallyOwned: false, GetMutexName(address));
        bool acquired;
        try
        {
            acquired = mutex.WaitOne(0);
        }
        catch (AbandonedMutexException)
        {
            acquired = true;
        }

        if (!acquired)
        {
            mutex.Dispose();
            return false;
        }

        _mutex = mutex;
        Address = address;
        return true;
    }

    /// <summary>
    /// Releases the mutex from its owning thread.
    /// </summary>
    public void Release()
    {
        Mutex? mutex = _mutex;
        _mutex = null;
        Address = null;
        if (mutex is null)
        {
            return;
        }

        mutex.ReleaseMutex();
        mutex.Dispose();
    }

    /// <inheritdoc />
    public void Dispose()
    {
        Release();
    }
}
