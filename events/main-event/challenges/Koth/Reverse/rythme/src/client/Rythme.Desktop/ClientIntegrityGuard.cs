using System.Diagnostics;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

namespace Rythme.Desktop;

internal static class ClientIntegrityGuard
{
    private const uint pageExecuteReadWrite = 0x40;
    private const uint pageExecuteWriteCopy = 0x80;
    private const uint disableLegacyExtensionPoints = 0x1;
    private const uint restrictedImageLoadPolicy = 0x7;
    private const int errorInvalidParameter = 87;
    private const int errorCallNotImplemented = 120;
    private const int nativeSampleLength = 32;
    private const int sha256Length = 32;

    private static readonly object sync = new();
    private static byte[]? canary;
    private static byte[]? canaryKey;
    private static byte[]? canaryTag;
    private static byte[]? nativeCodeKey;
    private static ProtectedMethod[]? protectedMethods;
    private static int checks;

    public static void Initialize()
    {
        lock (sync)
        {
            if (canary != null)
                return;

            canary = RandomNumberGenerator.GetBytes(48);
            canaryKey = RandomNumberGenerator.GetBytes(32);
            canaryTag = HMACSHA256.HashData(canaryKey, canary);

#if RYTHME_SIGNED_RELEASE
            if (OperatingSystem.IsWindows())
                applyProcessMitigations();

            ExecutableIntegrityVerifier.VerifyCurrentProcess();
            if (OperatingSystem.IsWindows())
                NativePolicyBridge.InitializeNative();
            protectedMethods = createProtectedMethods();
            verifyManagedBodies(protectedMethods);

            if (OperatingSystem.IsWindows())
            {
                nativeCodeKey = RandomNumberGenerator.GetBytes(32);
                foreach (ProtectedMethod method in protectedMethods)
                    captureNativeCode(method);
            }
#endif
        }
        AssertHealthy();
    }

    public static void AssertHealthy()
    {
        if (canary == null || canaryKey == null || canaryTag == null)
            Initialize();

        byte[] current = HMACSHA256.HashData(canaryKey!, canary!);
        bool intact = CryptographicOperations.FixedTimeEquals(current, canaryTag!);
        CryptographicOperations.ZeroMemory(current);
        if (!intact)
            throw new ClientIntegrityException("The protected runtime canary changed.");

#if RYTHME_SIGNED_RELEASE
        ExecutableIntegrityVerifier.AssertUnchanged();
        if (OperatingSystem.IsWindows())
            NativePolicyBridge.AssertNativeHealthy();
#endif

        if (Debugger.IsAttached)
            throw new ClientIntegrityException("A managed debugger is attached.");

        rejectProfilerEnvironment();
        if (!OperatingSystem.IsWindows())
            return;

        if (IsDebuggerPresent())
            throw new ClientIntegrityException("A native debugger is attached.");
        if (CheckRemoteDebuggerPresent(Process.GetCurrentProcess().Handle, out bool remote) && remote)
            throw new ClientIntegrityException("A remote debugger is attached.");

        // Native page inspection is comparatively expensive. Input batches
        // still cross this checkpoint frequently, so sampling catches common
        // JIT detours without adding latency to every judgement.
        if ((Interlocked.Increment(ref checks) & 31) == 1)
        {
#if RYTHME_SIGNED_RELEASE
            ProtectedMethod[] methods = protectedMethods ??
                                          throw new ClientIntegrityException("Protected method state is unavailable.");
            verifyManagedBodies(methods);
            verifyNativeCode(methods);
#endif
            assertCodePageReadOnly(typeof(ArenaFrame), nameof(ArenaFrame.ReadFinish));
            assertCodePageReadOnly(typeof(NativePolicyBridge), nameof(NativePolicyBridge.CreateCompletionFrame));
            assertCodePageReadOnly(typeof(SecureChannel), nameof(SecureChannel.CreateRequest));
            assertCodePageReadOnly(typeof(SecureChannel), nameof(SecureChannel.VerifyResponse));
        }
    }

