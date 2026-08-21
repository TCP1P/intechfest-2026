using System.Buffers.Binary;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

namespace Rythme.Desktop;

internal static unsafe class NativePolicyBridge
{
    private const string libraryName = "rythme_native.dll";
    private const uint expectedAbi = 0x0002_0000;
    private const int digestLength = 32;
    private const int outputCapacity = 16 * 1024;
#if RYTHME_E2E_SMOKE
    private static readonly string expectedLibraryDigest = new('0', digestLength * 2);
#else
    private static readonly string expectedLibraryDigest = "RYTHME_NATIVE_DIGEST_PLACEHOLDER";
#endif

    private static readonly object sync = new();
    private static bool initialized;

    public static void InitializeNative()
    {
#if !RYTHME_E2E_SMOKE
        if (!OperatingSystem.IsWindows())
            return;
#endif

        lock (sync)
        {
            if (initialized)
                return;

            byte[] abi = invokeRaw(NativePolicyIds.Abi, 0, [], out _);
            try
            {
                if (abi.Length != sizeof(uint) || BinaryPrimitives.ReadUInt32LittleEndian(abi) != expectedAbi)
                    throw new ClientIntegrityException("The native policy ABI is incompatible.");
            }
            finally
            {
                CryptographicOperations.ZeroMemory(abi);
            }
            // The release build binds this value before the executable is sealed.
            if (expectedLibraryDigest.Length != digestLength * 2)
                throw new ClientIntegrityException("The native policy digest was not bound at build time.");

            byte[] digest = Convert.FromHexString(expectedLibraryDigest);
            try
            {
                _ = invokeRaw(NativePolicyIds.Initialize, 0, digest, out _);
            }
            finally
            {
                CryptographicOperations.ZeroMemory(digest);
            }

            initialized = true;
        }
    }

    public static void AssertNativeHealthy()
    {
#if !RYTHME_E2E_SMOKE
        if (!OperatingSystem.IsWindows())
            return;
#endif
        ensureInitialized();
        _ = invokeRaw(NativePolicyIds.Health, 0, [], out _);
    }

    public static ulong OpenChannel(
        string channelId,
        long expiresAtUnixMs,
        ReadOnlySpan<byte> requestKey,
        ReadOnlySpan<byte> responseKey)
    {
        ensureInitialized();
        if (requestKey.Length != digestLength || responseKey.Length != digestLength)
            throw new CryptographicException("Secure channel keys must contain 32 bytes.");

        byte[] requestKeyBytes = requestKey.ToArray();
        byte[] responseKeyBytes = responseKey.ToArray();
        byte[] input = encode(writer =>
        {
            writer.Write(expiresAtUnixMs);
            writeString(writer, channelId);
            writer.Write(requestKeyBytes);
            writer.Write(responseKeyBytes);
        });
        try
        {
            _ = invokeRaw(NativePolicyIds.OpenChannel, 0, input, out ulong handle);
            if (handle == 0)
                throw new ClientIntegrityException("The native channel handle is invalid.");
            return handle;
        }
        finally
        {
            CryptographicOperations.ZeroMemory(requestKeyBytes);
            CryptographicOperations.ZeroMemory(responseKeyBytes);
            CryptographicOperations.ZeroMemory(input);
        }
    }

    public static void VerifyHandshake(
        ulong handle,
        SessionResponse session,
        string clientPublicKey,
        string clientNonce)
    {
        ensureInitialized();
        byte[] proof = Convert.FromHexString(session.ServerProof);
        if (proof.Length != digestLength)
            throw new CryptographicException("Coordinator returned a malformed channel proof.");
        byte[] input = encode(writer =>
        {
            writeString(writer, session.TeamId);
            writeString(writer, session.AccessToken);
            writeString(writer, clientPublicKey);
            writeString(writer, session.ServerPublicKey);
            writeString(writer, clientNonce);
            writeString(writer, session.ServerNonce);
            writer.Write(session.ChannelExpiresAtUnixMs);
            writer.Write(proof);
        });
        try
        {
            _ = invokeRaw(NativePolicyIds.VerifyHandshake, handle, input, out _);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(proof);
            CryptographicOperations.ZeroMemory(input);
        }
    }

    public static NativeChannelRequest CreateRequest(
        ulong handle,
        string method,
        string target,
        long sentAtUnixMs,
        ReadOnlySpan<byte> body)
    {
        ensureInitialized();
        byte[] bodyBytes = body.ToArray();
        byte[] input = encode(writer =>
        {
            writer.Write(sentAtUnixMs);
            writeString(writer, method);
            writeString(writer, target);
            writeBytes32(writer, bodyBytes);
        });
        try
        {
            byte[] output = invokeRaw(NativePolicyIds.CreateRequest, handle, input, out _);
            if (output.Length != sizeof(ulong) + digestLength * 2)
                throw new ClientIntegrityException("The native channel request was malformed.");
            ulong sequence = BinaryPrimitives.ReadUInt64LittleEndian(output);
            byte[] previous = output.AsSpan(sizeof(ulong), digestLength).ToArray();
            byte[] signature = output.AsSpan(sizeof(ulong) + digestLength, digestLength).ToArray();
            CryptographicOperations.ZeroMemory(output);
            return new NativeChannelRequest(sequence, previous, signature);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(bodyBytes);
            CryptographicOperations.ZeroMemory(input);
        }
    }

