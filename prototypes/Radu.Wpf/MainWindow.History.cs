using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Diagnostics;
using System.Windows;
using System.Windows.Data;
using System.Windows.Threading;

namespace Radu.Wpf;

public partial class MainWindow
{
    private readonly ObservableCollection<SuccessfulExecution> _successfulExecutions = [];
    private ICollectionView? _successfulExecutionView;
    private System.Windows.Media.MediaPlayer? _audioPlayer;
    private SuccessfulExecution? _playingExecution;
    private bool _historyLoading;
    private bool _historyNeedsRefresh;
    private bool _markedOnly;
    private FileSystemWatcher? _historyWatcher;
    private DispatcherTimer? _historyReloadTimer;

    private async void HistoryButton_Click(object sender, RoutedEventArgs e)
    {
        ShowView(HistoryView);
        SetActiveNavigation(HistoryNav);
        EnsureHistoryWatcher();
        await RefreshHistoryAsync();
    }

    private async Task RefreshHistoryAsync()
    {
        if (_historyLoading)
        {
            _historyNeedsRefresh = true;
            return;
        }

        if (_playingExecution is not null)
        {
            _historyNeedsRefresh = true;
            return;
        }

        _historyLoading = true;
        try
        {
            var entries = await Task.Run(SuccessfulExecutionCatalog.Load);
            StopHistoryPlayback();
            _successfulExecutions.Clear();
            foreach (var entry in entries)
                _successfulExecutions.Add(entry);

            if (_successfulExecutionView is null)
            {
                _successfulExecutionView = CollectionViewSource.GetDefaultView(_successfulExecutions);
                _successfulExecutionView.Filter = MatchesHistorySearch;
                HistoryList.ItemsSource = _successfulExecutionView;
            }
            else
            {
                _successfulExecutionView.Refresh();
            }

            UpdateHistoryEmptyState();
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or DirectoryNotFoundException)
        {
            HistoryEmptyText.Text = exception.Message;
            HistoryEmptyText.Visibility = Visibility.Visible;
        }
        finally
        {
            _historyLoading = false;
            if (_historyNeedsRefresh && _playingExecution is null && _historyReloadTimer is not null)
            {
                _historyReloadTimer.Stop();
                _historyReloadTimer.Start();
            }
        }
    }

    private void EnsureHistoryWatcher()
    {
        if (_historyWatcher is not null)
            return;

        try
        {
            var path = SuccessfulExecutionCatalog.GetHistoryFilePath();
            var directory = Path.GetDirectoryName(path)!;
            Directory.CreateDirectory(directory);
            _historyReloadTimer = new DispatcherTimer(DispatcherPriority.Background, Dispatcher)
            {
                Interval = TimeSpan.FromMilliseconds(350)
            };
            _historyReloadTimer.Tick += HistoryReloadTimer_Tick;
            _historyWatcher = new FileSystemWatcher(directory, Path.GetFileName(path))
            {
                NotifyFilter = NotifyFilters.LastWrite | NotifyFilters.FileName | NotifyFilters.Size,
                EnableRaisingEvents = true
            };
            _historyWatcher.Changed += HistoryFile_Changed;
            _historyWatcher.Created += HistoryFile_Changed;
            _historyWatcher.Renamed += HistoryFile_Renamed;
            _historyWatcher.Error += HistoryWatcher_Error;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or ArgumentException)
        {
            _historyWatcher?.Dispose();
            _historyWatcher = null;
        }
    }

    private void HistoryFile_Changed(object sender, FileSystemEventArgs e) => ScheduleHistoryReload();

    private void HistoryFile_Renamed(object sender, RenamedEventArgs e) => ScheduleHistoryReload();

    private void HistoryWatcher_Error(object sender, ErrorEventArgs e) => ScheduleHistoryReload();

    private void ScheduleHistoryReload()
    {
        if (!Dispatcher.CheckAccess())
        {
            Dispatcher.BeginInvoke(ScheduleHistoryReload);
            return;
        }

        if (_historyReloadTimer is null)
            return;

        _historyReloadTimer.Stop();
        _historyReloadTimer.Start();
    }

    private async void HistoryReloadTimer_Tick(object? sender, EventArgs e)
    {
        _historyReloadTimer?.Stop();
        if (!HistoryView.IsVisible || _playingExecution is not null)
        {
            _historyNeedsRefresh = true;
            return;
        }

        _historyNeedsRefresh = false;
        await RefreshHistoryAsync();
    }

    private void RefreshHistoryIfPending()
    {
        if (!_historyNeedsRefresh)
            return;

        _historyNeedsRefresh = false;
        _ = RefreshHistoryAsync();
    }

    private void DisposeHistoryWatcher()
    {
        _historyReloadTimer?.Stop();
        _historyWatcher?.Dispose();
        _historyWatcher = null;
    }

    private bool MatchesHistorySearch(object item)
    {
        if (item is not SuccessfulExecution execution)
            return false;

        var query = CommandCatalog.Normalize(HistorySearch.Text);
        return (!_markedOnly || execution.IsMarkedIncorrect)
            && (query.Length == 0
            || CommandCatalog.Normalize(execution.Transcript).Contains(query, StringComparison.Ordinal)
            || CommandCatalog.Normalize(execution.CommandId).Contains(query, StringComparison.Ordinal));
    }