    private static void applyProcessMitigations()
    {
        setMitigationPolicy(
            ProcessMitigationPolicy.ExtensionPointDisable,
            disableLegacyExtensionPoints,
            "legacy extension-point DLL loading");
        setMitigationPolicy(
            ProcessMitigationPolicy.ImageLoad,
            restrictedImageLoadPolicy,
            "remote and low-integrity image loading");
    }

    private static void setMitigationPolicy(
        ProcessMitigationPolicy policy,
        uint flags,
        string description)
    {
        var state = new ProcessMitigationFlags { Flags = flags };
        if (SetProcessMitigationPolicy(
                policy,
                ref state,
                (nuint)Marshal.SizeOf<ProcessMitigationFlags>()))
            return;

        int error = Marshal.GetLastWin32Error();
        if (error is errorInvalidParameter or errorCallNotImplemented)
            return;

        throw new ClientIntegrityException(
            $"Windows could not enable {description} (error {error}).");
    }

    private static void rejectProfilerEnvironment()
    {
        string[] switches =
        [
            "COR_ENABLE_PROFILING",
            "CORECLR_ENABLE_PROFILING",
            "DOTNET_STARTUP_HOOKS",
            "COMPlus_AltJit",
            "COMPlus_AltJitName",
            "COR_PROFILER",
            "COR_PROFILER_PATH",
            "CORECLR_PROFILER",
            "CORECLR_PROFILER_PATH",
            "DOTNET_ADDITIONAL_DEPS",
        ];
        foreach (string name in switches)
        {
            string? value = Environment.GetEnvironmentVariable(name);
            if (!string.IsNullOrWhiteSpace(value) && value != "0")
                throw new ClientIntegrityException($"Runtime instrumentation switch {name} is active.");
        }
    }

    private static ProtectedMethod[] createProtectedMethods()
    {
        byte[][] expected = ExecutableIntegrityVerifier.ReadProtectedBodyDigests();
        if (expected.Length != 4 || expected.Any(digest => digest.Length != sha256Length))
            throw new ClientIntegrityException("Protected policy digests are malformed.");

        return
        [
            new ProtectedMethod(
                resolveMethod(typeof(NativePolicyBridge), nameof(NativePolicyBridge.CreateCompletionFrame)),
                expected[0]),
            new ProtectedMethod(
                resolveMethod(typeof(ArenaFrame), nameof(ArenaFrame.ReadFinish)),
                expected[1]),
            new ProtectedMethod(
                resolveMethod(typeof(SecureChannel), nameof(SecureChannel.CreateRequest)),
                expected[2]),
            new ProtectedMethod(
                resolveMethod(typeof(SecureChannel), nameof(SecureChannel.VerifyResponse)),
                expected[3]),
        ];
    }

    private static MethodInfo resolveMethod(Type type, string methodName)
    {
        MethodInfo[] matches = type
                               .GetMethods(BindingFlags.Public | BindingFlags.NonPublic |
                                           BindingFlags.Static | BindingFlags.Instance)
                               .Where(method => method.Name == methodName)
                               .ToArray();
        if (matches.Length != 1)
            throw new ClientIntegrityException("A protected policy method is missing or ambiguous.");
        return matches[0];
    }

    private static void verifyManagedBodies(IEnumerable<ProtectedMethod> methods)
    {
        foreach (ProtectedMethod method in methods)
        {
            byte[] body = method.Method.GetMethodBody()?.GetILAsByteArray()
                          ?? throw new ClientIntegrityException("A protected policy body is unavailable.");
            byte[] actual = SHA256.HashData(body);
            bool valid = method.ExpectedBodyHash.Length == actual.Length &&
                         CryptographicOperations.FixedTimeEquals(actual, method.ExpectedBodyHash);
            CryptographicOperations.ZeroMemory(body);
            CryptographicOperations.ZeroMemory(actual);
            if (!valid)
                throw new ClientIntegrityException("A protected policy body changed.");
        }
    }

