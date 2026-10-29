using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Shapes;
using System.Windows.Threading;
using Color = System.Windows.Media.Color;
using Rectangle = System.Windows.Shapes.Rectangle;

namespace Radu.Wpf;

public partial class MainWindow
{
    private static readonly double[] DotBaseOpacity = [0.156, 0.111, 0.066];
    private static readonly double[] DotRadii = [108, 136, 164];
    private static readonly double[] DotLags = [0.2, 0.12, 0.07];
    private static readonly double[] DotOffsets = [0, 15, 0];
    private static readonly double[] DotCosines = CreateDotCoordinates(cosine: true);
    private static readonly double[] DotSines = CreateDotCoordinates(cosine: false);
    private static readonly double[] DotSeeds = Enumerable.Range(0, 36).Select(VisualSeed).ToArray();
    private static readonly double[] WaveEnvelopes = Enumerable.Range(0, 41)
        .Select(index => 0.22 + 0.78 * Math.Exp(-Math.Pow((index - 20) / 20d, 2) / 0.42))
        .ToArray();
    private static readonly double[] WaveSeeds = Enumerable.Range(0, 41).Select(VisualSeed).ToArray();
    private readonly List<Rectangle> _waveformBars = [];
    private readonly List<Ellipse> _visualizerDots = [];
    private readonly DispatcherTimer _feedbackTimer = new() { Interval = TimeSpan.FromMilliseconds(1000) };
    private readonly DispatcherTimer _visualizerTimer = new() { Interval = TimeSpan.FromMilliseconds(33) };
    private readonly Stopwatch _visualizerClock = Stopwatch.StartNew();
    private volatile float _audioLevel;
    private DateTime _feedbackExpiresAtUtc = DateTime.MinValue;
    private double _visualLevel;
    private readonly double[] _dotLevels = [0, 0, 0];
    private readonly double[] _barHeights = Enumerable.Repeat(6d, 41).ToArray();
    private Ellipse? _glow;
    private RadialGradientBrush? _glowBrush;
    private SolidColorBrush? _accentBrush;
    private SolidColorBrush? _navigationAccentBrush;
    private Color _listeningColor = ColorsForListening;
    private string _visualState = "listening";
    private readonly SolidColorBrush _microphoneSurfaceBrush = new(Color.FromRgb(27, 32, 36));
    private readonly ScaleTransform _microphoneScale = new(1, 1);
    private static readonly Color ColorsForListening = Color.FromRgb(232, 163, 61);
    private static readonly Color ColorsForSuccess = Color.FromRgb(70, 220, 120);
    private static readonly Color ColorsForError = Color.FromRgb(255, 75, 75);

    private void BuildVoiceVisualizer()
    {
        const double cx = 260, cy = 250;
        _glowBrush = new RadialGradientBrush(new GradientStopCollection
        {
            new(Color.FromArgb(125, _listeningColor.R, _listeningColor.G, _listeningColor.B), 0),
            new(Color.FromArgb(36, _listeningColor.R, _listeningColor.G, _listeningColor.B), 0.5),
            new(Color.FromArgb(0, _listeningColor.R, _listeningColor.G, _listeningColor.B), 1)
        });
        _glow = new System.Windows.Shapes.Ellipse
        {
            Width = 380,
            Height = 380,
            IsHitTestVisible = false,
            Fill = _glowBrush
        };
        Canvas.SetLeft(_glow, cx - 190);
        Canvas.SetTop(_glow, cy - 190);
        VoiceCanvas.Children.Insert(0, _glow);

        foreach (var j in Enumerable.Range(0, 3))
            for (var i = 0; i < 12; i++)
            {
                var dot = new System.Windows.Shapes.Ellipse { Width = 4.8, Height = 4.8, Fill = _accentBrush, IsHitTestVisible = false };
                var dotIndex = j * 12 + i;
                Canvas.SetLeft(dot, cx + DotRadii[j] * DotCosines[dotIndex] - 2.4);
                Canvas.SetTop(dot, cy + DotRadii[j] * DotSines[dotIndex] - 2.4);
                VoiceCanvas.Children.Insert(2, dot);
                _visualizerDots.Add(dot);
            }

        const int count = 41;
        const double pitch = 8.5, barWidth = 5, baseline = 478;
        var firstX = cx - ((count - 1) * pitch) / 2;
        for (var i = 0; i < count; i++)
        {
            var bar = new System.Windows.Shapes.Rectangle
            {
                Width = barWidth,
                Height = 6,
                RadiusX = 2.5,
                RadiusY = 2.5,
                Fill = _accentBrush,
                Opacity = 0.88,
                IsHitTestVisible = false
            };
            Canvas.SetLeft(bar, firstX + i * pitch - barWidth / 2);
            Canvas.SetTop(bar, baseline - 3);
            VoiceCanvas.Children.Add(bar);
            _waveformBars.Add(bar);
        }
    }

