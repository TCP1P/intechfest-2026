using System.Text.Json;
using System.Text.Json.Serialization;

namespace Rythme.Desktop;

[JsonSourceGenerationOptions(
    GenerationMode = JsonSourceGenerationMode.Default,
    PropertyNamingPolicy = JsonKnownNamingPolicy.CamelCase)]
[JsonSerializable(typeof(Pool))]
[JsonSerializable(typeof(Song))]
[JsonSerializable(typeof(Note))]
[JsonSerializable(typeof(ChartPayload))]
[JsonSerializable(typeof(SessionResponse))]
[JsonSerializable(typeof(ArenaView))]
[JsonSerializable(typeof(HeatView))]
[JsonSerializable(typeof(SeriesView))]
[JsonSerializable(typeof(SeriesStandingView))]
[JsonSerializable(typeof(PerformerView))]
[JsonSerializable(typeof(Selection))]
[JsonSerializable(typeof(LocalSessionRequest))]
[JsonSerializable(typeof(RctfSessionRequest))]
[JsonSerializable(typeof(JsonElement))]
internal partial class RythmeJsonContext : JsonSerializerContext;
