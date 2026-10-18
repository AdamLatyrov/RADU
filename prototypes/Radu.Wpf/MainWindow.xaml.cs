using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Animation;

namespace Radu.Wpf;

public partial class MainWindow : Window
{
    private VoiceEngine _engine = new();
    private bool _started;
    private bool _starting;

    public event Action<bool>? ListeningStateChanged;

    public bool IsListening => _engine.IsListening;

    public MainWindow()
    {
        InitializeComponent();
        ListenButton.Background = _microphoneSurfaceBrush;
        SourceInitialized += OnSourceInitialized;
        _listeningColor = LoadAccentColor();
        ApplyThemeAccent(_listeningColor);
        _accentBrush = new SolidColorBrush(_listeningColor);
        _navigationAccentBrush = new SolidColorBrush(_listeningColor);
        ListenButton.BorderBrush = _accentBrush;
        MicrophoneIcon.Stroke = _accentBrush;
        SetActiveNavigation(HomeNav);
        ListenButton.RenderTransformOrigin = new System.Windows.Point(0.5, 0.5);
        ListenButton.RenderTransform = _microphoneScale;
        BuildVoiceVisualizer();
        UpdateAccentPaletteSelection();
        _visualizerTimer.Tick += (_, _) => DrawVoiceVisualizer();
        IsVisibleChanged += (_, _) =>
        {
            if (IsVisible)
                _visualizerTimer.Start();
            else
                _visualizerTimer.Stop();
        };
        _feedbackTimer.Tick += (_, _) =>
        {
            _feedbackTimer.Stop();
            _feedbackExpiresAtUtc = DateTime.MinValue;
            SetVisualState(_engine.IsListening ? "listening" : "standby");
        };
        BindEngineEvents(_engine);
        Loaded += async (_, _) => await StartEngineAsync();
    }

    private void BindEngineEvents(VoiceEngine engine)
    {
        engine.StatusChanged += status =>
        {
            if (ReferenceEquals(engine, _engine)) UpdateStatus(status);
        };
        engine.DecisionMade += (transcript, detail) =>
        {
            if (ReferenceEquals(engine, _engine)) UpdateDecision(transcript, detail);
        };
        engine.ActionStarting += () => ReferenceEquals(engine, _engine)
            ? ShowActionStartingFeedback()
            : Task.CompletedTask;
        engine.CommandsLoaded += count =>
        {
            if (ReferenceEquals(engine, _engine)) UpdateCommands(count);
        };
        engine.AudioLevelChanged += level =>
        {
            if (ReferenceEquals(engine, _engine)) _audioLevel = level;
        };
        engine.EngineStopped += () => Dispatcher.BeginInvoke(() =>
        {
            if (!ReferenceEquals(engine, _engine)) return;
            _started = false;
            ListenButton.IsEnabled = true;
            VoiceStatus.Text = "Голосовой движок остановился. Нажмите микрофон, чтобы запустить снова.";
            ShowDecisionFeedback("rejected");
            ListeningStateChanged?.Invoke(false);
        });
    }

    private async Task StartEngineAsync()
    {
        if (_started || _starting) return;
        _starting = true;
        ListenButton.IsEnabled = false;
        VoiceStatus.Text = "Запускаю голосовой движок…";
        try
        {
            await _engine.StartAsync();
            _started = true;
            _feedbackExpiresAtUtc = DateTime.MinValue;
            SetVisualState("listening");
            VoiceStatus.Text = "Микрофон активен";
            ListeningStateChanged?.Invoke(_engine.IsListening);
        }
        catch (Exception exception)
        {
            ShowDecisionFeedback("rejected");
            VoiceStatus.Text = $"Не удалось запустить голосовой движок. {exception.Message}";
            _engine.Dispose();
            _engine = new VoiceEngine();
            BindEngineEvents(_engine);
        }
        finally
        {
            _starting = false;
            ListenButton.IsEnabled = true;
        }
    }

    public void ToggleListening()
    {
        if (!_started)
        {
            _ = StartEngineAsync();
            return;
        }

        try
        {
            if (!_engine.SetListening(!_engine.IsListening)) return;
        }
        catch (Exception exception) when (exception is IOException or InvalidOperationException)
        {
            VoiceStatus.Text = $"Не удалось изменить состояние микрофона. {exception.Message}";
            ShowDecisionFeedback("rejected");
            return;
        }
        var active = _engine.IsListening;
        _feedbackTimer.Stop();
        _feedbackExpiresAtUtc = DateTime.MinValue;
        SetVisualState(active ? "listening" : "standby");
        VoiceStatus.Text = active ? "Микрофон активен" : "Прослушивание приостановлено";
        ListeningStateChanged?.Invoke(active);
    }

    private void ListenButton_Click(object sender, RoutedEventArgs e) => ToggleListening();

    private void HomeButton_Click(object sender, RoutedEventArgs e)
    {
        ShowView(HomeView);
        SetActiveNavigation(HomeNav);
    }

    private void CommandsButton_Click(object sender, RoutedEventArgs e)
    {
        ShowView(CommandsView);
        SetActiveNavigation(CommandsNav);
    }

    private void SetActiveNavigation(System.Windows.Controls.Button active)
    {
        SetNavigationAppearance(HomeNav, ReferenceEquals(HomeNav, active));
        SetNavigationAppearance(CommandsNav, ReferenceEquals(CommandsNav, active));
        SetNavigationAppearance(HistoryNav, ReferenceEquals(HistoryNav, active));
    }

    private void SetNavigationAppearance(System.Windows.Controls.Button button, bool isActive)
    {
        if (button.Content is not StackPanel stack) return;
        var brush = isActive ? _navigationAccentBrush! : (System.Windows.Media.Brush)FindResource("MutedBrush");

        foreach (var item in stack.Children)
        {
            if (item is TextBlock label)
                label.Foreground = brush;
            else if (item is System.Windows.Shapes.Path icon)
                icon.Stroke = brush;
        }
    }

    private void ShowEditor()
    {
        CommandsView.Visibility = Visibility.Collapsed;
        ShowView(CommandEditorView);
    }

    private void ShowView(UIElement view)
    {
        foreach (var current in new UIElement[] { HomeView, CommandsView, CommandEditorView, HistoryView })
        {
            current.BeginAnimation(OpacityProperty, null);
            current.Opacity = 1;
            current.Visibility = Visibility.Collapsed;
        }
        view.Opacity = 0;
        view.Visibility = Visibility.Visible;
        view.BeginAnimation(OpacityProperty, new DoubleAnimation(0, 1, TimeSpan.FromMilliseconds(170))
        {
            EasingFunction = new QuadraticEase { EasingMode = EasingMode.EaseOut }
        });
    }
}
