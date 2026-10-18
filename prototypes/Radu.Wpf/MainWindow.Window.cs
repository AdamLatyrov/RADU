using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;

namespace Radu.Wpf;

public partial class MainWindow
{
    private const int WmGetMinMaxInfo = 0x0024;
    private const uint MonitorDefaultToNearest = 0x00000002;
    private bool _allowExit;

    private void OnSourceInitialized(object? sender, EventArgs e)
    {
        var source = HwndSource.FromHwnd(new WindowInteropHelper(this).Handle);
        source?.AddHook(WindowProc);
    }

    private IntPtr WindowProc(IntPtr hwnd, int message, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        if (message != WmGetMinMaxInfo)
            return IntPtr.Zero;

        SetMaximizedBoundsToWorkArea(hwnd, lParam);
        handled = true;
        return IntPtr.Zero;
    }

    private static void SetMaximizedBoundsToWorkArea(IntPtr hwnd, IntPtr minMaxInfoPointer)
    {
        var monitor = MonitorFromWindow(hwnd, MonitorDefaultToNearest);
        if (monitor == IntPtr.Zero)
            return;

        var monitorInfo = new NativeMonitorInfo { Size = Marshal.SizeOf<NativeMonitorInfo>() };
        if (!GetMonitorInfo(monitor, ref monitorInfo))
            return;

        var minMaxInfo = Marshal.PtrToStructure<NativeMinMaxInfo>(minMaxInfoPointer);
        minMaxInfo.MaxPosition = new NativePoint
        {
            X = monitorInfo.Work.Left - monitorInfo.Monitor.Left,
            Y = monitorInfo.Work.Top - monitorInfo.Monitor.Top
        };
        minMaxInfo.MaxSize = new NativePoint
        {
            X = monitorInfo.Work.Right - monitorInfo.Work.Left,
            Y = monitorInfo.Work.Bottom - monitorInfo.Work.Top
        };
        Marshal.StructureToPtr(minMaxInfo, minMaxInfoPointer, false);
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct NativePoint
    {
        public int X;
        public int Y;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct NativeRect
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct NativeMinMaxInfo
    {
        public NativePoint Reserved;
        public NativePoint MaxSize;
        public NativePoint MaxPosition;
        public NativePoint MinTrackSize;
        public NativePoint MaxTrackSize;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Auto)]
    private struct NativeMonitorInfo
    {
        public int Size;
        public NativeRect Monitor;
        public NativeRect Work;
        public uint Flags;
    }

    [DllImport("user32.dll")]
    private static extern IntPtr MonitorFromWindow(IntPtr hwnd, uint flags);

    [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetMonitorInfo(IntPtr monitor, ref NativeMonitorInfo monitorInfo);


    private void Minimize_Click(object sender, RoutedEventArgs e) => WindowState = WindowState.Minimized;
    private void Maximize_Click(object sender, RoutedEventArgs e) => WindowState = WindowState == WindowState.Maximized ? WindowState.Normal : WindowState.Maximized;
    private void Close_Click(object sender, RoutedEventArgs e) => Hide();

    private void Window_Closing(object? sender, System.ComponentModel.CancelEventArgs e)
    {
        if (_allowExit) return;
        e.Cancel = true;
        Hide();
    }

    public async Task ExitApplicationAsync()
    {
        _allowExit = true;
        DisposeHistoryWatcher();
        ListenButton.IsEnabled = false;
        VoiceStatus.Text = "Завершаю обработку и закрываю RADU…";
        await _engine.StopAsync();
        Close();
    }
}
