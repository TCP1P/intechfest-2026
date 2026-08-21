using System.Globalization;
using System.Net;
using System.Security.Cryptography;
using System.Text;

namespace Rythme.Desktop;

internal sealed class ClientHandshake : IDisposable
{
    private readonly ECDiffieHellman keyAgreement;
    private readonly byte[] nonce;

    public string PublicKey { get; }

    public string Nonce => Convert.ToBase64String(nonce);

    private ClientHandshake(ECDiffieHellman keyAgreement, byte[] nonce)
    {
        this.keyAgreement = keyAgreement;
        this.nonce = nonce;
        PublicKey = Convert.ToBase64String(keyAgreement.ExportSubjectPublicKeyInfo());
    }

    public static ClientHandshake Create()
    {
        ECDiffieHellman keyAgreement = ECDiffieHellman.Create(ECCurve.NamedCurves.nistP256);
        return new ClientHandshake(keyAgreement, RandomNumberGenerator.GetBytes(32));
    }

    public SecureChannel Complete(SessionResponse session)
    {
        byte[] serverPublic = Convert.FromBase64String(session.ServerPublicKey);
        byte[] serverNonce = Convert.FromBase64String(session.ServerNonce);
        if (serverNonce.Length != 32)
            throw new CryptographicException("Coordinator returned an invalid channel nonce.");

        using ECDiffieHellman server = ECDiffieHellman.Create();
        server.ImportSubjectPublicKeyInfo(serverPublic, out int consumed);
        if (consumed != serverPublic.Length)
            throw new CryptographicException("Coordinator returned trailing channel key data.");

        byte[] shared = keyAgreement.DeriveRawSecretAgreement(server.PublicKey);
        byte[] saltInput = new byte[nonce.Length + serverNonce.Length];
        nonce.CopyTo(saltInput, 0);
        serverNonce.CopyTo(saltInput, nonce.Length);
        byte[] salt = SHA256.HashData(saltInput);
        CryptographicOperations.ZeroMemory(saltInput);

        string infoText = string.Join('\n',
            "rythme-channel-kdf-v3",
            session.TeamId,
            session.ChannelId,
            PublicKey,
            session.ServerPublicKey,
            "");
        byte[] material = CryptoKdf.HkdfSha256(shared, salt, Encoding.UTF8.GetBytes(infoText), 96);
        CryptographicOperations.ZeroMemory(shared);
        CryptographicOperations.ZeroMemory(salt);

        try
        {
            var channel = new SecureChannel(
                session.ChannelId,
                material.AsSpan(0, 32),
                material.AsSpan(32, 32),
                material.AsSpan(64, 32),
                session.ChannelExpiresAtUnixMs);
            try
            {
                channel.VerifyHandshake(session, PublicKey, Nonce);
                return channel;
            }
            catch
            {
                channel.Dispose();
                throw;
            }
        }
        finally
        {
            CryptographicOperations.ZeroMemory(material);
            CryptographicOperations.ZeroMemory(serverPublic);
            CryptographicOperations.ZeroMemory(serverNonce);
        }
    }

    public void Dispose()
    {
        CryptographicOperations.ZeroMemory(nonce);
        keyAgreement.Dispose();
    }
}

internal sealed class SecureChannel : IDisposable
{
    private readonly byte[] stateKey;
    private readonly ulong nativeHandle;
    private bool disposed;

    public string ChannelId { get; }

    public long ExpiresAtUnixMs { get; }

    public SecureChannel(
        string channelId,
        ReadOnlySpan<byte> requestKey,
        ReadOnlySpan<byte> responseKey,
        ReadOnlySpan<byte> stateKey,
        long expiresAtUnixMs)
    {
        if (requestKey.Length != 32 || responseKey.Length != 32 || stateKey.Length != 32)
            throw new CryptographicException("Secure channel keys must contain 32 bytes.");

        ChannelId = channelId;
        ExpiresAtUnixMs = expiresAtUnixMs;
        nativeHandle = NativePolicyBridge.OpenChannel(channelId, expiresAtUnixMs, requestKey, responseKey);
        this.stateKey = stateKey.ToArray();
    }

