using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

namespace Rythme.Desktop;

internal static class StartupFailureReporter
{
    private const uint messageBoxOk = 0x00000000;
    private const uint messageBoxIconError = 0x00000010;
    private const uint messageBoxSetForeground = 0x00010000;

    private static int reported;

    public static void InstallGlobalHandler()
    {
        AppDomain.CurrentDomain.UnhandledException += (_, eventArgs) =>
        {
            Exception error = eventArgs.ExceptionObject as Exception
                              ?? new InvalidOperationException("The runtime stopped without an exception object.");
            Report(error);
        };
    }

    public static void Report(Exception error)
    {
        if (Interlocked.Exchange(ref reported, 1) != 0)
            return;

        string errorId = createErrorId(error);
        string? logPath = writeDiagnosticLog(error, errorId);
        string reason = playerFacingReason(error);
        string logNotice = logPath == null
            ? "A diagnostic log could not be created."
            : $"A diagnostic log was saved to:\n{logPath}";
        string message =
            $"Rythme could not start.\n\n{reason}\n\nError ID: {errorId}\n{logNotice}";

        if (OperatingSystem.IsWindows())
        {
            try
            {
                _ = MessageBoxW(
                    IntPtr.Zero,
                    message,
                    "Rythme startup failed",
                    messageBoxOk | messageBoxIconError | messageBoxSetForeground);
                return;
            }
            catch
            {
                // The log remains available even if the desktop cannot display a dialog.
            }
        }

        try
        {
            Console.Error.WriteLine(message);
        }
        catch
        {
            // Startup reporting must not replace the original failure.
        }
    }

    private static string playerFacingReason(Exception error)
    {
        string detail = error switch
        {
            ClientIntegrityException =>
                "The runtime integrity check failed. Close debugging, profiling, or process-injection tools and try again.",
            DllNotFoundException =>
                "A required native runtime component could not be loaded.",
            BadImageFormatException =>
                "A runtime component is missing, damaged, or built for the wrong architecture.",
            UnauthorizedAccessException =>
                "Rythme could not access its local storage.",
            _ => string.IsNullOrWhiteSpace(error.Message)
                ? "An unexpected startup error occurred."
                : $"Startup error: {singleLine(error.Message)}",
        };

        return detail.Length <= 420 ? detail : detail[..417] + "...";
    }

    private static string singleLine(string value) =>
        value.Replace('\r', ' ').Replace('\n', ' ').Trim();

    private static string createErrorId(Exception error)
    {
        byte[] input = Encoding.UTF8.GetBytes($"{error.GetType().FullName}\n{error.Message}");
        byte[] digest = SHA256.HashData(input);
        try
        {
            return Convert.ToHexString(digest.AsSpan(0, 6));
        }
        finally
        {
            CryptographicOperations.ZeroMemory(input);
            CryptographicOperations.ZeroMemory(digest);
        }
    }

    private static string? writeDiagnosticLog(Exception error, string errorId)
    {
        string report = string.Join(
            Environment.NewLine,
            "Rythme startup failure",
            $"UTC: {DateTimeOffset.UtcNow:O}",
            $"Error ID: {errorId}",
            $"OS: {RuntimeInformation.OSDescription}",
            $"Process architecture: {RuntimeInformation.ProcessArchitecture}",
            $"Framework: {RuntimeInformation.FrameworkDescription}",
            "",
            error.ToString(),
            "");

        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string temporary = Path.GetTempPath();
        foreach (string root in new[] { local, temporary }.Distinct(StringComparer.OrdinalIgnoreCase))
        {
            if (string.IsNullOrWhiteSpace(root))
                continue;

            try
            {
                string directory = Path.Combine(root, "Rythme", "Logs");
                Directory.CreateDirectory(directory);
                string filename =
                    $"startup-{DateTimeOffset.UtcNow:yyyyMMdd-HHmmss}-{Environment.ProcessId}.log";
                string path = Path.Combine(directory, filename);
                File.WriteAllText(path, report, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
                return path;
            }
            catch
            {
                // Try the next local, non-network fallback.
            }
        }

        return null;
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern int MessageBoxW(IntPtr window, string text, string caption, uint type);
}
