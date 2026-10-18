using System.IO;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Radu.Wpf;

public sealed class CommandCatalog
{
    private CommandCatalog(IReadOnlyList<VoiceCommand> commands)
    {
        Commands = commands;
        var phrases = new HashSet<string>(StringComparer.Ordinal);
        foreach (var command in commands)
            foreach (var phrase in command.Phrases)
            {
                var normalized = Normalize(phrase);
                if (!phrases.Add(normalized))
                    throw new InvalidDataException($"Повторяется фраза команды {phrase}");
            }
    }

    public IReadOnlyList<VoiceCommand> Commands { get; }

    public static CommandCatalog Load()
    {
        var userConfig = GetConfigPath();
        if (!File.Exists(userConfig))
        {
            var sourceConfig = FindEngineConfig();
            if (sourceConfig is not null)
                File.Copy(sourceConfig, userConfig);
            else
            {
                var bundled = Path.Combine(AppContext.BaseDirectory, "commands.default.json");
                File.Copy(bundled, userConfig);
            }
        }

        using var document = JsonDocument.Parse(File.ReadAllText(userConfig));
        var commands = document.RootElement.GetProperty("commands").Deserialize<VoiceCommand[]>() ?? [];
        return new CommandCatalog(commands);
    }

    public static string GetConfigPath()
    {
        var userRoot = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RADU-V3-WPF");
        Directory.CreateDirectory(userRoot);
        return Path.Combine(userRoot, "commands.json");
    }

    private static string? FindEngineConfig()
    {
        var configured = Environment.GetEnvironmentVariable("RADU_PYTHON_ENGINE_DIR");
        if (!string.IsNullOrWhiteSpace(configured))
        {
            var configuredFile = Path.Combine(configured, "commands.json");
            if (File.Exists(configuredFile)) return configuredFile;
        }

        for (var directory = new DirectoryInfo(AppContext.BaseDirectory); directory is not null; directory = directory.Parent)
        {
            var candidate = directory.FullName;
            if (File.Exists(Path.Combine(candidate, "assistant", "main.py")))
            {
                var commandsFile = Path.Combine(candidate, "commands.json");
                if (File.Exists(commandsFile)) return commandsFile;
            }
        }
        return null;
    }

    public static void Save(VoiceCommand command, string? replacingId = null)
    {
        if (!Regex.IsMatch(command.Id, @"^[a-z][a-z0-9_]*$"))
            throw new InvalidDataException("Идентификатор должен состоять из латинских букв, цифр и подчёркиваний");

        var phrases = command.Phrases.Select(Normalize).Where(phrase => phrase.Length > 0).Distinct(StringComparer.Ordinal).ToArray();
        if (phrases.Length == 0) throw new InvalidDataException("Добавь хотя бы одну фразу команды");
        if (command.Action.Type is not ("open_url" or "open_app") || string.IsNullOrWhiteSpace(command.Action.Value))
            throw new InvalidDataException("Укажи действие и его адрес или путь к приложению");
        if (command.Action.Type == "open_url"
            && (!Uri.TryCreate(command.Action.Value, UriKind.Absolute, out var uri) || uri.Scheme is not ("http" or "https")))
            throw new InvalidDataException("Для сайта укажи полный адрес, начинающийся с http или https");

        var path = GetConfigPath();
        using var document = JsonDocument.Parse(File.ReadAllText(path));
        var commands = document.RootElement.GetProperty("commands").Deserialize<List<VoiceCommand>>() ?? [];
        var existing = commands.FindIndex(item => item.Id == replacingId);
        if (replacingId is not null && existing < 0) throw new InvalidDataException("Команда уже удалена или не найдена");
        if (commands.Any(item => item.Id == command.Id && item.Id != replacingId))
            throw new InvalidDataException("Команда с таким идентификатором уже существует");

        var usedPhrases = commands.Where((_, index) => index != existing)
            .SelectMany(item => item.Phrases).Select(Normalize).ToHashSet(StringComparer.Ordinal);
        if (phrases.Any(usedPhrases.Contains)) throw new InvalidDataException("Эта фраза уже назначена другой команде");

        var saved = command with { Phrases = phrases };
        if (existing >= 0) commands[existing] = saved;
        else commands.Add(saved);
        var json = JsonSerializer.Serialize(new { commands }, new JsonSerializerOptions { WriteIndented = true });
        WriteAtomically(path, json + Environment.NewLine);
    }

    public static void Delete(string commandId)
    {
        if (string.IsNullOrWhiteSpace(commandId))
            throw new InvalidDataException("Не удалось определить команду для удаления");

        var path = GetConfigPath();
        using var document = JsonDocument.Parse(File.ReadAllText(path));
        var commands = document.RootElement.GetProperty("commands").Deserialize<List<VoiceCommand>>() ?? [];
        var index = commands.FindIndex(command => command.Id == commandId);
        if (index < 0)
            throw new InvalidDataException("Команда уже удалена или не найдена");

        commands.RemoveAt(index);
        var json = JsonSerializer.Serialize(new { commands }, new JsonSerializerOptions { WriteIndented = true });
        WriteAtomically(path, json + Environment.NewLine);
    }

    public static string Normalize(string text) => Regex.Replace((text ?? "").ToLowerInvariant().Replace('ё', 'е'), @"\s+", " ").Trim();

    private static void WriteAtomically(string path, string content)
    {
        var temporaryPath = $"{path}.{Guid.NewGuid():N}.tmp";
        try
        {
            File.WriteAllText(temporaryPath, content);
            File.Move(temporaryPath, path, overwrite: true);
        }
        finally
        {
            try
            {
                File.Delete(temporaryPath);
            }
            catch (IOException)
            {
            }
            catch (UnauthorizedAccessException)
            {
            }
        }
    }
}
