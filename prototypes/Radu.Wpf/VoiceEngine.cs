using System.Diagnostics;
using System.IO;
using System.Text.Json;

namespace Radu.Wpf;

public sealed class VoiceEngine : IDisposable
{
    private readonly object _writeGate = new();
    private TaskCompletionSource _ready = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private Process? _process;
    private volatile bool _listening;
    private volatile bool _disposed;
    private string _lastError = "";

    public event Action<string>? StatusChanged;
    public event Action<string, string>? DecisionMade;
    public event Func<Task>? ActionStarting;
    public event Action<int>? CommandsLoaded;
    public event Action<float>? AudioLevelChanged;
    public event Action? EngineStopped;
    public bool IsListening => _listening;
    public bool IsRunning => _process is { HasExited: false };

    public async Task StartAsync()
    {
        if (IsRunning) return;
        _process?.Dispose();
        _process = null;
        _ready = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        _disposed = false;
        _lastError = "";
        _listening = true;
        var engineDirectory = FindEngineDirectory();
        var virtualEnvironmentPython = Path.Combine(engineDirectory, ".venv", "Scripts", "python.exe");
        var useVirtualEnvironment = File.Exists(virtualEnvironmentPython);
        var startInfo = new ProcessStartInfo
        {
            FileName = useVirtualEnvironment ? virtualEnvironmentPython : "py",
            WorkingDirectory = engineDirectory,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = System.Text.Encoding.UTF8,
            StandardErrorEncoding = System.Text.Encoding.UTF8
        };
        if (!useVirtualEnvironment)
            startInfo.ArgumentList.Add("-3");
        startInfo.ArgumentList.Add("-u");
        startInfo.ArgumentList.Add("-m");
        startInfo.ArgumentList.Add("assistant.main");
        startInfo.ArgumentList.Add("--engine-service");
        startInfo.Environment["PYTHONUTF8"] = "1";
        startInfo.Environment["PYTHONUNBUFFERED"] = "1";
        startInfo.Environment["RADU_ENGINE_SERVICE"] = "1";
        startInfo.Environment["RADU_COMMANDS_FILE"] = CommandCatalog.GetConfigPath();

        _process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
        _process.OutputDataReceived += (_, args) =>
        {
            if (args.Data is null) return;
            HandleServiceMessage(args.Data);
        };
        _process.ErrorDataReceived += (_, args) =>
        {
            if (!string.IsNullOrWhiteSpace(args.Data)) _lastError = args.Data;
        };
        _process.Exited += (_, _) =>
        {
            if (!_ready.Task.IsCompleted)
                _ready.TrySetException(new InvalidOperationException(
                    string.IsNullOrWhiteSpace(_lastError) ? "Python speech engine stopped before startup" : _lastError));
            if (!_disposed)
            {
                _listening = false;
                EngineStopped?.Invoke();
                StatusChanged?.Invoke("Голосовой движок остановился");
            }
        };

        if (!_process.Start()) throw new InvalidOperationException("Не удалось запустить Python speech engine");
        _process.BeginOutputReadLine();
        _process.BeginErrorReadLine();
        try
        {
            await _ready.Task.WaitAsync(TimeSpan.FromSeconds(90));
        }
        catch
        {
            Dispose();
            throw;
        }
    }

    internal static string FindEngineDirectory()
    {
        var configured = Environment.GetEnvironmentVariable("RADU_PYTHON_ENGINE_DIR");
        if (!string.IsNullOrWhiteSpace(configured)
            && File.Exists(Path.Combine(configured, "assistant", "main.py")))
            return configured;

        for (var directory = new DirectoryInfo(AppContext.BaseDirectory); directory is not null; directory = directory.Parent)
        {
            var candidate = directory.FullName;
            if (File.Exists(Path.Combine(candidate, "assistant", "main.py"))) return candidate;
        }
        throw new DirectoryNotFoundException("Не найдена папка исходного Python-движка RADU V3");
    }

    private void HandleServiceMessage(string line)
    {
        try
        {
            using var document = JsonDocument.Parse(line);
            var root = document.RootElement;
            var eventName = root.GetProperty("event").GetString();
            switch (eventName)
            {
                case "ready":
                    CommandsLoaded?.Invoke(root.GetProperty("commands").GetInt32());
                    _ready.TrySetResult();
                    StatusChanged?.Invoke("Микрофон активен");
                    break;
                case "fatal":
                    var message = root.TryGetProperty("message", out var fatalMessage)
                        ? fatalMessage.GetString() ?? "Python voice engine could not start"
                        : "Python voice engine could not start";
                    _ready.TrySetException(new InvalidOperationException(message));
                    break;
                case "microphone":
                    if (root.TryGetProperty("value", out var microphoneValue))
                        StatusChanged?.Invoke(microphoneValue.GetBoolean() ? "Микрофон активен" : "Микрофон неактивен");
                    break;
                case "listening":
                    if (root.TryGetProperty("value", out var listeningValue))
                    {
                        _listening = listeningValue.GetBoolean();
                        if (!_listening) AudioLevelChanged?.Invoke(0);
                    }
                    break;
                case "audio_level":
                    if (root.TryGetProperty("value", out var levelValue) && levelValue.TryGetSingle(out var level))
                        AudioLevelChanged?.Invoke(Math.Clamp(level, 0, 1));
                    break;
                case "error":
                    var errorMessage = root.TryGetProperty("message", out var suppliedMessage)
                        ? suppliedMessage.GetString()
                        : null;
                    StatusChanged?.Invoke(string.IsNullOrWhiteSpace(errorMessage)
                        ? "Ошибка микрофона. Проверь устройство и разрешения."
                        : errorMessage);
                    break;
                case "action_starting":
                    _ = AcknowledgeActionStartingAsync();
                    break;
                case "command_result":
                    HandleCommandResult(root);
                    break;
                case "config_reload":
                    if (root.TryGetProperty("value", out var count) && count.ValueKind == JsonValueKind.Number)
                        CommandsLoaded?.Invoke(count.GetInt32());
                    break;
            }
        }
        catch (Exception exception) when (exception is JsonException or InvalidOperationException or KeyNotFoundException)
        {
            _lastError = line;
        }
    }