    private void SetVisualState(string state)
    {
        var listening = _engine.IsListening;
        ListenButton.ToolTip = listening ? "Выключить прослушивание" : "Включить прослушивание";
        System.Windows.Automation.AutomationProperties.SetName(
            ListenButton,
            listening ? "Выключить прослушивание" : "Включить прослушивание");
        System.Windows.Automation.AutomationProperties.SetItemStatus(
            ListenButton,
            state switch
            {
                "accepted" => "Команда выполнена",
                "rejected" => "Ошибка распознавания или запуска команды",
                "standby" => "Прослушивание выключено",
                _ => "Микрофон активен"
            });
        MicrophoneIcon.Data = state == "standby"
            ? Geometry.Parse("M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z M19 10v2a7 7 0 0 1-12.1 4.9 M5 10v2c0 1.6.5 3.1 1.4 4.3 M12 19v3 M8 22h8 M2 2l20 20")
            : Geometry.Parse("M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z M19 10v2a7 7 0 0 1-14 0v-2 M12 19v3 M8 22h8");
        _visualState = state;
        var color = state switch
        {
            "accepted" => ColorsForSuccess,
            "rejected" => ColorsForError,
            _ => _listeningColor
        };
        if (_accentBrush is null) return;
        _accentBrush.BeginAnimation(SolidColorBrush.ColorProperty, new ColorAnimation(color, TimeSpan.FromMilliseconds(260))
        {
            EasingFunction = new CubicEase { EasingMode = EasingMode.EaseInOut }
        });
        ListenButton.BorderBrush = _accentBrush;
        MicrophoneIcon.Stroke = _accentBrush;
        if (_glowBrush is not null)
        {
            var alphas = new byte[] { 125, 36, 0 };
            for (var i = 0; i < _glowBrush.GradientStops.Count; i++)
                _glowBrush.GradientStops[i].BeginAnimation(GradientStop.ColorProperty,
                    new ColorAnimation(System.Windows.Media.Color.FromArgb(alphas[i], color.R, color.G, color.B), TimeSpan.FromMilliseconds(260))
                    {
                        EasingFunction = new CubicEase { EasingMode = EasingMode.EaseInOut }
                    });
        }
    }

    private void DrawVoiceVisualizer()
    {
        const double cx = 260, cy = 250, waveY = 478;
        var t = _visualizerClock.Elapsed.TotalSeconds;
        var targetLevel = _engine.IsListening ? Math.Max(0, _audioLevel - 0.012f) * 2.6 : 0;
        _visualLevel += (targetLevel - _visualLevel) * 0.18;
        if (targetLevel < 0.001 && _visualLevel < 0.001
            && Math.Abs(_microphoneScale.ScaleX - 1) < 0.001
            && Math.Abs(_microphoneScale.ScaleY - 1) < 0.001
            && _dotLevels.All(level => level < 0.01)
            && _barHeights.All(height => Math.Abs(height - 6) < 0.05))
            return;

        var pulse = 1 + 0.055 * Math.Min(1, _visualLevel);
        _microphoneScale.ScaleX += (pulse - _microphoneScale.ScaleX) * 0.18;
        _microphoneScale.ScaleY += (pulse - _microphoneScale.ScaleY) * 0.18;
        for (var j = 0; j < 3; j++)
        {
            _dotLevels[j] += (_visualLevel - _dotLevels[j]) * DotLags[j];
            var lv = Math.Min(1, _dotLevels[j] * 1.2);
            var breathe = 1 + 0.05 * lv * Math.Sin(t * 2.2 - j * 0.9);
            for (var i = 0; i < 12; i++)
            {
                var m = Math.Min(i, 12 - i);
                var noise = 0.5 + 0.5 * Math.Sin(t * (1.6 + m * 0.37) + m * 1.9 + j);
                var seed = DotSeeds[m + j * 3];
                var radius = (2.4 + 6.2 * lv * (0.4 + 0.6 * noise * seed)) * breathe;
                var dot = _visualizerDots[j * 12 + i];
                dot.Width = dot.Height = radius * 2;
                var dotIndex = j * 12 + i;
                Canvas.SetLeft(dot, cx + DotRadii[j] * DotCosines[dotIndex] - radius);
                Canvas.SetTop(dot, cy + DotRadii[j] * DotSines[dotIndex] - radius);
                dot.Opacity = Math.Min(1, 0.22 + DotBaseOpacity[j] + 0.55 * lv * (0.5 + 0.5 * noise));
            }
        }
        if (_glow is not null) _glow.Opacity = Math.Min(1, 0.2 + _dotLevels[0] * 0.3);
        for (var i = 0; i < _waveformBars.Count; i++)
        {
            var noise = 0.5 + 0.5 * Math.Sin(t * (2.1 + i * 0.29) + i * 1.7);
            var value = _visualLevel * WaveEnvelopes[i] * WaveSeeds[i] * (0.5 + 0.5 * noise);
            var targetHeight = 6 + 58 * Math.Min(1, value * 3.5);
            _barHeights[i] += (targetHeight - _barHeights[i]) * 0.2;
            _waveformBars[i].Height = _barHeights[i];
            Canvas.SetTop(_waveformBars[i], waveY - _barHeights[i] / 2);
        }
    }

