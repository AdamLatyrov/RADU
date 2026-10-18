using System.Text.Json.Serialization;
using System.IO;

namespace Radu.Wpf;

public sealed record VoiceAction(
    [property: JsonPropertyName("type")] string Type,
    [property: JsonPropertyName("value")] string Value,
    [property: JsonPropertyName("reuse_existing_window")] bool ReuseExistingWindow = false);

public sealed record VoiceCommand(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("phrases")] string[] Phrases,
    [property: JsonPropertyName("action")] VoiceAction Action,
    [property: JsonPropertyName("name")] string? Name = null)
{
    [JsonIgnore]
    public string DisplayName => string.IsNullOrWhiteSpace(Name) ? PrimaryPhrase : Name;

    [JsonIgnore]
    public string PrimaryPhrase => Phrases.FirstOrDefault() ?? Id;

    [JsonIgnore]
    public string ActionDescription => Action.Type == "open_url" ? Action.Value : $"Приложение · {Path.GetFileName(Action.Value)}";
}
