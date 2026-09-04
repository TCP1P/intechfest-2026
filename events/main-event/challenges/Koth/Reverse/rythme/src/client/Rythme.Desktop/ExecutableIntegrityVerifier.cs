using System.Buffers.Binary;
using System.Diagnostics;
using System.Security.Cryptography;

namespace Rythme.Desktop;

internal static class ExecutableIntegrityVerifier
{
    private const int signatureLength = 64;
    private const int protectedBodyCount = 4;
    private const int protectedBodyDigestLength = 32;
    private const int protectedBodyBlockLength = protectedBodyCount * protectedBodyDigestLength;
    private const int trailerLength = protectedBodyBlockLength + signatureLength + sizeof(long) + 16;
    private const string publicKeySpki =
        "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEeU3vq8ylfTWRHy5yx/s5dgefXb/S" +
        "gKwdg9l4DtyvZh6nQLNuyJVX72Hiv8y5qBXEtMLTqKYfMOQFYb7afcxVnA==";

    private static readonly byte[] trailerMagic =
    [
        (byte)'R', (byte)'Y', (byte)'T', (byte)'H',
        (byte)'M', (byte)'E', (byte)'-', (byte)'S',
        (byte)'I', (byte)'G', (byte)'-', (byte)'V',
        (byte)'1', 0, 0, 0,
    ];

    private static readonly object sync = new();
    private static string? verifiedPath;
    private static long verifiedLength;
    private static long verifiedWriteTicks;
    private static byte[][]? verifiedBodyDigests;

    public static void VerifyCurrentProcess()
    {
#if !RYTHME_SIGNED_RELEASE
        return;
#else
        if (!OperatingSystem.IsWindows())
            return;

        lock (sync)
        {
            if (verifiedPath != null)
                return;

            string path = Environment.ProcessPath
                          ?? Process.GetCurrentProcess().MainModule?.FileName
                          ?? throw new ClientIntegrityException("The executable path is unavailable.");
            var info = new FileInfo(path);
            if (!info.Exists || info.Length < trailerLength)
                throw new ClientIntegrityException("The signed executable image is incomplete.");

            byte[] trailer = new byte[trailerLength];
            using (FileStream stream = File.Open(
                       path,
                       FileMode.Open,
                       FileAccess.Read,
                       FileShare.Read | FileShare.Delete))
            {
                stream.Position = stream.Length - trailer.Length;
                stream.ReadExactly(trailer);
            }

            int signatureOffset = protectedBodyBlockLength;
            int lengthOffset = signatureOffset + signatureLength;
            ReadOnlySpan<byte> magic = trailer.AsSpan(lengthOffset + sizeof(long));
            if (!CryptographicOperations.FixedTimeEquals(magic, trailerMagic))
            {
                CryptographicOperations.ZeroMemory(trailer);
                throw new ClientIntegrityException("The executable signature trailer is missing.");
            }

            long signedLength = BinaryPrimitives.ReadInt64LittleEndian(
                trailer.AsSpan(lengthOffset, sizeof(long)));
            if (signedLength <= 0 || signedLength != info.Length - trailerLength)
            {
                CryptographicOperations.ZeroMemory(trailer);
                throw new ClientIntegrityException("The executable signature boundary is invalid.");
            }

            byte[] digest = hashPrefix(
                path,
                signedLength,
                trailer.AsSpan(0, protectedBodyBlockLength));
            byte[] publicKey = Convert.FromBase64String(publicKeySpki);
            bool valid;
            using (ECDsa verifier = ECDsa.Create())
            {
                verifier.ImportSubjectPublicKeyInfo(publicKey, out int consumed);
                valid = consumed == publicKey.Length &&
                        verifier.VerifyHash(
                            digest,
                            trailer.AsSpan(signatureOffset, signatureLength),
                            DSASignatureFormat.IeeeP1363FixedFieldConcatenation);
            }

            CryptographicOperations.ZeroMemory(digest);
            CryptographicOperations.ZeroMemory(publicKey);
            if (!valid)
            {
                CryptographicOperations.ZeroMemory(trailer);
                throw new ClientIntegrityException("The executable signature did not verify.");
            }

            verifiedBodyDigests = new byte[protectedBodyCount][];
            for (int i = 0; i < protectedBodyCount; i++)
            {
                verifiedBodyDigests[i] = trailer
                                         .AsSpan(i * protectedBodyDigestLength, protectedBodyDigestLength)
                                         .ToArray();
            }
            CryptographicOperations.ZeroMemory(trailer);

            verifiedPath = path;
            verifiedLength = info.Length;
            verifiedWriteTicks = info.LastWriteTimeUtc.Ticks;
        }
#endif
    }

    public static byte[][] ReadProtectedBodyDigests()
    {
#if !RYTHME_SIGNED_RELEASE
        return [];
#else
        VerifyCurrentProcess();
        lock (sync)
        {
            byte[][] digests = verifiedBodyDigests ??
                               throw new ClientIntegrityException("Protected policy digests are unavailable.");
            return digests.Select(digest => digest.ToArray()).ToArray();
        }
#endif
    }

    public static void AssertUnchanged()
    {
#if RYTHME_SIGNED_RELEASE
        VerifyCurrentProcess();
        if (!OperatingSystem.IsWindows())
            return;

        string path = verifiedPath ??
                      throw new ClientIntegrityException("The executable image was not verified.");
        var info = new FileInfo(path);
        if (!info.Exists ||
            info.Length != verifiedLength ||
            info.LastWriteTimeUtc.Ticks != verifiedWriteTicks)
        {
            throw new ClientIntegrityException("The executable image changed after verification.");
        }
#endif
    }

    private static byte[] hashPrefix(string path, long length, ReadOnlySpan<byte> additionalData)
    {
        using FileStream stream = File.Open(
            path,
            FileMode.Open,
            FileAccess.Read,
            FileShare.Read | FileShare.Delete);
        using IncrementalHash hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        byte[] buffer = new byte[1024 * 1024];
        long remaining = length;

        try
        {
            while (remaining > 0)
            {
                int requested = (int)Math.Min(buffer.Length, remaining);
                int read = stream.Read(buffer, 0, requested);
                if (read == 0)
                    throw new ClientIntegrityException("The executable ended inside its signed region.");

                hash.AppendData(buffer, 0, read);
                remaining -= read;
            }

            hash.AppendData(additionalData);
            return hash.GetHashAndReset();
        }
        finally
        {
            CryptographicOperations.ZeroMemory(buffer);
        }
    }
}
