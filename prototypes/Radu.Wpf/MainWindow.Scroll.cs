using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Threading;

namespace Radu.Wpf;

public partial class MainWindow
{
    private readonly Dictionary<ScrollViewer, double> _scrollTargets = [];
    private DispatcherTimer? _smoothScrollTimer;

    private void SmoothScrollViewer_PreviewMouseWheel(object sender, MouseWheelEventArgs e)
    {
        if (sender is not ScrollViewer viewer || !QueueSmoothScroll(viewer, e.Delta))
            return;

        e.Handled = true;
    }

    private void HistoryList_PreviewMouseWheel(object sender, MouseWheelEventArgs e)
    {
        if (sender is not System.Windows.Controls.ListBox list || FindScrollViewer(list) is not { } viewer)
            return;

        if (QueueSmoothScroll(viewer, e.Delta))
            e.Handled = true;
    }

    private bool QueueSmoothScroll(ScrollViewer viewer, int wheelDelta)
    {
        if (viewer.ScrollableHeight <= 0)
            return false;

        if (!_scrollTargets.TryGetValue(viewer, out var target))
            target = viewer.VerticalOffset;

        _scrollTargets[viewer] = Math.Clamp(target - wheelDelta * 0.55, 0, viewer.ScrollableHeight);
        _smoothScrollTimer ??= CreateSmoothScrollTimer();
        if (!_smoothScrollTimer.IsEnabled)
            _smoothScrollTimer.Start();
        return true;
    }

    private static ScrollViewer? FindScrollViewer(DependencyObject parent)
    {
        for (var index = 0; index < System.Windows.Media.VisualTreeHelper.GetChildrenCount(parent); index++)
        {
            var child = System.Windows.Media.VisualTreeHelper.GetChild(parent, index);
            if (child is ScrollViewer viewer)
                return viewer;

            if (FindScrollViewer(child) is { } nested)
                return nested;
        }

        return null;
    }

    private DispatcherTimer CreateSmoothScrollTimer()
    {
        var timer = new DispatcherTimer(DispatcherPriority.Render, Dispatcher)
        {
            Interval = TimeSpan.FromMilliseconds(16)
        };
        timer.Tick += SmoothScroll_Tick;
        return timer;
    }

    private void SmoothScroll_Tick(object? sender, EventArgs e)
    {
        foreach (var pair in _scrollTargets.ToArray())
        {
            var viewer = pair.Key;
            if (!viewer.IsVisible || viewer.ScrollableHeight <= 0)
            {
                _scrollTargets.Remove(viewer);
                continue;
            }

            var target = Math.Clamp(pair.Value, 0, viewer.ScrollableHeight);
            var distance = target - viewer.VerticalOffset;
            if (Math.Abs(distance) < 0.8)
            {
                viewer.ScrollToVerticalOffset(target);
                _scrollTargets.Remove(viewer);
            }
            else
            {
                viewer.ScrollToVerticalOffset(viewer.VerticalOffset + distance * 0.28);
            }
        }

        if (_scrollTargets.Count == 0)
            _smoothScrollTimer?.Stop();
    }
}
