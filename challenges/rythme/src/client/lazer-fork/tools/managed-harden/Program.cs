using System.Buffers.Binary;
using System.IO.Compression;
using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;
using System.Security.Cryptography;
using System.Text;

const int signatureLength = 64;
const int protectedBodyCount = 4;
const int protectedBodyDigestLength = 32;
const int protectedBodyBlockLength = protectedBodyCount * protectedBodyDigestLength;
const int trailerLength = protectedBodyBlockLength + signatureLength + sizeof(long) + 16;
byte[] trailerMagic =
[
    (byte)'R', (byte)'Y', (byte)'T', (byte)'H',
    (byte)'M', (byte)'E', (byte)'-', (byte)'S',
    (byte)'I', (byte)'G', (byte)'-', (byte)'V',
    (byte)'1', 0, 0, 0,
];
byte[] bundleSignature =
[
    0x8b, 0x12, 0x02, 0xb9, 0x6a, 0x61, 0x20, 0x38,
    0x72, 0x7b, 0x93, 0x02, 0x14, 0xd7, 0xa0, 0x32,
    0x13, 0xf5, 0xb9, 0xe6, 0xef, 0xae, 0x33, 0x18,
    0xee, 0x3b, 0x2d, 0xce, 0x24, 0xb3, 0x6a, 0xae,
];
var symbolMap = new Dictionary<string, string>(StringComparer.Ordinal)
{
    ["ArenaApi"] = "W9k2Q7m4",
    ["ReadyAsync"] = "H4q8N2m6R1",
    ["SubmitCompletionAsync"] = "J7v2Q9m4N1x8K5r0D6p3T",
    ["SendSecureBytesAsync"] = "F8n2V7m4Q1x9K5r0D6p3",
    ["ManifestResponse"] = "M7q2V9n4R1x8K5p0",
    ["ManifestSent"] = "M8n2R7v4K1x9",
    ["AttestedMods"] = "A8q2V7m4N1x9",
    ["AttestedMultiplier"] = "A7m2Q9v4N1x8K5r0D6",
    ["BuildEnvelope"] = "B7q2V9m4N1x8K",
    ["AppliedModSet"] = "d4N8p2R6v0T7q",
    ["ReadyBinding"] = "R5n8Q2v4K1x7",
    ["ClientIntegrityGuard"] = "G4v8N2q7K1m5R9x3T6p0",
    ["ClientIntegrityException"] = "T6x2M8q4V9n1K7r3P5d0C8bA",
    ["ExecutableIntegrityVerifier"] = "B7n2Q9x4M1v8R5k0T6p3D9c2F7a",
    ["VerifyCurrentProcess"] = "V8m2R7q4K9x1P6n3D5c0",
    ["AssertUnchanged"] = "A4x8N2v7Q1m5R9k",
    ["ReadProtectedBodyDigests"] = "F8n2Q7v4M1x9K5r3D6p0C8b4",
    ["ProtectedMethod"] = "L8q2V7m4N1x9K5r",
    ["ExpectedBodyHash"] = "J7m2Q9v4N1x8P5r0",
    ["NativeTag"] = "Q7m2V9x4K",
    ["SealedRunState"] = "C8m2R7v4K9x1P6",
    ["RunBinding"] = "N4q8V2m6R1",
    ["ArenaPolicy"] = "D7x3K9v2M5q",
    ["RequiredMods"] = "N8p4R1x7V2m6",
    ["BuildModifierEnvelope"] = "B4q8V2n6R1x7K5p0D3m9Q",
    ["CreateCompletionFrame"] = "C7m2Q9v4N1x8K5r0D6p3T",
    ["NativeChannelRequest"] = "N4q8V2m6R1x7K5p0D3c9",
    ["NativePolicyIds"] = "N8p4R1x7V2m6K9q",
    ["SendArenaFrameAsync"] = "S8q2M7v4N1x9K5r0D6p",
    ["ArenaFrame"] = "K7m2Q9v4N1",
    ["ReadFinish"] = "R8n2V7m4K1",
    ["applyProcessMitigations"] = "h7Q2m9V4x1K8r5N0p6D3c7B",
    ["setMitigationPolicy"] = "q8N2v7M4x1K9r5P3d6C",
    ["ProcessMitigationFlags"] = "F7m2Q9v4N1x8K5r0D6p3C8",
    ["NativePolicyBridge"] = "Q7m2V9x4K1p8R5d0C6",
    ["InitializeNative"] = "q8V2n5B7m1C9x4K0",
    ["AssertNativeHealthy"] = "G4v8N2q7K1m5R9x3T6p",
    ["OpenChannel"] = "O8p2R7x4V1m",
    ["CreateRequest"] = "C7m2Q9v4N1x8K",
    ["VerifyResponse"] = "V8m2R7q4K9x1P6",
    ["VerifyHandshake"] = "V4q8N2m6R1x7K5p",
    ["EncryptState"] = "E7m2Q9v4N1x8",
    ["DecryptState"] = "D8n2V7m4K1x9",
    ["CloseChannel"] = "C8m2R7v4K9x1",
};