    private void HistorySearch_TextChanged(object sender, System.Windows.Controls.TextChangedEventArgs e)
    {
        if (HistorySearchPlaceholder is not null)
            HistorySearchPlaceholder.Visibility = string.IsNullOrEmpty(HistorySearch.Text) ? Visibility.Visible : Visibility.Collapsed;
        if (HistorySearchClearButton is not null)
            HistorySearchClearButton.Visibility = string.IsNullOrEmpty(HistorySearch.Text) ? Visibility.Collapsed : Visibility.Visible;

        _successfulExecutionView?.Refresh();
        UpdateHistoryEmptyState();
    }

    private void ClearHistorySearch_Click(object sender, RoutedEventArgs e)
    {
        HistorySearch.Clear();
        HistorySearch.Focus();
    }

    private void OpenHistoryFolder_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not FrameworkElement { DataContext: SuccessfulExecution execution })
            return;

        var audioPath = execution.AudioPath;
        var folder = !string.IsNullOrWhiteSpace(audioPath) && File.Exists(audioPath)
            ? Path.GetDirectoryName(audioPath)
            : execution.HistoryFolderPath;
        if (string.IsNullOrWhiteSpace(folder) || !Directory.Exists(folder))
            return;

        try
        {
            var startInfo = new ProcessStartInfo("explorer.exe") { UseShellExecute = true };
            if (!string.IsNullOrWhiteSpace(audioPath) && File.Exists(audioPath))
                startInfo.Arguments = $"/select,\"{audioPath}\"";
            else
                startInfo.Arguments = $"\"{folder}\"";

            Process.Start(startInfo);
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or InvalidOperationException or System.ComponentModel.Win32Exception)
        {
            System.Windows.MessageBox.Show(this, $"Не удалось открыть папку. {exception.Message}", "История запусков", MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }

    private void UpdateHistoryEmptyState()
    {
        if (HistoryEmptyText is null || _successfulExecutionView is null)
            return;

        var visibleCount = _successfulExecutionView.Cast<object>().Take(1).Count();
        HistoryEmptyText.Text = _successfulExecutions.Count == 0
            ? "Успешных запусков пока нет"
            : _markedOnly && !_successfulExecutions.Any(execution => execution.IsMarkedIncorrect)
                ? "Помеченных записей пока нет"
                : "По этому запросу ничего не найдено";
        HistoryEmptyText.Visibility = visibleCount == 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    private void MarkRecognitionIncorrect_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not FrameworkElement { DataContext: SuccessfulExecution execution })
            return;

        execution.IsMarkedIncorrect = !execution.IsMarkedIncorrect;
        try
        {
            RecognitionReviewStore.SaveMarkedIds(
                _successfulExecutions.Where(item => item.IsMarkedIncorrect).Select(item => item.ObservationId));
            _successfulExecutionView?.Refresh();
            UpdateHistoryEmptyState();
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            execution.IsMarkedIncorrect = !execution.IsMarkedIncorrect;
            System.Windows.MessageBox.Show(this, $"Не удалось сохранить пометку. {exception.Message}", "История запусков", MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }

    private void MarkedFilterButton_Click(object sender, RoutedEventArgs e)
    {
        _markedOnly = !_markedOnly;
        MarkedFilterButton.Content = _markedOnly ? "Только помеченные" : "Все записи";
        _successfulExecutionView?.Refresh();
        UpdateHistoryEmptyState();
    }

    private void PlayHistoryAudio_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not FrameworkElement { DataContext: SuccessfulExecution execution })
            return;

        if (ReferenceEquals(_playingExecution, execution))
        {
            StopHistoryPlayback();
            RefreshHistoryIfPending();
            return;
        }

        if (string.IsNullOrWhiteSpace(execution.AudioPath) || !File.Exists(execution.AudioPath))
        {
            System.Windows.MessageBox.Show(this, "Запись для этой команды не найдена.", "История запусков", MessageBoxButton.OK, MessageBoxImage.Information);
            return;
        }

        StopHistoryPlayback();
        var player = new System.Windows.Media.MediaPlayer();
        _audioPlayer = player;
        _playingExecution = execution;
        execution.IsPlaying = true;
        player.MediaOpened += (_, _) =>
        {
            if (ReferenceEquals(_audioPlayer, player))
                player.Play();
        };
        player.MediaEnded += (_, _) =>
        {
            if (!ReferenceEquals(_audioPlayer, player))
                return;

            StopHistoryPlayback();
            RefreshHistoryIfPending();
        };
        player.MediaFailed += (_, args) =>
        {
            if (!ReferenceEquals(_audioPlayer, player))
                return;

            StopHistoryPlayback();
            RefreshHistoryIfPending();
            System.Windows.MessageBox.Show(this, $"Не удалось воспроизвести запись. {args.ErrorException?.Message ?? "Проверьте WAV-файл."}", "История запусков", MessageBoxButton.OK, MessageBoxImage.Error);
        };
        try
        {
            player.Open(new Uri(execution.AudioPath, UriKind.Absolute));
        }
        catch (Exception exception) when (exception is IOException or InvalidOperationException or UriFormatException)
        {
            StopHistoryPlayback();
            RefreshHistoryIfPending();
            System.Windows.MessageBox.Show(this, $"Не удалось открыть запись. {exception.Message}", "История запусков", MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }

    private void StopHistoryPlayback()
    {
        if (_playingExecution is not null)
            _playingExecution.IsPlaying = false;

        _playingExecution = null;
        if (_audioPlayer is null)
            return;

        _audioPlayer.Stop();
        _audioPlayer.Close();
        _audioPlayer = null;
    }
}