    public static void VerifyResponse(
        ulong handle,
        ulong sequence,
        int statusCode,
        ReadOnlySpan<byte> body,
        ReadOnlySpan<byte> requestSignature,
        ReadOnlySpan<byte> responseSignature)
    {
        ensureInitialized();
        if (requestSignature.Length != digestLength || responseSignature.Length != digestLength)
            throw new CryptographicException("Coordinator response proof is malformed.");
        byte[] bodyBytes = body.ToArray();
        byte[] requestBytes = requestSignature.ToArray();
        byte[] responseBytes = responseSignature.ToArray();
        byte[] input = encode(writer =>
        {
            writer.Write(sequence);
            writer.Write(statusCode);
            writeBytes32(writer, bodyBytes);
            writer.Write(requestBytes);
            writer.Write(responseBytes);
        });
        try
        {
            _ = invokeRaw(NativePolicyIds.VerifyResponse, handle, input, out _);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(bodyBytes);
            CryptographicOperations.ZeroMemory(requestBytes);
            CryptographicOperations.ZeroMemory(responseBytes);
            CryptographicOperations.ZeroMemory(input);
        }
    }

    public static byte[] BuildModifierEnvelope(ulong handle, string ruleset, string acronym, string nonce)
    {
        ensureInitialized();
        byte[] input = encode(writer =>
        {
            writeString(writer, ruleset);
            writeString(writer, acronym);
            writeString(writer, nonce);
        });
        try
        {
            return invokeRaw(NativePolicyIds.BuildEnvelope, handle, input, out _);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(input);
        }
    }

    public static byte[] CreateCompletionFrame(ulong handle)
    {
        ensureInitialized();
        ExecutableIntegrityVerifier.AssertUnchanged();
        ClientIntegrityGuard.AssertHealthy();
        byte[] output = invokeRaw(NativePolicyIds.Finalize, handle, [], out _);
        if (output.Length < 6 ||
            !output.AsSpan(0, 4).SequenceEqual(NativePolicyIds.FrameMagic) ||
            output[5] != NativePolicyIds.FrameFinish)
        {
            CryptographicOperations.ZeroMemory(output);
            throw new ClientIntegrityException("The native completion frame is malformed.");
        }
        return output;
    }

    public static void CloseChannel(ulong handle)
    {
        if (!initialized || handle == 0)
            return;
        _ = invokeRaw(NativePolicyIds.Close, handle, [], out _);
    }

    private static byte[] invokeRaw(uint operation, ulong handle, ReadOnlySpan<byte> input, out ulong resultHandle)
    {
        byte[] output = new byte[outputCapacity];
        try
        {
            nuint outputLength = 0;
            ulong nativeHandle = 0;
            uint status;
            fixed (byte* inputPointer = input)
            fixed (byte* outputPointer = output)
            {
                status = gate(
                    operation,
                    handle,
                    inputPointer,
                    (nuint)input.Length,
                    outputPointer,
                    (nuint)output.Length,
                    &outputLength,
                    &nativeHandle);
            }
            resultHandle = nativeHandle;
            require(status);
            if (outputLength > (nuint)output.Length)
                throw new ClientIntegrityException("The native dispatcher returned an oversized result.");
            return output.AsSpan(0, checked((int)outputLength)).ToArray();
        }
        finally
        {
            CryptographicOperations.ZeroMemory(output);
        }
    }

    private static byte[] encode(Action<BinaryWriter> write)
    {
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream, Encoding.UTF8, leaveOpen: true);
        write(writer);
        writer.Flush();
        return stream.ToArray();
    }

    private static void writeString(BinaryWriter writer, string value)
    {
        byte[] raw = Encoding.UTF8.GetBytes(value);
        if (raw.Length > ushort.MaxValue)
            throw new ClientIntegrityException("Native policy field is oversized.");
        writer.Write((ushort)raw.Length);
        writer.Write(raw);
    }

    private static void writeBytes32(BinaryWriter writer, ReadOnlySpan<byte> value)
    {
        writer.Write(checked((uint)value.Length));
        writer.Write(value);
    }

    private static void ensureInitialized()
    {
        if (!initialized)
            InitializeNative();
        if (!initialized)
            throw new PlatformNotSupportedException("The native Rythme policy requires Windows.");
    }

    private static void require(uint status)
    {
        if (status == 0)
            return;
        string category = status switch
        {
            1 => "invalid native input",
            2 => "unsupported platform",
            3 => "native module unavailable",
            4 => "native image malformed",
            5 => "native debugger detected",
            6 => "writable native code",
            7 => "native code changed",
            8 => "native canary changed",
            9 => "native library changed",
            10 => "native policy not initialized",
            11 => "native channel unavailable",
            13 => "native policy request rejected",
            14 => "coordinator state rejected",
            _ => "native policy failure"
        };
        throw new ClientIntegrityException($"{category} (code {status}).");
    }

    [DllImport(libraryName, EntryPoint = "r4_gate", ExactSpelling = true, CallingConvention = CallingConvention.Cdecl)]
    private static extern uint gate(
        uint operation,
        ulong handle,
        byte* input,
        nuint inputLength,
        byte* output,
        nuint outputCapacity,
        nuint* outputLength,
        ulong* outputHandle);
}

internal sealed record NativeChannelRequest(ulong Sequence, byte[] Previous, byte[] Signature);
