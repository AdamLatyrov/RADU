using System.Globalization;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Button = System.Windows.Controls.Button;
using Color = System.Windows.Media.Color;
using ColorConverter = System.Windows.Media.ColorConverter;
using Brushes = System.Windows.Media.Brushes;

namespace Radu.Wpf;

public partial class MainWindow
{
    private static string AccentColorPath
    {
        get
        {
            var directory = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "RADU-V3-WPF");
            Directory.CreateDirectory(directory);
            return Path.Combine(directory, "accent-color.txt");
        }
    }

    private static Color LoadAccentColor()
    {
        try
        {
            if (TryParseAccentColor(File.ReadAllText(AccentColorPath).Trim(), out var color))
                return color;
        }
        catch (IOException)
        {
        }
        catch (UnauthorizedAccessException)
        {
        }

        return ColorsForListening;
    }

    private void AccentColor_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button button || button.Tag is not string value) return;
        Color color;
        if (value == "custom")
        {
            using var dialog = new System.Windows.Forms.ColorDialog
            {
                AllowFullOpen = true,
                FullOpen = true,
                Color = System.Drawing.Color.FromArgb(
                    _listeningColor.R, _listeningColor.G, _listeningColor.B)
            };
            if (dialog.ShowDialog() != System.Windows.Forms.DialogResult.OK) return;
            color = Color.FromRgb(dialog.Color.R, dialog.Color.G, dialog.Color.B);
            value = $"#{color.R:X2}{color.G:X2}{color.B:X2}";
        }
        else if (!TryParseAccentColor(value, out color))
        {
            return;
        }

        _listeningColor = color;
        System.Windows.Application.Current.Resources["AccentBrush"] = new SolidColorBrush(color);
        System.Windows.Application.Current.Resources["AccentColor"] = color;
        if (_navigationAccentBrush is not null)
            _navigationAccentBrush.Color = color;
        if (_accentBrush is not null)
        {
            _accentBrush.BeginAnimation(SolidColorBrush.ColorProperty, null);
            _accentBrush.Color = color;
        }

        if (_glowBrush is not null)
        {
            var alphas = new byte[] { 125, 36, 0 };
            for (var index = 0; index < _glowBrush.GradientStops.Count; index++)
                _glowBrush.GradientStops[index].Color = Color.FromArgb(alphas[index], color.R, color.G, color.B);
        }

        try
        {
            File.WriteAllText(AccentColorPath, value);
        }
        catch (IOException)
        {
        }
        catch (UnauthorizedAccessException)
        {
        }

        UpdateAccentPaletteSelection();
        if (_visualState is "listening" or "standby")
            SetVisualState(_visualState);
    }

    private void UpdateAccentPaletteSelection()
    {
        if (AccentPalette is null) return;
        var presetColors = new[] { "#E8A33D", "#5EAFFF", "#A98BFF", "#42C7D5", "#F07E9A", "#FFFFFF" };
        var selectedPreset = presetColors.Any(preset =>
            TryParseAccentColor(preset, out var presetColor) && presetColor == _listeningColor);
        foreach (var button in AccentPalette.Children.OfType<Button>())
        {
            var isCustomButton = button.Tag is string value && value == "custom";
            if (isCustomButton)
                button.Background = new SolidColorBrush(_listeningColor);
            var selected = isCustomButton
                ? !selectedPreset
                : button.Tag is string preset
                    && TryParseAccentColor(preset, out var candidate)
                    && candidate == _listeningColor;
            button.BorderBrush = selected
                ? (_listeningColor == Colors.White
                    ? new SolidColorBrush(Color.FromRgb(232, 163, 61))
                    : Brushes.White)
                : new SolidColorBrush(Color.FromRgb(86, 97, 107));
            button.BorderThickness = new Thickness(selected ? 2 : 1);
        }
    }


    private static void ApplyThemeAccent(Color color)
    {
        var resources = System.Windows.Application.Current.Resources;
        resources["AccentBrush"] = new SolidColorBrush(color);
        resources["AccentHoverBrush"] = new SolidColorBrush(BlendAccent(color, Colors.White, 0.16));
        resources["AccentPressedBrush"] = new SolidColorBrush(BlendAccent(color, Colors.Black, 0.12));
        resources["AccentColor"] = color;
    }

    private static Color BlendAccent(Color color, Color target, double amount) => Color.FromRgb(
        (byte)(color.R + (target.R - color.R) * amount),
        (byte)(color.G + (target.G - color.G) * amount),
        (byte)(color.B + (target.B - color.B) * amount));

    private static bool TryParseAccentColor(string value, out Color color)
    {
        try
        {
            if (new ColorConverter().ConvertFromString(null, CultureInfo.InvariantCulture, value) is Color parsed)
            {
                color = parsed;
                return true;
            }
        }
        catch (FormatException)
        {
        }

        color = ColorsForListening;
        return false;
    }
}
