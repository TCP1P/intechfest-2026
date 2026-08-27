using System.Security.Cryptography;
using System.Text;

namespace Rythme.Desktop;

internal sealed class SealedRunState : IDisposable
{
    private const int nonceSize = 12;
    private const int tagSize = 16;
    private const int formatVersion = 4;

    private readonly byte[] nonce;
    private readonly byte[] ciphertext;
    private readonly byte[] tag;
    private bool disposed;

    private SealedRunState(byte[] nonce, byte[] ciphertext, byte[] tag)
    {
        this.nonce = nonce;
        this.ciphertext = ciphertext;
        this.tag = tag;
    }

    public static SealedRunState Create(
        SecureChannel channel,
        SessionResponse session,
        EnrollResponse enrollment,
        HeatView heat,
        Selection selection,
        ReadyBinding ready)
    {
        ClientIntegrityGuard.AssertHealthy();
        byte[] plaintext = serialize(new RunBinding(
            session.TeamId,
            enrollment.HeatId,
            enrollment.HeatTicketId,
            selection.SongId,
            selection.Ruleset,
            selection.ChartDigest,
            heat.StartAtUnixMs,
            ready.AppliedMods));
        byte[] nonce = RandomNumberGenerator.GetBytes(nonceSize);
        byte[] ciphertext = new byte[plaintext.Length];
        byte[] tag = new byte[tagSize];

        try
        {
            channel.EncryptState(nonce, plaintext, ciphertext, tag, associatedData(channel));
            return new SealedRunState(nonce, ciphertext, tag);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(plaintext);
        }
    }

    public RunBinding Open(SecureChannel channel)
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        ClientIntegrityGuard.AssertHealthy();
        byte[] plaintext = new byte[ciphertext.Length];
        try
        {
            channel.DecryptState(nonce, ciphertext, tag, plaintext, associatedData(channel));
            return deserialize(plaintext);
        }
        catch (CryptographicException error)
        {
            throw new ClientIntegrityException($"Protected run state authentication failed: {error.Message}");
        }
        finally
        {
            CryptographicOperations.ZeroMemory(plaintext);
        }
    }

    private static byte[] serialize(RunBinding binding)
    {
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream, Encoding.UTF8, leaveOpen: true);
        writer.Write(formatVersion);
        writer.Write(binding.TeamId);
        writer.Write(binding.HeatId);
        writer.Write(binding.HeatTicketId);
        writer.Write(binding.SongId);
        writer.Write(binding.Ruleset);
        writer.Write(binding.ChartDigest);
        writer.Write(binding.StartAtUnixMs);
        writer.Write(binding.AppliedMods.Acronym);
        writer.Write(binding.AppliedMods.RateMilli);
        writer.Write(binding.AppliedMods.SettingsHash);
        writer.Write(binding.AppliedMods.ScoreMultiplierMilli);
        writer.Flush();
        return stream.ToArray();
    }

    private static RunBinding deserialize(byte[] plaintext)
    {
        using var stream = new MemoryStream(plaintext, writable: false);
        using var reader = new BinaryReader(stream, Encoding.UTF8, leaveOpen: true);
        if (reader.ReadInt32() != formatVersion)
            throw new ClientIntegrityException("Protected run state has an unknown version.");

        var binding = new RunBinding(
            bounded(reader.ReadString()),
            bounded(reader.ReadString()),
            bounded(reader.ReadString()),
            bounded(reader.ReadString()),
            bounded(reader.ReadString()),
            bounded(reader.ReadString()),
            reader.ReadInt64(),
            new AppliedModSet(
                bounded(reader.ReadString()),
                reader.ReadInt32(),
                bounded(reader.ReadString()),
                reader.ReadInt32()));
        if (stream.Position != stream.Length)
            throw new ClientIntegrityException("Protected run state contains trailing data.");
        return binding;
    }

    private static string bounded(string value)
    {
        if (value.Length > 512)
            throw new ClientIntegrityException("Protected run state field is oversized.");
        return value;
    }

    private static byte[] associatedData(SecureChannel channel) =>
        Encoding.UTF8.GetBytes($"{ProtocolConstants.ClientBuild}\n{channel.ChannelId}\n");

    public void Dispose()
    {
        if (disposed)
            return;
        disposed = true;
        CryptographicOperations.ZeroMemory(nonce);
        CryptographicOperations.ZeroMemory(ciphertext);
        CryptographicOperations.ZeroMemory(tag);
    }
}

internal sealed record RunBinding(
    string TeamId,
    string HeatId,
    string HeatTicketId,
    string SongId,
    string Ruleset,
    string ChartDigest,
    long StartAtUnixMs,
    AppliedModSet AppliedMods);