    private static double VisualSeed(int index) => 0.62 + 0.38 * Math.Abs(Math.Sin(index * 12.9898) * Math.Cos(index * 4.1414));

    private static double[] CreateDotCoordinates(bool cosine)
    {
        var coordinates = new double[36];
        for (var ring = 0; ring < 3; ring++)
            for (var dot = 0; dot < 12; dot++)
            {
                var angle = (dot * 30 + DotOffsets[ring] - 90) * Math.PI / 180;
                coordinates[ring * 12 + dot] = cosine ? Math.Cos(angle) : Math.Sin(angle);
            }
        return coordinates;
    }


    private void UpdateStatus(string status)
    {
        Dispatcher.BeginInvoke(() =>
        {
            if (status.StartsWith("Выполнено · ", StringComparison.Ordinal))
            {
                VoiceStatus.Text = $"Команда выполнена · {status[12..]}";
                VoiceStatus.Foreground = new SolidColorBrush(ColorsForSuccess);
                return;
            }

            VoiceStatus.Foreground = status.StartsWith("Ошибка", StringComparison.Ordinal)
                || status.StartsWith("Не удалось", StringComparison.Ordinal)
                || status.StartsWith("Фраза не совпала", StringComparison.Ordinal)
                || status.StartsWith("Распознавание команды", StringComparison.Ordinal)
                || status.StartsWith("Команда с отрицанием", StringComparison.Ordinal)
                ? new SolidColorBrush(ColorsForError)
                : (System.Windows.Media.Brush)FindResource("MutedBrush");
            VoiceStatus.Text = status switch
            {
                "Микрофон активен" => "Готов к командам",
                "Прослушивание приостановлено" => "Прослушивание выключено",
                "Голосовой движок недоступен" => "Движок остановлен. Нажмите микрофон, чтобы запустить снова.",
                _ => status
            };

            if (status.StartsWith("Ошибка", StringComparison.Ordinal)
                || status.StartsWith("Не удалось", StringComparison.Ordinal)
                || status.StartsWith("Голосовой движок остановился", StringComparison.Ordinal))
                ShowDecisionFeedback("rejected");
        });
    }

    private async Task ShowActionStartingFeedback()
    {
        if (Dispatcher.HasShutdownStarted || Dispatcher.HasShutdownFinished) return;

        try
        {
            await Dispatcher.InvokeAsync(() => ShowDecisionFeedback("accepted"), DispatcherPriority.Send)
                .Task.ConfigureAwait(false);
            await Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ContextIdle)
                .Task.ConfigureAwait(false);
        }
        catch (InvalidOperationException) when (Dispatcher.HasShutdownStarted || Dispatcher.HasShutdownFinished)
        {
            // Closing the window must not prevent the recognized action from running.
        }
    }

    private void ShowDecisionFeedback(string state)
    {
        _feedbackTimer.Stop();
        _feedbackExpiresAtUtc = DateTime.UtcNow.Add(_feedbackTimer.Interval);
        SetVisualState(state);
        _feedbackTimer.Start();
    }

    private void UpdateDecision(string transcript, string detail)
    {
        Dispatcher.BeginInvoke(() =>
        {
            var matched = detail.StartsWith("Выполняю ", StringComparison.Ordinal);
            VoiceStatus.Foreground = matched
                ? new SolidColorBrush(ColorsForSuccess)
                : LooksLikeCommandAttempt(transcript)
                    ? new SolidColorBrush(ColorsForError)
                    : (System.Windows.Media.Brush)FindResource("MutedBrush");
            VoiceStatus.Text = matched
                ? $"Команда распознана{detail["Выполняю ".Length..]}"
                : detail;
            if (matched) return;

            if (LooksLikeCommandAttempt(transcript))
            {
                ShowDecisionFeedback("rejected");
            }
            else if (DateTime.UtcNow >= _feedbackExpiresAtUtc)
            {
                SetVisualState(_engine.IsListening ? "listening" : "standby");
            }
        });
    }

    private bool LooksLikeCommandAttempt(string transcript)
    {
        var normalized = CommandCatalog.Normalize(transcript);
        if (normalized.Length == 0) return false;
        if (normalized.StartsWith("открой ", StringComparison.Ordinal)
            || normalized.StartsWith("не открой ", StringComparison.Ordinal)) return true;

        return _commands.Any(command => command.Phrases.Any(phrase =>
        {
            var commandFirstWord = CommandCatalog.Normalize(phrase).Split(' ', StringSplitOptions.RemoveEmptyEntries).FirstOrDefault();
            return commandFirstWord is not null && (normalized == commandFirstWord
                || normalized.StartsWith(commandFirstWord + " ", StringComparison.Ordinal));
        }));
    }

}
