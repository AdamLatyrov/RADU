using System.Globalization;
using System.ComponentModel;
using System.Text.Json;
using System.IO;

namespace Radu.Wpf;

public sealed class SuccessfulExecution : INotifyPropertyChanged
{
    private bool _isPlaying;
    private bool _isMarkedIncorrect;

    public event PropertyChangedEventHandler? PropertyChanged;

    public required string Transcript { get; init; }
    public required string ObservationId { get; init; }
    public required string CommandId { get; init; }
    public required DateTimeOffset ExecutedAtUtc { get; init; }
    public string? AudioPath { get; init; }
    public required string HistoryFolderPath { get; init; }
    public bool IsPlaying
    {
        get => _isPlaying;
        set
        {
            if (_isPlaying == value)
                return;

            _isPlaying = value;
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(IsPlaying)));
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(PlayAutomationLabel)));
        }
    }

    public bool IsMarkedIncorrect
    {
        get => _isMarkedIncorrect;
        set
        {
            if (_isMarkedIncorrect == value)
                return;

            _isMarkedIncorrect = value;
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(IsMarkedIncorrect)));
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(MarkAutomationLabel)));
        }
    }

    public string LocalTimeLabel => ExecutedAtUtc.ToLocalTime().ToString("dd MMM, HH:mm", CultureInfo.CurrentCulture);
    public string PlayAutomationLabel => IsPlaying ? "Остановить воспроизведение" : "Прослушать запись";
    public string MarkAutomationLabel => IsMarkedIncorrect ? "Снять отметку о неверном распознавании" : "Отметить неверное распознавание";
}

public static class SuccessfulExecutionCatalog
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true
    };

    public static IReadOnlyList<SuccessfulExecution> Load()
    {
        var historyPath = GetHistoryFilePath();
        var dataDirectory = Path.GetDirectoryName(Path.GetDirectoryName(historyPath)!)!;
        if (!File.Exists(historyPath))
            return [];

        var markedIds = RecognitionReviewStore.LoadMarkedIds();
        var entries = new List<SuccessfulExecution>();
        foreach (var line in File.ReadLines(historyPath))
        {
            if (string.IsNullOrWhiteSpace(line))
                continue;

            try
            {
                using var document = JsonDocument.Parse(line);
                var root = document.RootElement;
                var transcript = ReadString(root, "transcript");
                var observationId = ReadString(root, "observation_id");
                var commandId = ReadString(root, "command_id");
                if (string.IsNullOrWhiteSpace(transcript)
                    || string.IsNullOrWhiteSpace(commandId)
                    || string.IsNullOrWhiteSpace(observationId))
                    continue;

                if (!DateTimeOffset.TryParse(
                        ReadString(root, "executed_at_utc"),
                        CultureInfo.InvariantCulture,
                        DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal,
                        out var executedAt))
                    executedAt = DateTimeOffset.MinValue;

                var audioPath = ResolveAudioPath(dataDirectory, ReadString(root, "observation_wav"));
                entries.Add(new SuccessfulExecution
                {
                    Transcript = transcript,
                    ObservationId = observationId,
                    CommandId = commandId,
                    ExecutedAtUtc = executedAt,
                    AudioPath = audioPath,
                    HistoryFolderPath = audioPath is not null
                        ? Path.GetDirectoryName(audioPath)!
                        : Path.Combine(dataDirectory, "command_history"),
                    IsMarkedIncorrect = markedIds.Contains(observationId)
                });
            }
            catch (JsonException)
            {
                // Ignore incomplete history lines and keep the remaining successful records available.
            }
        }

        return entries
            .OrderByDescending(entry => entry.ExecutedAtUtc)
            .ToArray();
    }

    public static string GetHistoryFilePath() => Path.Combine(
        VoiceEngine.FindEngineDirectory(), "data", "command_history", "executed_commands.jsonl");

    private static string ReadString(JsonElement element, string propertyName) =>
        element.TryGetProperty(propertyName, out var property)
            ? property.GetString() ?? string.Empty
            : string.Empty;

    private static string? ResolveAudioPath(string dataDirectory, string relativePath)
    {
        if (string.IsNullOrWhiteSpace(relativePath))
            return null;

        var dataRoot = Path.GetFullPath(dataDirectory) + Path.DirectorySeparatorChar;
        var fullPath = Path.GetFullPath(Path.Combine(dataDirectory, relativePath));
        return fullPath.StartsWith(dataRoot, StringComparison.OrdinalIgnoreCase)
            && File.Exists(fullPath)
                ? fullPath
                : null;
    }
}

public static class RecognitionReviewStore
{
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };

    private static string FilePath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "RADU-V3-WPF",
        "recognition-review.json");

    public static HashSet<string> LoadMarkedIds()
    {
        try
        {
            return File.Exists(FilePath)
                ? JsonSerializer.Deserialize<HashSet<string>>(File.ReadAllText(FilePath), JsonOptions) ?? []
                : [];
        }
        catch (IOException)
        {
            return [];
        }
        catch (UnauthorizedAccessException)
        {
            return [];
        }
        catch (JsonException)
        {
            return [];
        }
    }

    public static void SaveMarkedIds(IEnumerable<string> observationIds)
    {
        var path = FilePath;
        var directory = Path.GetDirectoryName(path)!;
        Directory.CreateDirectory(directory);
        var temporaryPath = path + ".tmp";

        try
        {
            var ids = observationIds.Distinct(StringComparer.Ordinal).OrderBy(id => id, StringComparer.Ordinal);
            File.WriteAllText(temporaryPath, JsonSerializer.Serialize(ids, JsonOptions));
            File.Move(temporaryPath, path, overwrite: true);
        }
        finally
        {
            if (File.Exists(temporaryPath))
            {
                try { File.Delete(temporaryPath); }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
            }
        }
    }
}