if (args.Length == 0)
    return usage();

try
{
    switch (args[0])
    {
        case "harden-source" when args.Length == 2:
            hardenSource(args[1]);
            break;

        case "verify-assembly" when args.Length == 2:
            verifyAssembly(args[1]);
            break;

        case "sign" when args.Length == 4:
            sign(args[1], args[2], args[3]);
            break;

        case "verify" when args.Length == 3:
            verifyWithPublicKey(args[1], args[2]);
            break;

        case "verify-bound-assembly" when args.Length == 4:
            verifyBoundAssembly(args[1], args[2], args[3]);
            break;

        case "tamper-test" when args.Length == 3:
            verifyRejectsTamper(args[1], args[2]);
            break;

        case "verify-bundle-entry" when args.Length == 4:
            verifyBundleEntry(args[1], args[2], args[3]);
            break;

        case "verify-resource" when args.Length == 4:
            verifyResource(args[1], args[2], args[3]);
            break;

        default:
            return usage();
    }

    return 0;
}
catch (Exception error)
{
    Console.Error.WriteLine($"managed-harden: {error.Message}");
    return 1;
}

int usage()
{
    Console.Error.WriteLine(
        "usage:\n" +
        "  managed-harden harden-source <osu.Desktop-source-directory>\n" +
        "  managed-harden verify-assembly <final-Rythme.dll>\n" +
        "  managed-harden sign <single-file-Rythme.exe> <private-key.pem> <final-Rythme.dll>\n" +
        "  managed-harden verify <single-file-Rythme.exe> <public-key.pem>\n" +
        "  managed-harden verify-bound-assembly <single-file-Rythme.exe> <public-key.pem> <final-Rythme.dll>\n" +
        "  managed-harden tamper-test <single-file-Rythme.exe> <public-key.pem>\n" +
        "  managed-harden verify-bundle-entry <single-file-Rythme.exe> <entry-name> <expected-file>\n" +
        "  managed-harden verify-resource <managed-assembly> <resource-name> <expected-file>");
    return 2;
}

