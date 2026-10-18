using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows;
using System.Windows.Media.Imaging;
using Forms = System.Windows.Forms;

namespace Radu.Wpf;

public partial class App : System.Windows.Application
{
    private Forms.NotifyIcon? _tray;
    private MainWindow? _window;
    private Forms.ToolStripMenuItem? _listeningItem;
    private Mutex? _instanceMutex;
    private bool _ownsInstanceMutex;
    private bool _isExiting;

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern nint FindWindow(string? className, string windowName);

    [DllImport("user32.dll")]
    private static extern bool ShowWindow(nint handle, int command);

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(nint handle);

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        ShutdownMode = ShutdownMode.OnExplicitShutdown;
        _instanceMutex = new Mutex(true, @"Local\RADU-V3-WPF", out var createdNew);
        if (!createdNew)
        {
            var existingWindow = FindWindow(null, "RADU");
            if (existingWindow != nint.Zero)
            {
                ShowWindow(existingWindow, 9);
                SetForegroundWindow(existingWindow);
            }
            _instanceMutex.Dispose();
            _instanceMutex = null;
            Shutdown();
            return;
        }
        _ownsInstanceMutex = true;
        _window = new MainWindow();
        var iconPath = Path.Combine(AppContext.BaseDirectory, "radu.ico");
        if (File.Exists(iconPath))
            _window.Icon = BitmapFrame.Create(new Uri(iconPath), BitmapCreateOptions.None, BitmapCacheOption.OnLoad);
        _window.Show();

        var menu = new Forms.ContextMenuStrip();
        menu.Items.Add("Открыть RADU", null, (_, _) => ShowWindow());
        _listeningItem = new Forms.ToolStripMenuItem();
        _listeningItem.Click += (_, _) => _window?.ToggleListening();
        _window.ListeningStateChanged += listening => UpdateListeningItem(listening);
        UpdateListeningItem(_window.IsListening);
        menu.Items.Add(_listeningItem);
        menu.Items.Add(new Forms.ToolStripSeparator());
        menu.Items.Add("Выход", null, (_, _) => ExitApplication());
        _tray = new Forms.NotifyIcon
        {
            Icon = File.Exists(iconPath) ? new System.Drawing.Icon(iconPath) : System.Drawing.SystemIcons.Application,
            Text = "RADU — голосовые команды",
            ContextMenuStrip = menu,
            Visible = true
        };
        _tray.DoubleClick += (_, _) => ShowWindow();
    }

    private void UpdateListeningItem(bool listening)
    {
        if (_listeningItem is not null)
            _listeningItem.Text = listening
                ? "Приостановить прослушивание"
                : "Возобновить прослушивание";
    }

    private void ShowWindow()
    {
        if (_window is null) return;
        _window.Show();
        _window.Activate();
    }

    private async void ExitApplication()
    {
        if (_isExiting) return;
        _isExiting = true;
        try
        {
            if (_window is not null)
                await _window.ExitApplicationAsync();
        }
        finally
        {
            _tray?.Dispose();
            _tray = null;
            Shutdown();
        }
    }

    protected override void OnExit(ExitEventArgs e)
    {
        if (_ownsInstanceMutex)
            _instanceMutex?.ReleaseMutex();
        _instanceMutex?.Dispose();
        _instanceMutex = null;
        base.OnExit(e);
    }
}