    private async Task AcknowledgeActionStartingAsync()
    {
        try
        {
            if (ActionStarting is not null)
                await ActionStarting();
        }
        catch (Exception exception)
        {
            StatusChanged?.Invoke($"Ошибка отклика интерфейса · {exception.Message}");
        }
        finally
        {
            try
            {
                SendControl(new { action = "action_starting_ack" });
            }
            catch (Exception exception) when (exception is InvalidOperationException or IOException)
            {
            }
        }
    }

    private void HandleCommandResult(JsonElement root)
    {
        var accepted = root.GetProperty("accepted").GetBoolean();
        var transcript = root.GetProperty("transcript").GetString() ?? "";
        var phrase = root.TryGetProperty("phrase", out var phraseValue) ? phraseValue.GetString() ?? "" : "";
        var reason = root.TryGetProperty("reason", out var reasonValue) ? reasonValue.GetString() ?? "" : "";
        var elapsed = root.TryGetProperty("recognition_seconds", out var elapsedValue)
            && elapsedValue.ValueKind == JsonValueKind.Number
            ? elapsedValue.GetDouble()
            : (double?)null;
        var elapsedText = elapsed is { } seconds
            ? $" — {seconds.ToString("0.00", System.Globalization.CultureInfo.GetCultureInfo("ru-RU"))} с"
            : "";
        if (accepted)
        {
            DecisionMade?.Invoke(transcript, $"Выполняю {phrase}{elapsedText}");
            StatusChanged?.Invoke($"Выполнено · {phrase}{elapsedText}");
        }
        else
        {
            var message = reason switch
            {
                "action_execution_failed" => "Не удалось выполнить команду",
                "explicit_negation" => "Команда с отрицанием не запущена",
                "word_confidence_below_threshold_or_missing" => "Распознавание команды не прошло проверку",
                "empty_transcript" => "Не удалось распознать фразу",
                "transcript_not_an_exact_configured_phrase" => "Фраза не совпала с командой",
                _ => string.IsNullOrWhiteSpace(reason) ? "Фраза не совпала с командой" : reason
            };
            DecisionMade?.Invoke(transcript, $"{message}{elapsedText}");
            StatusChanged?.Invoke($"{message}{elapsedText}");
        }
    }

    public bool SetListening(bool enabled)
    {
        if (!SendControl(new { action = "listen", enabled }))
        {
            StatusChanged?.Invoke("Голосовой движок недоступен");
            return false;
        }
        _listening = enabled;
        StatusChanged?.Invoke(enabled ? "Микрофон активен" : "Прослушивание приостановлено");
        return true;
    }

    public void ReloadCommands()
    {
        if (!SendControl(new { action = "reload_commands" }))
            StatusChanged?.Invoke("Голосовой движок недоступен");
    }

    private bool SendControl(object message)
    {
        lock (_writeGate)
        {
            if (_process is not { HasExited: false }) return false;
            _process.StandardInput.WriteLine(JsonSerializer.Serialize(message));
            _process.StandardInput.Flush();
            return true;
        }
    }

    public void Dispose()
    {
        _disposed = true;
        var process = _process;
        if (process is null) return;

        try
        {
            if (!process.HasExited)
            {
                try
                {
                    SendControl(new { action = "stop" });
                }
                catch (Exception exception) when (exception is InvalidOperationException or IOException)
                {
                }

                if (!process.WaitForExit(2500))
                    process.Kill(entireProcessTree: true);
            }
        }
        catch (Exception exception) when (exception is InvalidOperationException or IOException or System.ComponentModel.Win32Exception)
        {
        }
        finally
        {
            _listening = false;
            process.Dispose();
            _process = null;
        }
    }

    public async Task StopAsync()
    {
        _disposed = true;
        var process = _process;
        if (process is null) return;

        try
        {
            if (!process.HasExited)
            {
                try
                {
                    SendControl(new { action = "stop" });
                }
                catch (Exception exception) when (exception is InvalidOperationException or IOException)
                {
                }

                try
                {
                    await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(30));
                }
                catch (TimeoutException)
                {
                    process.Kill(entireProcessTree: true);
                    await process.WaitForExitAsync();
                }
            }
        }
        catch (Exception exception) when (exception is InvalidOperationException or IOException or System.ComponentModel.Win32Exception)
        {
        }
        finally
        {
            _listening = false;
            process.Dispose();
            if (ReferenceEquals(_process, process))
                _process = null;
        }
    }
}
