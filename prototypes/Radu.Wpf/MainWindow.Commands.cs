using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace Radu.Wpf;

public partial class MainWindow
{
    private VoiceCommand? _editingCommand;
    private VoiceCommand? _pendingDeleteCommand;
    private IReadOnlyList<VoiceCommand> _commands = [];

    private void AddCommand_Click(object sender, RoutedEventArgs e)
    {
        _editingCommand = null;
        EditorTitle.Text = "Новая команда";
        CommandNameInput.Clear();
        SetPhraseRows([""]);
        ActionTypeInput.SelectedIndex = 0;
        ActionValueInput.Clear();
        EditorMessage.Text = "";
        ShowEditor();
    }

    private void EditCommandCard_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not System.Windows.FrameworkElement element || element.Tag is not VoiceCommand command) return;
        _editingCommand = command;
        EditorTitle.Text = "Изменить команду";
        CommandNameInput.Text = command.Name ?? command.PrimaryPhrase;
        SetPhraseRows(command.Phrases);
        ActionTypeInput.SelectedIndex = command.Action.Type == "open_url" ? 0 : 1;
        ActionValueInput.Text = command.Action.Value;
        EditorMessage.Text = "";
        ShowEditor();
    }

    private void DeleteCommand_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not FrameworkElement element || element.Tag is not VoiceCommand command)
            return;

        _pendingDeleteCommand = command;
        DeleteConfirmationText.Text = $"Удалить только команду «{command.DisplayName}»? Распознанные фразы и записи в истории сохранятся.";
        DeleteConfirmationOverlay.Visibility = Visibility.Visible;
        Dispatcher.BeginInvoke(() => CancelDeleteButton.Focus());
    }

    private void DeleteCommand_MouseEnter(object sender, RoutedEventArgs e) => SetDeleteGlyphColor(sender, "DangerBrush");

    private void DeleteCommand_MouseLeave(object sender, RoutedEventArgs e) => SetDeleteGlyphColor(sender, "MutedBrush");

    private void SetDeleteGlyphColor(object sender, string resourceKey)
    {
        if (sender is System.Windows.Controls.Button { Content: System.Windows.Shapes.Path glyph })
            glyph.Stroke = (System.Windows.Media.Brush)FindResource(resourceKey);
    }

    private void CancelDeleteCommand_Click(object sender, RoutedEventArgs e)
    {
        _pendingDeleteCommand = null;
        DeleteConfirmationOverlay.Visibility = Visibility.Collapsed;
    }

    private void DeleteConfirmationOverlay_MouseDown(object sender, System.Windows.Input.MouseButtonEventArgs e)
    {
        if (ReferenceEquals(e.OriginalSource, DeleteConfirmationOverlay))
            CancelDeleteCommand_Click(sender, new RoutedEventArgs());
    }

    private void ConfirmDeleteCommand_Click(object sender, RoutedEventArgs e)
    {
        if (_pendingDeleteCommand is not { } command)
            return;

        _pendingDeleteCommand = null;
        DeleteConfirmationOverlay.Visibility = Visibility.Collapsed;

        try
        {
            CommandCatalog.Delete(command.Id);
            _commands = CommandCatalog.Load().Commands;
            ApplyCommandFilter();
            CommandList.SelectedItem = null;
            _engine.ReloadCommands();
            RestartNotice.Text = "Команда удалена. Её фразы больше не запускают действие.";
            RestartNotice.Visibility = Visibility.Visible;
        }
        catch (Exception error)
        {
            System.Windows.MessageBox.Show(
                this,
                $"Не удалось удалить команду. {error.Message}",
                "Ошибка удаления",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Error);
        }
    }

    private void MainWindow_PreviewKeyDown(object sender, System.Windows.Input.KeyEventArgs e)
    {
        if (e.Key == System.Windows.Input.Key.Escape && DeleteConfirmationOverlay.Visibility == Visibility.Visible)
        {
            CancelDeleteCommand_Click(sender, new RoutedEventArgs());
            e.Handled = true;
        }
    }

    private void CommandCard_Click(object sender, System.Windows.Input.MouseButtonEventArgs e)
    {
        if (sender is System.Windows.FrameworkElement element && element.Tag is VoiceCommand)
            EditCommandCard_Click(sender, new RoutedEventArgs());
    }


    private void CancelEditor_Click(object sender, RoutedEventArgs e)
    {
        ShowView(CommandsView);
    }

    private void SaveCommand_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var actionType = (ActionTypeInput.SelectedItem as System.Windows.Controls.ComboBoxItem)?.Tag?.ToString() ?? "open_url";
            var phrases = PhraseInputList.Children.OfType<FrameworkElement>()
                .Select(element => (element as Grid)?.Children.OfType<System.Windows.Controls.TextBox>().FirstOrDefault()?.Text?.Trim())
                .Where(text => !string.IsNullOrWhiteSpace(text)).Select(text => text!).ToArray();
            var id = _editingCommand?.Id ?? $"command_{Guid.NewGuid():N}";
            var command = new VoiceCommand(
                id,
                phrases,
                new VoiceAction(actionType, ActionValueInput.Text.Trim(), _editingCommand?.Action.ReuseExistingWindow ?? false),
                CommandNameInput.Text.Trim());
            CommandCatalog.Save(command, _editingCommand?.Id);
            _commands = CommandCatalog.Load().Commands;
            _engine.ReloadCommands();
            ApplyCommandFilter();
            CommandList.SelectedItem = null;
            RestartNotice.Visibility = Visibility.Visible;
            RestartNotice.Text = "Сохранено и добавлено в распознавание.";
            ShowView(CommandsView);
        }
        catch (Exception error)
        {
            EditorMessage.Text = error.Message;
            EditorMessage.Foreground = System.Windows.Media.Brushes.Orange;
        }
    }

    private void ActionType_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e)
    {
        if (ActionValueLabel is null || ActionHint is null || BrowseActionButton is null
            || ActionTypeInput.SelectedItem is not System.Windows.Controls.ComboBoxItem item) return;
        var opensSite = item.Tag?.ToString() == "open_url";
        ActionValueLabel.Text = opensSite ? "Адрес сайта" : "Приложение или файл для запуска";
        ActionHint.Text = "Укажи команду или путь к приложению. Можно выбрать файл через обзор.";
        ActionHint.Visibility = opensSite ? Visibility.Collapsed : Visibility.Visible;
        BrowseActionButton.Visibility = opensSite ? Visibility.Collapsed : Visibility.Visible;
    }

    private void BrowseAction_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new Microsoft.Win32.OpenFileDialog
        {
            Title = "Выбери приложение или файл для запуска",
            Filter = "Запускаемые файлы и ярлыки|*.exe;*.com;*.bat;*.cmd;*.lnk;*.url|Все файлы|*.*",
            CheckFileExists = true,
            Multiselect = false
        };

        if (dialog.ShowDialog(this) == true)
            ActionValueInput.Text = dialog.FileName;
    }


    private void UpdateCommands(int count)
    {
        Dispatcher.BeginInvoke(() =>
        {
            CommandsCountLabel.Text = $"{count}";
            _commands = CommandCatalog.Load().Commands;
            ApplyCommandFilter();
        });
    }

    private void CommandSearch_TextChanged(object sender, System.Windows.Controls.TextChangedEventArgs e)
    {
        if (CommandSearchPlaceholder is not null)
            CommandSearchPlaceholder.Visibility = string.IsNullOrEmpty(CommandSearch.Text) ? Visibility.Visible : Visibility.Collapsed;
        if (CommandSearchClearButton is not null)
            CommandSearchClearButton.Visibility = string.IsNullOrEmpty(CommandSearch.Text) ? Visibility.Collapsed : Visibility.Visible;
        ApplyCommandFilter();
    }

    private void ClearCommandSearch_Click(object sender, RoutedEventArgs e)
    {
        CommandSearch.Clear();
        CommandSearch.Focus();
    }

    private void ApplyCommandFilter()
    {
        if (CommandList is null) return;
        var query = CommandCatalog.Normalize(CommandSearch?.Text ?? "");
        var visible = _commands.Where(command => query.Length == 0
            || CommandCatalog.Normalize(command.DisplayName).Contains(query, StringComparison.Ordinal)
            || CommandCatalog.Normalize(command.ActionDescription).Contains(query, StringComparison.Ordinal)
            || command.Phrases.Any(phrase => CommandCatalog.Normalize(phrase).Contains(query, StringComparison.Ordinal)))
            .ToArray();
        CommandList.ItemsSource = visible;
        CommandsCountLabel.Text = query.Length == 0 ? $"{_commands.Count}" : $"{visible.Length} / {_commands.Count}";
        CommandScrollViewer.ScrollToTop();
    }

    private void AddPhrase_Click(object sender, RoutedEventArgs e) => AddPhraseRow("");

    private void SetPhraseRows(IEnumerable<string> phrases)
    {
        PhraseInputList.Children.Clear();
        foreach (var phrase in phrases) AddPhraseRow(phrase);
    }

    private void AddPhraseRow(string phrase)
    {
        var row = new Grid { Margin = new Thickness(0, 0, 0, 7) };
        row.ColumnDefinitions.Add(new ColumnDefinition());
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var input = new System.Windows.Controls.TextBox { Text = phrase, VerticalContentAlignment = VerticalAlignment.Center };
        row.Children.Add(input);
        var remove = new System.Windows.Controls.Button { Width = 42, Height = 42, Margin = new Thickness(8, 0, 0, 0), Padding = new Thickness(0), ToolTip = "Удалить фразу" };
        System.Windows.Automation.AutomationProperties.SetName(remove, "Удалить фразу");
        remove.Content = new System.Windows.Shapes.Path { Width = 17, Height = 17, Stretch = Stretch.Uniform, Stroke = (System.Windows.Media.Brush)FindResource("MutedBrush"), StrokeThickness = 1.6, Data = System.Windows.Media.Geometry.Parse("M5,5 L19,19 M19,5 L5,19") };
        remove.Click += (_, _) =>
        {
            if (PhraseInputList.Children.Count > 1) PhraseInputList.Children.Remove(row);
            else
            {
                input.Clear();
                input.Focus();
            }
        };
        Grid.SetColumn(remove, 1);
        row.Children.Add(remove);
        PhraseInputList.Children.Add(row);
    }

}