void hardenSource(string sourceDirectory)
{
    if (!Directory.Exists(sourceDirectory))
        throw new DirectoryNotFoundException(sourceDirectory);

    var totals = symbolMap.Keys.ToDictionary(key => key, _ => 0, StringComparer.Ordinal);
    foreach (string path in Directory.EnumerateFiles(sourceDirectory, "*.cs", SearchOption.AllDirectories))
    {
        string separator = Path.DirectorySeparatorChar.ToString();
        if (path.Contains($"{separator}obj{separator}", StringComparison.Ordinal) ||
            path.Contains($"{separator}bin{separator}", StringComparison.Ordinal))
            continue;

        string originalText = File.ReadAllText(path);
        string hardenedText = originalText;
        foreach ((string original, string replacement) in symbolMap)
        {
            if (original.Length != replacement.Length)
                throw new InvalidDataException($"symbol map changes the length of {original}");

            int occurrences = countOccurrences(hardenedText, original);
            if (occurrences == 0)
                continue;

            totals[original] += occurrences;
            hardenedText = hardenedText.Replace(original, replacement, StringComparison.Ordinal);
        }

        if (!ReferenceEquals(originalText, hardenedText) &&
            !string.Equals(originalText, hardenedText, StringComparison.Ordinal))
            File.WriteAllText(path, hardenedText, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
    }

    foreach ((string original, string replacement) in symbolMap)
    {
        if (totals[original] == 0)
            throw new InvalidDataException($"source symbol {original} was not found");
        Console.WriteLine($"[*] {original} -> {replacement} ({totals[original]} source occurrence(s))");
    }
}

void verifyAssembly(string assemblyPath)
{
    byte[] assembly = File.ReadAllBytes(assemblyPath);
    try
    {
        foreach (string original in symbolMap.Keys)
        {
            if (assembly.AsSpan().IndexOf(Encoding.UTF8.GetBytes(original)) >= 0 ||
                assembly.AsSpan().IndexOf(Encoding.Unicode.GetBytes(original)) >= 0)
                throw new InvalidDataException($"compiled assembly still exposes protected symbol {original}");
        }
    }
    finally
    {
        CryptographicOperations.ZeroMemory(assembly);
    }

    string[] digests = protectedBodyHashes(assemblyPath);
    for (int i = 0; i < digests.Length; i++)
        Console.WriteLine($"[*] protected IL {(char)('A' + i)}={digests[i]}");
    Console.WriteLine($"[+] verified hardened managed assembly {assemblyPath}");
}

string[] protectedBodyHashes(string assemblyPath) =>
    [
        methodHash(
            assemblyPath,
            "Rythme.Desktop",
            symbolMap["NativePolicyBridge"],
            symbolMap["CreateCompletionFrame"]),
        methodHash(
            assemblyPath,
            "Rythme.Desktop",
            symbolMap["ArenaFrame"],
            symbolMap["ReadFinish"]),
        methodHash(
            assemblyPath,
            "Rythme.Desktop",
            "SecureChannel",
            symbolMap["CreateRequest"]),
        methodHash(
            assemblyPath,
            "Rythme.Desktop",
            "SecureChannel",
            symbolMap["VerifyResponse"]),
    ];

int countOccurrences(string haystack, string needle)
{
    int count = 0;
    int offset = 0;
    while (offset <= haystack.Length - needle.Length)
    {
        int found = haystack.IndexOf(needle, offset, StringComparison.Ordinal);
        if (found < 0)
            break;
        count++;
        offset = checked(found + needle.Length);
    }
    return count;
}

string methodHash(string assemblyPath, string targetNamespace, string targetType, string targetMethod)
{
    using FileStream stream = File.OpenRead(assemblyPath);
    using var pe = new PEReader(stream);
    MetadataReader metadata = pe.GetMetadataReader();
    var matches = new List<MethodDefinition>();

    foreach (TypeDefinitionHandle typeHandle in metadata.TypeDefinitions)
    {
        TypeDefinition type = metadata.GetTypeDefinition(typeHandle);
        if (metadata.GetString(type.Namespace) != targetNamespace ||
            metadata.GetString(type.Name) != targetType)
            continue;

        foreach (MethodDefinitionHandle methodHandle in type.GetMethods())
        {
            MethodDefinition method = metadata.GetMethodDefinition(methodHandle);
            if (metadata.GetString(method.Name) == targetMethod)
                matches.Add(method);
        }
    }

    if (matches.Count != 1)
    {
        throw new InvalidDataException(
            $"expected one {targetNamespace}.{targetType}.{targetMethod}, found {matches.Count}");
    }

    MethodDefinition selected = matches[0];
    if (selected.RelativeVirtualAddress == 0)
        throw new InvalidDataException($"{targetType}.{targetMethod} has no managed body");

    byte[] body = pe.GetMethodBody(selected.RelativeVirtualAddress).GetILBytes()
                  ?? throw new InvalidDataException($"{targetType}.{targetMethod} returned no IL bytes");
    byte[] digest = SHA256.HashData(body);
    try
    {
        return Convert.ToHexString(digest).ToLowerInvariant();
    }
    finally
    {
        CryptographicOperations.ZeroMemory(body);
        CryptographicOperations.ZeroMemory(digest);
    }
}

void sign(string executablePath, string privateKeyPath, string assemblyPath)
{
    using ECDsa signer = ECDsa.Create();
    signer.ImportFromPem(File.ReadAllText(privateKeyPath));

    using (FileStream source = File.Open(executablePath, FileMode.Open, FileAccess.Read, FileShare.Read))
    {
        if (hasTrailer(source))
            throw new InvalidDataException("the executable already has an integrity trailer");
    }

    long signedLength = new FileInfo(executablePath).Length;
    string[] bodyHashes = protectedBodyHashes(assemblyPath);
    byte[] bodyDigestBlock = new byte[protectedBodyBlockLength];
    for (int i = 0; i < bodyHashes.Length; i++)
    {
        byte[] methodDigest = Convert.FromHexString(bodyHashes[i]);
        if (methodDigest.Length != protectedBodyDigestLength)
            throw new CryptographicException($"unexpected protected method digest length {methodDigest.Length}");
        methodDigest.CopyTo(bodyDigestBlock, i * protectedBodyDigestLength);
        CryptographicOperations.ZeroMemory(methodDigest);
    }

    byte[] digest = hashPrefix(executablePath, signedLength, bodyDigestBlock);
    byte[] signature = signer.SignHash(
        digest,
        DSASignatureFormat.IeeeP1363FixedFieldConcatenation);
    CryptographicOperations.ZeroMemory(digest);
    if (signature.Length != signatureLength)
        throw new CryptographicException($"unexpected P-256 signature length {signature.Length}");

    byte[] lengthBytes = new byte[sizeof(long)];
    BinaryPrimitives.WriteInt64LittleEndian(lengthBytes, signedLength);
    using (FileStream target = File.Open(executablePath, FileMode.Append, FileAccess.Write, FileShare.None))
    {
        target.Write(bodyDigestBlock);
        target.Write(signature);
        target.Write(lengthBytes);
        target.Write(trailerMagic);
        target.Flush(flushToDisk: true);
    }

    byte[] publicKey = signer.ExportSubjectPublicKeyInfo();
    try
    {
        verify(executablePath, publicKey);
    }
    finally
    {
        CryptographicOperations.ZeroMemory(bodyDigestBlock);
        CryptographicOperations.ZeroMemory(signature);
        CryptographicOperations.ZeroMemory(publicKey);
    }

    Console.WriteLine($"[+] signed {executablePath} ({signedLength} protected bytes)");
}

void verifyWithPublicKey(string executablePath, string publicKeyPath)
{
    using ECDsa verifier = ECDsa.Create();
    verifier.ImportFromPem(File.ReadAllText(publicKeyPath));
    byte[] publicKey = verifier.ExportSubjectPublicKeyInfo();
    try
    {
        verify(executablePath, publicKey);
    }
    finally
    {
        CryptographicOperations.ZeroMemory(publicKey);
    }
}

void verifyBoundAssembly(string executablePath, string publicKeyPath, string assemblyPath)
{
    verifyWithPublicKey(executablePath, publicKeyPath);

    byte[] trailer = readTrailer(executablePath);
    try
    {
        string[] bodyHashes = protectedBodyHashes(assemblyPath);
        if (bodyHashes.Length != protectedBodyCount)
            throw new InvalidDataException("the protected assembly body count is invalid");

        for (int i = 0; i < bodyHashes.Length; i++)
        {
            byte[] actual = Convert.FromHexString(bodyHashes[i]);
            try
            {
                ReadOnlySpan<byte> signed = trailer.AsSpan(
                    i * protectedBodyDigestLength,
                    protectedBodyDigestLength);
                if (actual.Length != signed.Length ||
                    !CryptographicOperations.FixedTimeEquals(actual, signed))
                {
                    throw new CryptographicException(
                        $"protected IL digest {(char)('A' + i)} is not bound to the executable");
                }
            }
            finally
            {
                CryptographicOperations.ZeroMemory(actual);
            }
        }
    }
    finally
    {
        CryptographicOperations.ZeroMemory(trailer);
    }

    Console.WriteLine($"[+] executable is bound to {assemblyPath}");
}

void verifyRejectsTamper(string executablePath, string publicKeyPath)
{
    string copyPath = Path.Combine(
        Path.GetTempPath(),
        $"rythme-tamper-{Guid.NewGuid():N}.exe");
    File.Copy(executablePath, copyPath);

    try
    {
        using (FileStream copy = File.Open(copyPath, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
        {
            long protectedLength = copy.Length - trailerLength;
            if (protectedLength <= 4096)
                throw new InvalidDataException("the signed executable is too short for the tamper test");

            copy.Position = 4096;
            int original = copy.ReadByte();
            if (original < 0)
                throw new EndOfStreamException("the tamper-test byte is unavailable");
            copy.Position = 4096;
            copy.WriteByte((byte)(original ^ 1));
            copy.Flush(flushToDisk: true);
        }

        try
        {
            verifyWithPublicKey(copyPath, publicKeyPath);
        }
        catch (CryptographicException)
        {
            Console.WriteLine("[+] tampered executable was rejected");
            return;
        }

        throw new CryptographicException("tampered executable unexpectedly verified");
    }
    finally
    {
        File.Delete(copyPath);
    }
}

void verifyBundleEntry(string executablePath, string entryName, string expectedPath)
{
    const int appHostProbeLength = 16 * 1024 * 1024;

    using FileStream executable = File.OpenRead(executablePath);
    int probeLength = checked((int)Math.Min(executable.Length, appHostProbeLength));
    byte[] probe = new byte[probeLength];
    try
    {
        executable.ReadExactly(probe);
        int signatureOffset = probe.AsSpan().IndexOf(bundleSignature);
        if (signatureOffset < sizeof(long))
            throw new InvalidDataException("the .NET bundle signature is unavailable");

        long headerOffset = BinaryPrimitives.ReadInt64LittleEndian(
            probe.AsSpan(signatureOffset - sizeof(long), sizeof(long)));
        if (headerOffset < 0 || headerOffset >= executable.Length)
            throw new InvalidDataException("the .NET bundle header offset is invalid");

        executable.Position = headerOffset;
        using var reader = new BinaryReader(executable, Encoding.UTF8, leaveOpen: true);
        uint major = reader.ReadUInt32();
        uint minor = reader.ReadUInt32();
        int fileCount = reader.ReadInt32();
        if (fileCount <= 0 || fileCount > 100_000)
            throw new InvalidDataException("the .NET bundle file count is invalid");
        _ = reader.ReadString();
        if (major >= 2)
        {
            for (int i = 0; i < 4; i++)
                _ = reader.ReadInt64();
        }
        if (major >= 6)
            _ = reader.ReadInt64();

        (long Offset, long Size, long CompressedSize)? match = null;
        for (int i = 0; i < fileCount; i++)
        {
            long offset = reader.ReadInt64();
            long size = reader.ReadInt64();
            long compressedSize = reader.ReadInt64();
            _ = reader.ReadByte();
            string path = reader.ReadString();
            if (!string.Equals(path, entryName, StringComparison.Ordinal))
                continue;
            if (match is not null)
                throw new InvalidDataException($"bundle entry {entryName} appears more than once");
            match = (offset, size, compressedSize);
        }

        if (match is null)
            throw new InvalidDataException($"bundle entry {entryName} is missing");

        long expectedSize = new FileInfo(expectedPath).Length;
        long storedSize = match.Value.CompressedSize == 0
            ? match.Value.Size
            : match.Value.CompressedSize;
        if (match.Value.Offset < 0 || match.Value.Size != expectedSize ||
            match.Value.CompressedSize < 0 || storedSize < 0 ||
            storedSize > executable.Length ||
            match.Value.Offset > executable.Length - storedSize)
            throw new InvalidDataException($"bundle entry {entryName} has invalid bounds or size");

        byte[] expectedHash = SHA256.HashData(File.ReadAllBytes(expectedPath));
        byte[] bundledHash = match.Value.CompressedSize == 0
            ? hashRange(executable, match.Value.Offset, match.Value.Size)
            : hashDeflatedRange(
                executable,
                match.Value.Offset,
                match.Value.CompressedSize,
                match.Value.Size);
        try
        {
            if (!CryptographicOperations.FixedTimeEquals(expectedHash, bundledHash))
                throw new CryptographicException($"bundle entry {entryName} differs from {expectedPath}");
        }
        finally
        {
            CryptographicOperations.ZeroMemory(expectedHash);
            CryptographicOperations.ZeroMemory(bundledHash);
        }

        Console.WriteLine(
            $"[+] verified bundle {major}.{minor} entry {entryName} " +
            $"({expectedSize} bytes, {storedSize} stored)");
    }
    finally
    {
        CryptographicOperations.ZeroMemory(probe);
    }
}

void verifyResource(string assemblyPath, string resourceName, string expectedPath)
{
    using FileStream stream = File.OpenRead(assemblyPath);
    using var pe = new PEReader(stream);
    MetadataReader metadata = pe.GetMetadataReader();
    ManifestResource? match = null;
    foreach (ManifestResourceHandle handle in metadata.ManifestResources)
    {
        ManifestResource candidate = metadata.GetManifestResource(handle);
        if (!candidate.Implementation.IsNil ||
            !string.Equals(metadata.GetString(candidate.Name), resourceName, StringComparison.Ordinal))
            continue;
        if (match is not null)
            throw new InvalidDataException($"resource {resourceName} appears more than once");
        match = candidate;
    }

    if (match is null)
        throw new InvalidDataException($"resource {resourceName} is missing");
    CorHeader cor = pe.PEHeaders.CorHeader
                    ?? throw new InvalidDataException("managed resources header is missing");
    int resourceRva = cor.ResourcesDirectory.RelativeVirtualAddress;
    if (resourceRva <= 0 || match.Value.Offset > int.MaxValue)
        throw new InvalidDataException($"resource {resourceName} has invalid bounds");

    PEMemoryBlock block = pe.GetSectionData(checked(resourceRva + (int)match.Value.Offset));
    BlobReader reader = block.GetReader();
    if (reader.RemainingBytes < sizeof(uint))
        throw new InvalidDataException($"resource {resourceName} is truncated");
    uint length = reader.ReadUInt32();
    if (length > int.MaxValue || reader.RemainingBytes < length)
        throw new InvalidDataException($"resource {resourceName} has an invalid length");
    byte[] actual = reader.ReadBytes((int)length);
    byte[] expected = File.ReadAllBytes(expectedPath);
    try
    {
        if (!actual.AsSpan().SequenceEqual(expected))
            throw new InvalidDataException($"resource {resourceName} differs from {expectedPath}");
    }
    finally
    {
        CryptographicOperations.ZeroMemory(actual);
        CryptographicOperations.ZeroMemory(expected);
    }

    Console.WriteLine($"[+] verified resource {resourceName} ({length} bytes)");
}

byte[] hashRange(FileStream stream, long offset, long length)
{
    stream.Position = offset;
    using IncrementalHash hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
    byte[] buffer = new byte[1024 * 1024];
    try
    {
        long remaining = length;
        while (remaining > 0)
        {
            int requested = checked((int)Math.Min(buffer.Length, remaining));
            int read = stream.Read(buffer, 0, requested);
            if (read <= 0)
                throw new EndOfStreamException("the bundle entry ended before its declared size");
            hash.AppendData(buffer, 0, read);
            remaining -= read;
        }
        return hash.GetHashAndReset();
    }
    finally
    {
        CryptographicOperations.ZeroMemory(buffer);
    }
}

byte[] hashDeflatedRange(
    FileStream stream,
    long offset,
    long compressedLength,
    long expectedLength)
{
    stream.Position = offset;
    using var bounded = new BoundedReadStream(stream, compressedLength, leaveOpen: true);
    using var deflate = new DeflateStream(bounded, CompressionMode.Decompress, leaveOpen: true);
    using IncrementalHash hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
    byte[] buffer = new byte[1024 * 1024];
    long total = 0;
    try
    {
        while (true)
        {
            int read = deflate.Read(buffer, 0, buffer.Length);
            if (read == 0)
                break;
            if (total > expectedLength - read)
                throw new InvalidDataException("the compressed bundle entry exceeds its declared size");
            hash.AppendData(buffer, 0, read);
            total += read;
        }

        if (total != expectedLength)
        {
            throw new InvalidDataException(
                $"the compressed bundle entry expanded to {total} bytes, expected {expectedLength}");
        }

        return hash.GetHashAndReset();
    }
    finally
    {
        CryptographicOperations.ZeroMemory(buffer);
    }
}

void verify(string executablePath, byte[] publicKey)
{
    byte[] trailer = readTrailer(executablePath);

    try
    {
        int signatureOffset = protectedBodyBlockLength;
        int lengthOffset = signatureOffset + signatureLength;
        ReadOnlySpan<byte> magic = trailer.AsSpan(lengthOffset + sizeof(long));
        if (!CryptographicOperations.FixedTimeEquals(magic, trailerMagic))
            throw new InvalidDataException("the executable integrity trailer is missing");

        long signedLength = BinaryPrimitives.ReadInt64LittleEndian(
            trailer.AsSpan(lengthOffset, sizeof(long)));
        long fileLength = new FileInfo(executablePath).Length;
        if (signedLength <= 0 || signedLength != fileLength - trailerLength)
            throw new InvalidDataException("the executable integrity trailer has an invalid length");

        byte[] digest = hashPrefix(
            executablePath,
            signedLength,
            trailer.AsSpan(0, protectedBodyBlockLength));
        bool valid;
        try
        {
            using ECDsa verifier = ECDsa.Create();
            verifier.ImportSubjectPublicKeyInfo(publicKey, out int consumed);
            valid = consumed == publicKey.Length &&
                    verifier.VerifyHash(
                        digest,
                        trailer.AsSpan(signatureOffset, signatureLength),
                        DSASignatureFormat.IeeeP1363FixedFieldConcatenation);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(digest);
        }

        if (!valid)
            throw new CryptographicException("the executable integrity signature is invalid");

        Console.WriteLine($"[+] verified {executablePath}");
    }
    finally
    {
        CryptographicOperations.ZeroMemory(trailer);
    }
}

byte[] readTrailer(string executablePath)
{
    byte[] trailer = new byte[trailerLength];
    try
    {
        using FileStream stream = File.Open(
            executablePath,
            FileMode.Open,
            FileAccess.Read,
            FileShare.Read);
        if (stream.Length < trailerLength)
            throw new InvalidDataException("the executable is shorter than the integrity trailer");
        stream.Position = stream.Length - trailer.Length;
        stream.ReadExactly(trailer);
        return trailer;
    }
    catch
    {
        CryptographicOperations.ZeroMemory(trailer);
        throw;
    }
}

bool hasTrailer(FileStream stream)
{
    if (stream.Length < trailerLength)
        return false;
    byte[] magic = new byte[trailerMagic.Length];
    stream.Position = stream.Length - magic.Length;
    stream.ReadExactly(magic);
    bool present = magic.AsSpan().SequenceEqual(trailerMagic);
    CryptographicOperations.ZeroMemory(magic);
    return present;
}

byte[] hashPrefix(string path, long length, ReadOnlySpan<byte> additionalData)
{
    using FileStream stream = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read);
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
                throw new EndOfStreamException("the executable ended before the signed boundary");
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

sealed class BoundedReadStream : Stream
{
    private readonly Stream inner;
    private readonly bool leaveOpen;
    private long remaining;

    public BoundedReadStream(Stream inner, long length, bool leaveOpen)
    {
        ArgumentNullException.ThrowIfNull(inner);
        ArgumentOutOfRangeException.ThrowIfNegative(length);
        if (!inner.CanRead)
            throw new ArgumentException("the bounded stream must be readable", nameof(inner));

        this.inner = inner;
        this.leaveOpen = leaveOpen;
        remaining = length;
    }

    public override bool CanRead => true;
    public override bool CanSeek => false;
    public override bool CanWrite => false;
    public override long Length => throw new NotSupportedException();

    public override long Position
    {
        get => throw new NotSupportedException();
        set => throw new NotSupportedException();
    }

    public override int Read(byte[] buffer, int offset, int count)
    {
        ArgumentNullException.ThrowIfNull(buffer);
        ArgumentOutOfRangeException.ThrowIfNegative(offset);
        ArgumentOutOfRangeException.ThrowIfNegative(count);
        if (buffer.Length - offset < count)
            throw new ArgumentException("the read range exceeds the buffer", nameof(count));

        int requested = checked((int)Math.Min(count, remaining));
        if (requested == 0)
            return 0;
        int read = inner.Read(buffer, offset, requested);
        remaining -= read;
        return read;
    }

    public override int Read(Span<byte> buffer)
    {
        int requested = checked((int)Math.Min(buffer.Length, remaining));
        if (requested == 0)
            return 0;
        int read = inner.Read(buffer[..requested]);
        remaining -= read;
        return read;
    }

    public override void Flush()
    {
    }

    public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
    public override void SetLength(long value) => throw new NotSupportedException();
    public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();

    protected override void Dispose(bool disposing)
    {
        if (disposing && !leaveOpen)
            inner.Dispose();
        base.Dispose(disposing);
    }
}
