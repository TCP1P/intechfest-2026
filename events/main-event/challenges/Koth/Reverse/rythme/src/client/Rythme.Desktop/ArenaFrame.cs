using System.Text;

namespace Rythme.Desktop;

internal static class ArenaFrame
{
    private const byte version = 1;

    public static byte[] Empty(byte action) => Build(action, _ => { });

    public static byte[] Ready(string ticket, byte[] payload) => Build(NativePolicyIds.FrameReady, writer =>
    {
        WriteText(writer, ticket);
        WriteBlob(writer, payload);
    });

    public static byte[] Cues(string ticket) => Build(NativePolicyIds.FrameCues, writer => WriteText(writer, ticket));

    public static byte[] Result(string ticket) => Build(NativePolicyIds.FrameResult, writer => WriteText(writer, ticket));

    public static byte[] Input(IReadOnlyList<InputFrame> frames)
    {
        if (frames.Count is < 1 or > 64)
            throw new ArenaProtocolException(0, "Input batch size is invalid.");
        InputFrame first = frames[0];
        return Build(NativePolicyIds.FrameInput, writer =>
        {
            WriteText(writer, first.HeatTicketId);
            writer.Write(Ruleset(first.Ruleset));
            writer.Write(checked((ushort)frames.Count));
            foreach (InputFrame frame in frames)
            {
                if (frame.HeatTicketId != first.HeatTicketId || frame.Ruleset != first.Ruleset)
                    throw new ArenaProtocolException(0, "Input batch binding is inconsistent.");
                writer.Write(frame.Sequence);
                writer.Write(frame.NoteIndex);
                WriteText(writer, frame.CueToken);
                writer.Write(frame.ActionBits);
                bool pointer = frame.PointerX.HasValue && frame.PointerY.HasValue;
                writer.Write(pointer ? (byte)1 : (byte)0);
                if (pointer)
                {
                    writer.Write(frame.PointerX!.Value);
                    writer.Write(frame.PointerY!.Value);
                }
            }
        });
    }

    public static EnrollResponse ReadEnroll(byte[] raw)
    {
        using BinaryReader reader = Open(raw, NativePolicyIds.FrameEnroll);
        var result = new EnrollResponse(ReadText(reader), ReadText(reader));
        RequireDone(reader);
        return result;
    }

    public static ReadyBinding ReadReady(byte[] raw)
    {
        using BinaryReader reader = Open(raw, NativePolicyIds.FrameReady);
        if (reader.ReadByte() != 1)
            throw new ArenaProtocolException(0, "The modifier lock was not accepted.");
        AppliedModProfile locked = ReadProfile(reader);
        _ = ReadBlob(reader, 8192);
        RequireDone(reader);
        return new ReadyBinding(new AppliedModSet(
            locked.Acronym,
            locked.RateMilli,
            locked.SettingsHash,
            locked.ScoreMultiplierMilli));
    }

    public static CueResponse ReadCues(byte[] raw)
    {
        using BinaryReader reader = Open(raw, NativePolicyIds.FrameCues);
        string heat = ReadText(reader);
        long now = reader.ReadInt64();
        int count = reader.ReadUInt16();
        if (count > 256)
            throw new ArenaProtocolException(0, "The cue batch is oversized.");
        var cues = new Cue[count];
        for (int index = 0; index < cues.Length; index++)
        {
            cues[index] = new Cue
            {
                NoteIndex = reader.ReadInt32(),
                OpensUnixMs = reader.ReadInt64(),
                ExpiresUnixMs = reader.ReadInt64(),
                Token = ReadText(reader)
            };
        }
        RequireDone(reader);
        return new CueResponse { HeatId = heat, ServerNowUnixMs = now, Cues = cues };
    }

    public static InputResponse ReadInput(byte[] raw)
    {
        using BinaryReader reader = Open(raw, NativePolicyIds.FrameInput);
        var result = new InputResponse(reader.ReadInt32(), ReadText(reader));
        RequireDone(reader);
        return result;
    }

    public static ResultResponse ReadResult(byte[] raw)
    {
        using BinaryReader reader = Open(raw, NativePolicyIds.FrameResult);
        int hits = reader.ReadInt32();
        int count = reader.ReadInt32();
        _ = reader.ReadInt64();
        _ = reader.ReadInt64();
        long rawBasis = reader.ReadInt64();
        double accuracy = reader.ReadDouble();
        int combo = reader.ReadInt32();
        string digest = ReadText(reader);
        _ = ReadBlob(reader, 8192);
        RequireDone(reader);
        return new ResultResponse
        {
            Judge = new JudgeResult
            {
                HitCount = hits,
                NoteCount = count,
                RawBasis = rawBasis,
                Accuracy = accuracy,
                MaxCombo = combo,
                InputDigest = digest
            }
        };
    }