    internal void VerifyHandshake(SessionResponse session, string clientPublicKey, string clientNonce)
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        NativePolicyBridge.VerifyHandshake(nativeHandle, session, clientPublicKey, clientNonce);
    }

    public ChannelRequestProof CreateRequest(string method, string target, ReadOnlySpan<byte> body)
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        ClientIntegrityGuard.AssertHealthy();
        long sentAt = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        NativeChannelRequest native = NativePolicyBridge.CreateRequest(nativeHandle, method, target, sentAt, body);
        string previous = Convert.ToHexString(native.Previous).ToLowerInvariant();
        string signature = Convert.ToHexString(native.Signature).ToLowerInvariant();
        CryptographicOperations.ZeroMemory(native.Previous);
        return new ChannelRequestProof(
            ChannelId,
            native.Sequence,
            sentAt,
            previous,
            signature,
            native.Signature);
    }

    public void VerifyResponse(
        ChannelRequestProof request,
        HttpStatusCode status,
        ReadOnlySpan<byte> body,
        string? ack,
        string? signature)
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        if (ack != request.Sequence.ToString(CultureInfo.InvariantCulture) || string.IsNullOrWhiteSpace(signature))
            throw new CryptographicException("Coordinator response was not bound to the secure request.");

        byte[] provided;
        try
        {
            provided = Convert.FromHexString(signature);
        }
        catch (FormatException error)
        {
            throw new CryptographicException("Coordinator response proof is malformed.", error);
        }
        try
        {
            NativePolicyBridge.VerifyResponse(
                nativeHandle,
                request.Sequence,
                (int)status,
                body,
                request.SignatureBytes,
                provided);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(provided);
        }
    }

    internal void EncryptState(
        ReadOnlySpan<byte> nonce,
        ReadOnlySpan<byte> plaintext,
        Span<byte> ciphertext,
        Span<byte> tag,
        ReadOnlySpan<byte> associatedData)
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        ClientIntegrityGuard.AssertHealthy();
        using var cipher = new AesGcm(stateKey, tag.Length);
        cipher.Encrypt(nonce, plaintext, ciphertext, tag, associatedData);
    }

    internal void DecryptState(
        ReadOnlySpan<byte> nonce,
        ReadOnlySpan<byte> ciphertext,
        ReadOnlySpan<byte> tag,
        Span<byte> plaintext,
        ReadOnlySpan<byte> associatedData)
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        ClientIntegrityGuard.AssertHealthy();
        using var cipher = new AesGcm(stateKey, tag.Length);
        cipher.Decrypt(nonce, ciphertext, tag, plaintext, associatedData);
    }

    public byte[] BuildModifierEnvelope(string ruleset, string acronym, string nonce)
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        return NativePolicyBridge.BuildModifierEnvelope(nativeHandle, ruleset, acronym, nonce);
    }

    public byte[] CreateCompletionFrame()
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        return NativePolicyBridge.CreateCompletionFrame(nativeHandle);
    }

    public void Dispose()
    {
        if (disposed)
            return;
        disposed = true;
        try
        {
            NativePolicyBridge.CloseChannel(nativeHandle);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(stateKey);
        }
    }
}

internal sealed record ChannelRequestProof(
    string ChannelId,
    ulong Sequence,
    long SentAtUnixMs,
    string Previous,
    string Signature,
    byte[] SignatureBytes);

internal static class CryptoKdf
{
    public static byte[] HkdfSha256(
        ReadOnlySpan<byte> inputKeyMaterial,
        ReadOnlySpan<byte> salt,
        ReadOnlySpan<byte> info,
        int length)
    {
        byte[] actualSalt = salt.IsEmpty ? new byte[SHA256.HashSizeInBytes] : salt.ToArray();
        byte[] pseudorandomKey = HMACSHA256.HashData(actualSalt, inputKeyMaterial);
        byte[] output = new byte[length];
        byte[] previous = [];
        int written = 0;
        byte counter = 1;

        try
        {
            while (written < length)
            {
                byte[] blockInput = new byte[previous.Length + info.Length + 1];
                previous.CopyTo(blockInput, 0);
                info.CopyTo(blockInput.AsSpan(previous.Length));
                blockInput[^1] = counter++;

                byte[] block = HMACSHA256.HashData(pseudorandomKey, blockInput);
                CryptographicOperations.ZeroMemory(blockInput);
                if (previous.Length != 0)
                    CryptographicOperations.ZeroMemory(previous);
                previous = block;

                int count = Math.Min(block.Length, length - written);
                block.AsSpan(0, count).CopyTo(output.AsSpan(written));
                written += count;
            }
            return output;
        }
        finally
        {
            CryptographicOperations.ZeroMemory(actualSalt);
            CryptographicOperations.ZeroMemory(pseudorandomKey);
            if (previous.Length != 0)
                CryptographicOperations.ZeroMemory(previous);
        }
    }
}