    private static void captureNativeCode(ProtectedMethod method)
    {
        byte[] key = nativeCodeKey ??
                     throw new ClientIntegrityException("The native code canary key is unavailable.");
        RuntimeHelpers.PrepareMethod(method.Method.MethodHandle);
        method.Address = method.Method.MethodHandle.GetFunctionPointer();

        byte[] sample = new byte[nativeSampleLength];
        Marshal.Copy(method.Address, sample, 0, sample.Length);
        method.NativeTag = HMACSHA256.HashData(key, sample);
        CryptographicOperations.ZeroMemory(sample);
    }

    private static void verifyNativeCode(IEnumerable<ProtectedMethod> methods)
    {
        if (!OperatingSystem.IsWindows())
            return;

        byte[] key = nativeCodeKey ??
                     throw new ClientIntegrityException("The native code canary key is unavailable.");
        foreach (ProtectedMethod method in methods)
        {
            IntPtr currentAddress = method.Method.MethodHandle.GetFunctionPointer();
            if (method.Address == IntPtr.Zero || currentAddress != method.Address || method.NativeTag == null)
                throw new ClientIntegrityException("A protected policy entry point changed.");

            byte[] sample = new byte[nativeSampleLength];
            Marshal.Copy(currentAddress, sample, 0, sample.Length);
            byte[] currentTag = HMACSHA256.HashData(key, sample);
            bool valid = CryptographicOperations.FixedTimeEquals(currentTag, method.NativeTag);
            CryptographicOperations.ZeroMemory(sample);
            CryptographicOperations.ZeroMemory(currentTag);
            if (!valid)
                throw new ClientIntegrityException("A protected policy entry point changed.");
        }
    }

    private static void assertCodePageReadOnly(Type type, string methodName)
    {
        MethodInfo? method = type.GetMethod(
            methodName,
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance);
        if (method == null)
            throw new ClientIntegrityException("A protected policy method is missing.");

        RuntimeHelpers.PrepareMethod(method.MethodHandle);
        IntPtr address = method.MethodHandle.GetFunctionPointer();
        if (VirtualQuery(address, out MemoryBasicInformation info, (nuint)Marshal.SizeOf<MemoryBasicInformation>()) == 0)
            return; // Wine and restricted hosts may not expose this query.

        uint protection = info.Protect & 0xff;
        if (protection is pageExecuteReadWrite or pageExecuteWriteCopy)
            throw new ClientIntegrityException("A protected policy code page is writable.");
    }

    [DllImport("kernel32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool IsDebuggerPresent();

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CheckRemoteDebuggerPresent(IntPtr process, [MarshalAs(UnmanagedType.Bool)] out bool debuggerPresent);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern nuint VirtualQuery(IntPtr address, out MemoryBasicInformation buffer, nuint length);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetProcessMitigationPolicy(
        ProcessMitigationPolicy mitigationPolicy,
        ref ProcessMitigationFlags buffer,
        nuint length);

    private enum ProcessMitigationPolicy
    {
        ExtensionPointDisable = 6,
        ImageLoad = 10,
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ProcessMitigationFlags
    {
        public uint Flags;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MemoryBasicInformation
    {
        public IntPtr BaseAddress;
        public IntPtr AllocationBase;
        public uint AllocationProtect;
        public ushort PartitionId;
        public nuint RegionSize;
        public uint State;
        public uint Protect;
        public uint Type;
    }

    private sealed class ProtectedMethod
    {
        public MethodInfo Method { get; }

        public byte[] ExpectedBodyHash { get; }

        public IntPtr Address { get; set; }

        public byte[]? NativeTag { get; set; }

        public ProtectedMethod(MethodInfo method, byte[] expectedBodyHash)
        {
            Method = method;
            ExpectedBodyHash = expectedBodyHash;
        }
    }
}

internal sealed class ClientIntegrityException : Exception
{
    public ClientIntegrityException(string detail)
        : base($"Client integrity check failed: {detail}")
    {
    }
}