    public static ManifestResponse ReadFinish(byte[] raw)
    {
        using BinaryReader reader = Open(raw, NativePolicyIds.FrameFinish);
        var settlement = new Settlement
        {
            TeamId = ReadText(reader),
            TeamName = ReadText(reader),
            Eligible = ReadBool(reader),
            Reason = EmptyToNull(ReadText(reader)),
            AppliedMods = reader.ReadByte() == 0 ? null : ReadProfile(reader),
            AttestedMods = reader.ReadByte() == 0 ? null : ReadClaim(reader),
            AppliedMultiplier = reader.ReadDouble(),
            AttestedMultiplier = reader.ReadDouble(),
            Multiplier = reader.ReadDouble(),
            SettledScore = reader.ReadInt64(),
            Judge = ReadJudge(reader),
            SeriesIndex = reader.ReadInt64(),
            SeriesLeg = reader.ReadInt32()
        };
        bool tookCrown = ReadBool(reader);
        RequireDone(reader);
        return new ManifestResponse { Settlement = settlement, TookCrown = tookCrown };
    }

    private static byte[] Build(byte action, Action<BinaryWriter> payload)
    {
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream, Encoding.UTF8, leaveOpen: true);
        writer.Write(NativePolicyIds.FrameMagic);
        writer.Write(version);
        writer.Write(action);
        payload(writer);
        writer.Flush();
        if (stream.Length > 64 * 1024)
            throw new ArenaProtocolException(0, "Arena frame is oversized.");
        return stream.ToArray();
    }

    private static BinaryReader Open(byte[] raw, byte expectedAction)
    {
        if (raw.Length is < 6 or > 64 * 1024 ||
            !raw.AsSpan(0, 4).SequenceEqual(NativePolicyIds.FrameMagic) ||
            raw[4] != version || raw[5] != expectedAction)
            throw new ArenaProtocolException(0, "Coordinator returned an invalid arena frame.");
        var stream = new MemoryStream(raw, writable: false);
        stream.Position = 6;
        return new BinaryReader(stream, Encoding.UTF8, leaveOpen: false);
    }

    private static void WriteText(BinaryWriter writer, string value) => WriteBlob(writer, Encoding.UTF8.GetBytes(value));

    private static void WriteBlob(BinaryWriter writer, ReadOnlySpan<byte> value)
    {
        if (value.Length > ushort.MaxValue)
            throw new ArenaProtocolException(0, "Arena field is oversized.");
        writer.Write((ushort)value.Length);
        writer.Write(value);
    }

    private static string ReadText(BinaryReader reader) => Encoding.UTF8.GetString(ReadBlob(reader, 8192));

    private static byte[] ReadBlob(BinaryReader reader, int maximum)
    {
        int length = reader.ReadUInt16();
        if (length > maximum)
            throw new ArenaProtocolException(0, "Coordinator field is oversized.");
        byte[] value = reader.ReadBytes(length);
        if (value.Length != length)
            throw new ArenaProtocolException(0, "Coordinator frame is truncated.");
        return value;
    }

    private static AppliedModProfile ReadProfile(BinaryReader reader) => new()
    {
        Acronym = ReadText(reader),
        RateMilli = reader.ReadInt32(),
        SettingsHash = ReadText(reader),
        ScoreMultiplierMilli = reader.ReadInt32()
    };

    private static ModClaim ReadClaim(BinaryReader reader) => new()
    {
        Acronym = ReadText(reader),
        RateMilli = reader.ReadInt32(),
        SettingsHash = ReadText(reader)
    };

    private static JudgeResult ReadJudge(BinaryReader reader)
    {
        int hits = reader.ReadInt32();
        int count = reader.ReadInt32();
        _ = reader.ReadInt64();
        _ = reader.ReadInt64();
        long rawBasis = reader.ReadInt64();
        double accuracy = reader.ReadDouble();
        int combo = reader.ReadInt32();
        string digest = ReadText(reader);
        return new JudgeResult
        {
            HitCount = hits,
            NoteCount = count,
            RawBasis = rawBasis,
            Accuracy = accuracy,
            MaxCombo = combo,
            InputDigest = digest
        };
    }

    private static bool ReadBool(BinaryReader reader) => reader.ReadByte() switch
    {
        0 => false,
        1 => true,
        _ => throw new ArenaProtocolException(0, "Coordinator returned an invalid boolean.")
    };

    private static byte Ruleset(string value) => value switch
    {
        "osu" => 0,
        "taiko" => 1,
        "fruits" => 2,
        "mania" => 3,
        _ => throw new ArenaProtocolException(0, "Unsupported ruleset.")
    };

    private static void RequireDone(BinaryReader reader)
    {
        if (reader.BaseStream.Position != reader.BaseStream.Length)
            throw new ArenaProtocolException(0, "Coordinator frame contains trailing data.");
    }

    private static string? EmptyToNull(string value) => value.Length == 0 ? null : value;
}
